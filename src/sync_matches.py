#!/usr/bin/env python3
"""Daily sync: FootyStats fixtures/results -> footballsiam.com `matches` CPT.

For Thai League 1 (season_id 17367) and Thai League 2 (season_id 17896):

  * fetch every match from FootyStats
  * find the WP `matches` post whose `footystats_match_id` meta == that match id
  * no post at all
        -> create it (README field + club-ID mapping)
  * exists + FootyStats status == "complete" + WP home_score still empty
        -> write home_score, away_score, match_week
  * a post that already has a score recorded is NEVER re-scored

  Part A - full enrichment (enrich.py): whenever a completed match still has no
  raw_match_data, fetch /match detail and write team stats, home/away_xg and
  home/away_xg_prematch, and raw_match_data (player_stats[] with wp_id +
  position, match_events[] with p1_wp_id, own-goal detection). Missing players
  are auto-created (last_confirmed_club / last_confirmed_date, no current_club).
  A match that already has raw_match_data is skipped. Disable with --no-enrich.

  Part B - club reassignment (--club-sync): a separate pass over every `player`
  with fs_advanced_stats_json. Reads their most recent non-International club
  from all_seasons_metrics and, ID-based only, either points current_club at the
  right WP club or clears it when they have moved to a club we do not track.
  Runs weekly (weekly-club-sync.yml), not twice daily.

Safety: writes only happen with --live. Without it the run is a dry run
(reads still hit WP so the would-be payloads / changes are realistic).

    python src/sync_matches.py                       # dry run, match sync + enrich
    python src/sync_matches.py --live                # actually write
    python src/sync_matches.py --check               # credentials / connectivity
    python src/sync_matches.py --no-enrich --live    # score updates only
    python src/sync_matches.py --club-sync           # Part B, dry run (report)
    python src/sync_matches.py --club-sync --live    # Part B, write
    python src/sync_matches.py --enrich-test 25500,25739   # Part A guard test
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path
from typing import Any


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a local .env file (KEY=VALUE per line).

    Never overrides a variable that is already set, so exported shell vars
    and GitHub Actions secrets always win. No-op if the file is absent.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# repo root == parent of src/ ; load .env there before the clients read the env
_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from footystats_client import FootyStatsClient, FootyStatsError  # noqa: E402
from wp_client import WPClient, WPError  # noqa: E402
from enrich import (  # noqa: E402
    build_player_lookup,
    enrich_match,
    reassign_clubs,
)

# --------------------------------------------------------------------------
# Config - mirrors README.md "What was imported" + "Club ID mapping"
# --------------------------------------------------------------------------

BANGKOK_TZ = dt.timezone(dt.timedelta(hours=7))   # Asia/Bangkok, no DST
SEASON_LABEL = "2026/2027"
SEASON_TERM_ID = 172                              # `season` taxonomy term "2026"

# FootyStats team id -> WordPress `club` post id
CLUB_MAP_TL1: dict[int, int] = {
    1180: 7931, 1182: 7928, 1184: 7930, 678763: 7942, 1171: 7933, 1172: 7935,
    1173: 7929, 1177: 7937, 1186: 7934, 7038: 7938, 8374: 7939, 9887: 7941,
    9898: 7940, 666944: 7953, 678796: 7961, 688240: 7959,
}
CLUB_MAP_TL2: dict[int, int] = {
    1187: 7944, 1189: 7945, 21948: 7951, 9886: 7949, 688585: 7943, 8372: 7948,
    8384: 7956, 678797: 7960, 666943: 13879, 1174: 7936, 8381: 7952, 1188: 7932,
    8371: 7947, 688234: 7958, 9889: 7954, 1023811: 9862, 21927: 7955, 666933: 9792,
}

# every FootyStats club id we can resolve to a WP `club` post (TL1 + TL2).
# Used by Part B (club reassignment) as the set of "clubs we track".
CLUB_MAP: dict[int, int] = {**CLUB_MAP_TL1, **CLUB_MAP_TL2}

LEAGUES: list[dict[str, Any]] = [
    {
        "name": "Thai League 1",
        "season_id": 17367,
        "club_map": CLUB_MAP_TL1,
        # taxonomy REST field -> term ids (built-in category taxonomy is "categories")
        "taxonomies": {"competition": [116], "categories": [204], "season": [SEASON_TERM_ID]},
    },
    {
        "name": "Thai League 2",
        "season_id": 17896,
        "club_map": CLUB_MAP_TL2,
        "taxonomies": {"competition": [117], "categories": [209], "season": [SEASON_TERM_ID]},
    },
]

log = logging.getLogger("sync_matches")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def is_empty_score(value: Any) -> bool:
    """True when no score has been recorded. '0' is a real score -> False."""
    if value is None:
        return True
    return str(value).strip() == ""


def match_week_of(match: dict[str, Any]) -> int:
    revised = match.get("revised_game_week", -1)
    try:
        revised = int(revised)
    except (TypeError, ValueError):
        revised = -1
    if revised != -1:
        return revised
    try:
        return int(match.get("game_week", 0) or 0)
    except (TypeError, ValueError):
        return 0


def bangkok_date_time(unix_ts: int) -> tuple[str, str]:
    """(YYYYMMDD, H:i) for a FootyStats date_unix, in Asia/Bangkok."""
    d = dt.datetime.fromtimestamp(int(unix_ts), BANGKOK_TZ)
    return d.strftime("%Y%m%d"), d.strftime("%H:%M")


def build_create_payload(
    match: dict[str, Any], league: dict[str, Any], wp: WPClient
) -> dict[str, Any]:
    home_wp = league["club_map"][match["homeID"]]
    away_wp = league["club_map"][match["awayID"]]
    ymd, kickoff = bangkok_date_time(match["date_unix"])
    home_title = wp.get_club_title(home_wp) or str(match.get("home_name", "")).strip()
    away_title = wp.get_club_title(away_wp) or str(match.get("away_name", "")).strip()

    # All meta is registered as `string` on the site (see README) and the existing
    # data is stored as strings, so every value goes out as a string.
    payload: dict[str, Any] = {
        "title": f"{home_title} vs {away_title}",
        "status": "publish",
        "meta": {
            "home_team": str(home_wp),
            "away_team": str(away_wp),
            "home_score": "",
            "away_score": "",
            "date": ymd,                       # YYYYMMDD - required by fs_get_matches()
            "kick_off_time": kickoff,          # H:i, 24h, Asia/Bangkok
            "match_week": str(match_week_of(match)),
            "season": SEASON_LABEL,
            "footystats_match_id": str(int(match["id"])),
        },
    }
    payload.update(league["taxonomies"])       # competition / categories / season
    # the built-in category taxonomy's REST field is "categories" on most setups
    # but "category" on some; send both, WP ignores whichever isn't registered.
    if "categories" in league["taxonomies"]:
        payload["category"] = league["taxonomies"]["categories"]
    return payload


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

def _audit(msg: str) -> None:
    """One audit line, prefixed so it stands out in an unattended CI log."""
    log.info("AUDIT | %s", msg)


def sync(
    wp: WPClient,
    fs: FootyStatsClient,
    *,
    league_filter: str | None = None,
    limit: int | None = None,
    enrich: bool = True,
) -> dict[str, int]:
    stats = {
        "checked": 0,
        "scores_updated": 0,
        "created": 0,
        "skipped_recorded": 0,
        "skipped_incomplete": 0,
        "unmapped_team": 0,
        "enriched": 0,
        "enrich_skipped": 0,
        "players_created": 0,
        "errors": 0,
    }

    log.info("Indexing existing `matches` posts by footystats_match_id ...")
    index = wp.index_matches_by_footystats_id()
    log.info("  %d existing matches indexed", len(index))

    # Guard: if the CPT is non-empty but nothing had a readable footystats_match_id,
    # the meta is not exposed to REST. Abort before creating hundreds of duplicates.
    if not index:
        if next(wp.iter_all_matches(), None) is not None:
            raise WPError(
                "Indexed 0 matches by footystats_match_id, but the `matches` CPT is "
                "not empty. The footystats_match_id meta is almost certainly not "
                "exposed to REST (show_in_rest). Aborting before creating duplicates. "
                "See README > 'WordPress REST prerequisites'."
            )
        log.warning("  `matches` CPT appears empty - every match will be created")

    # Player lookup for Part A is built lazily and ONCE (fresh) the first time a
    # match actually needs enriching, then reused (and grown) for the rest of the
    # run. Never persisted between runs.
    player_lookup: dict[str, Any] | None = None

    for league in LEAGUES:
        if league_filter and league["name"] != league_filter:
            continue
        log.info("=== %s (season_id %s) ===", league["name"], league["season_id"])
        try:
            matches = fs.get_league_matches(league["season_id"])
        except FootyStatsError as exc:
            log.error("  FootyStats fetch failed: %s", exc)
            stats["errors"] += 1
            continue
        log.info("  %d matches from FootyStats", len(matches))

        if limit:
            matches = matches[:limit]

        for match in matches:
            stats["checked"] += 1
            fs_id = int(match["id"])
            home_id, away_id = match.get("homeID"), match.get("awayID")

            if home_id not in league["club_map"] or away_id not in league["club_map"]:
                stats["unmapped_team"] += 1
                log.warning(
                    "  UNMAPPED team in match %s: homeID=%s awayID=%s (%s vs %s) - skipped, not guessing",
                    fs_id, home_id, away_id,
                    match.get("home_name"), match.get("away_name"),
                )
                continue

            existing = index.get(fs_id)
            status = str(match.get("status", "")).lower()

            try:
                if existing is None:
                    payload = build_create_payload(match, league, wp)
                    wp.create_match(payload)
                    stats["created"] += 1
                    log.info("  CREATE  %s  %s  [wk %s]",
                             fs_id, payload["title"], payload["meta"]["match_week"])
                    continue

                meta = existing.get("meta") or {}

                # -- score update (unchanged trigger) ------------------
                if is_empty_score(meta.get("home_score")):
                    if status == "complete":
                        new_meta = {
                            "home_score": str(int(match.get("homeGoalCount", 0) or 0)),
                            "away_score": str(int(match.get("awayGoalCount", 0) or 0)),
                            "match_week": str(match_week_of(match)),
                        }
                        wp.update_match_meta(existing["id"], new_meta)
                        stats["scores_updated"] += 1
                        _audit(
                            f"SCORE  post {existing['id']}  fs {fs_id}  "
                            f"{new_meta['home_score']}-{new_meta['away_score']}  "
                            f"[wk {new_meta['match_week']}]  ({match.get('home_name')} "
                            f"v {match.get('away_name')})"
                        )
                    else:
                        stats["skipped_incomplete"] += 1
                else:
                    stats["skipped_recorded"] += 1

                # -- full enrichment (Part A) ----------------------
                # Independent of the score trigger: fires whenever a completed
                # match still has no raw_match_data (fresh result, or an older
                # post from before Part A existed). Idempotent - the guard in
                # enrich_match() also re-checks raw_match_data.
                if (
                    enrich
                    and status == "complete"
                    and not str(meta.get("raw_match_data") or "").strip()
                ):
                    if player_lookup is None:
                        log.info("  building fresh player lookup ...")
                        player_lookup = build_player_lookup(wp)
                        log.info("  %d players in lookup", len(player_lookup))
                    before = len(player_lookup)
                    result = enrich_match(
                        wp, fs, match, existing, league["club_map"],
                        player_lookup, audit=_audit,
                    )
                    stats["players_created"] += len(player_lookup) - before
                    if result == "enriched":
                        stats["enriched"] += 1
                    elif result == "skipped_has_data":
                        stats["enrich_skipped"] += 1
                    else:
                        stats["errors"] += 1
            except (WPError, KeyError) as exc:
                stats["errors"] += 1
                log.error("  ERROR on match %s: %s", fs_id, exc)

    return stats


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _run_club_sync(wp: WPClient, *, sample_limit: int = 10) -> int:
    """Part B: full pass over players with fs_advanced_stats_json."""
    mode = "LIVE" if not wp.dry_run else "DRY RUN (report only, no writes)"
    log.info("Starting club reassignment (Part B) - %s", mode)
    try:
        st = reassign_clubs(wp, CLUB_MAP, audit=_audit, sample_limit=sample_limit)
    except WPError as exc:
        log.error("Club sync aborted: %s", exc)
        return 2

    log.info("---------------------------------------------")
    log.info("Club reassignment summary (%s)", mode)
    log.info("  players scanned              : %d", st["scanned"])
    log.info("  eligible (has fs_advanced..) : %d", st["eligible"])
    log.info("  would reassign / reassigned  : %d", st["reassigned"])
    log.info("  would clear / cleared        : %d", st["cleared"])
    log.info("  no action                    : %d", st["noop"])
    log.info("  errors                       : %d", st["errors"])
    if st["reassign_sample"]:
        log.info("  --- reassign sample ---")
        for s in st["reassign_sample"]:
            log.info("    %s", s)
    if st["clear_sample"]:
        log.info("  --- clear sample ---")
        for s in st["clear_sample"]:
            log.info("    %s", s)
    if wp.dry_run and st["noop_blank_sample"]:
        log.info("  --- already-blank / untracked sample ---")
        for s in st["noop_blank_sample"]:
            log.info("    %s", s)
    log.info("---------------------------------------------")
    return 1 if st["errors"] else 0


def _run_enrich_test(wp: WPClient, fs: FootyStatsClient, post_ids: list[int]) -> int:
    """Part C test: run the Part A enrich path (dry run, forced) against
    specific WP `matches` posts. Already-enriched posts hit the guard and skip.
    """
    wp.dry_run = True
    log.info("Enrich guard test (Part A) on posts: %s", post_ids)
    lookup: dict[str, Any] | None = None
    for pid in post_ids:
        try:
            post = wp.get_match(
                pid, fields="id,title,meta.footystats_match_id,meta.raw_match_data,"
                            "meta.home_team,meta.away_team"
            )
        except WPError as exc:
            log.error("  post %s: fetch failed: %s", pid, exc)
            continue
        meta = post.get("meta") or {}
        fs_id = meta.get("footystats_match_id")
        title = (post.get("title") or {}).get("rendered", "")
        log.info("  post %s  %s  fs=%s  raw_match_data=%s",
                 pid, title, fs_id, "SET" if str(meta.get("raw_match_data") or "").strip() else "empty")
        if not fs_id:
            log.warning("    no footystats_match_id - cannot test")
            continue
        if lookup is None:
            lookup = build_player_lookup(wp)
        fake_fs_match = {"id": int(fs_id), "homeID": 0, "awayID": 0}
        result = enrich_match(
            wp, fs, fake_fs_match, {"id": pid, "meta": meta},
            CLUB_MAP, lookup, audit=_audit,
        )
        log.info("    -> %s", result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="actually write to WordPress (default: dry run)")
    parser.add_argument("--check", action="store_true",
                        help="only verify FootyStats + WordPress connectivity, then exit")
    parser.add_argument("--league", choices=[lg["name"] for lg in LEAGUES],
                        help="restrict to a single league")
    parser.add_argument("--limit", type=int, default=None,
                        help="only process the first N matches per league (testing)")
    parser.add_argument("--no-enrich", action="store_true",
                        help="skip Part A match enrichment (score updates only)")
    parser.add_argument("--club-sync", action="store_true",
                        help="run Part B (club reassignment) instead of the match sync")
    parser.add_argument("--enrich-test", metavar="POST_IDS",
                        help="Part C test: run the enrich path (dry) against "
                             "these comma-separated `matches` post ids and exit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        wp = WPClient(dry_run=not args.live)
        fs = FootyStatsClient()
    except (FootyStatsError, WPError) as exc:
        log.error("Startup failed: %s", exc)
        return 2

    if args.check:
        try:
            me = wp.whoami()
            log.info("WordPress auth OK - user: %s (%s)", me.get("name"), me.get("slug"))
            sample = fs.get_league_matches(LEAGUES[0]["season_id"])
            log.info("FootyStats OK - %s returned %d matches",
                     LEAGUES[0]["name"], len(sample))
            return 0
        except (WPError, FootyStatsError) as exc:
            log.error("Check failed: %s", exc)
            return 1

    if args.enrich_test:
        ids = [int(x) for x in args.enrich_test.replace(" ", "").split(",") if x]
        return _run_enrich_test(wp, fs, ids)

    if args.club_sync:
        return _run_club_sync(wp)

    mode = "LIVE" if args.live else "DRY RUN (no writes)"
    log.info("Starting match sync - %s", mode)
    try:
        stats = sync(
            wp, fs,
            league_filter=args.league, limit=args.limit,
            enrich=not args.no_enrich,
        )
    except (WPError, FootyStatsError) as exc:
        log.error("Sync aborted: %s", exc)
        return 2

    log.info("---------------------------------------------")
    log.info("Summary (%s)", mode)
    log.info("  matches checked        : %d", stats["checked"])
    log.info("  scores updated         : %d", stats["scores_updated"])
    log.info("  new posts created      : %d", stats["created"])
    log.info("  matches enriched       : %d", stats["enriched"])
    log.info("  enrich skipped (had it): %d", stats["enrich_skipped"])
    log.info("  players auto-created   : %d", stats["players_created"])
    log.info("  skipped (has score)    : %d", stats["skipped_recorded"])
    log.info("  skipped (not complete) : %d", stats["skipped_incomplete"])
    log.info("  unmapped team (skip)   : %d", stats["unmapped_team"])
    log.info("  errors                 : %d", stats["errors"])
    log.info("---------------------------------------------")

    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
