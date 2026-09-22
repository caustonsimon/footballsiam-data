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
from wp_client import WPClient, WPError, WPHostChallengeError  # noqa: E402

# exit codes: 0 ok, 1 finished with per-item errors, 2 sync aborted (real
# failure), 3 host anti-bot challenge (WP REST unreachable from this IP - not
# our failure; retry later / allowlist the caller).
EXIT_HOST_CHALLENGE = 3
from enrich import (  # noqa: E402
    build_player_lookup,
    enrich_match,
    populate_player_stats,
    reassign_clubs,
    reenrich_raw_match_data,
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
        "require_full_club_map": True,
    },
    {
        "name": "Thai League 2",
        "season_id": 17896,
        "club_map": CLUB_MAP_TL2,
        "taxonomies": {"competition": [117], "categories": [209], "season": [SEASON_TERM_ID]},
        "require_full_club_map": True,
    },
    # -- Added 2026-09-15: fixtures/results only, no dedicated club or player
    # pages for these. club_map is the full merged CLUB_MAP (not a per-league
    # slice) so a Thai club already tracked in T1/T2 (e.g. a side playing in
    # the AFC competitions) still resolves to its real club page; everyone
    # else (every T3 club, every foreign AFC opponent) falls back to a
    # plain-text name in build_create_payload() instead of a KeyError.
    {
        "name": "AFC Champions League Elite",
        "season_id": 17381,          # FootyStats: "Asia AFC Champions League"
        "club_map": CLUB_MAP,
        "taxonomies": {"competition": [120], "categories": [173], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "AFC Champions League Two",
        "season_id": 17205,          # FootyStats: "Asia AFC Cup"
        "club_map": CLUB_MAP,
        "taxonomies": {"competition": [121], "categories": [177], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "Thai League 3 North",
        "season_id": 17840,
        "club_map": CLUB_MAP,
        "taxonomies": {"competition": [127], "categories": [128], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "Thai League 3 North East",
        "season_id": 17883,
        "club_map": CLUB_MAP,
        # NB: "North East" has a duplicate competition term in WP (137 and
        # 154). The live fixtures filter dropdown uses slug t3_north_east,
        # which is term 137 -- 154 (slug t3_northeast, no underscore) looks
        # orphaned/unused. Using 137.
        "taxonomies": {"competition": [137], "categories": [138], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "Thai League 3 South",
        "season_id": 17884,
        "club_map": CLUB_MAP,
        "taxonomies": {"competition": [131], "categories": [132], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "Thai League 3 East",
        "season_id": 17885,
        "club_map": CLUB_MAP,
        "taxonomies": {"competition": [129], "categories": [130], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "Thai League 3 West",
        "season_id": 17886,
        "club_map": CLUB_MAP,
        "taxonomies": {"competition": [139], "categories": [140], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    # Thai League 3 Central deliberately omitted -- FootyStats has no
    # current-season data for it (confirmed 2026-09-15). WP taxonomy terms
    # already exist (competition 133, category 134) for whenever it's ready.
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
    # .get(), not [] -- for a require_full_club_map=False league (AFC CL,
    # T3) being unmapped is the expected case, not an error. sync() already
    # guarantees both sides ARE mapped for require_full_club_map=True
    # leagues before this is ever called, so .get() is safe there too.
    home_wp = league["club_map"].get(match["homeID"])
    away_wp = league["club_map"].get(match["awayID"])
    ymd, kickoff = bangkok_date_time(match["date_unix"])
    home_title = (
        (wp.get_club_title(home_wp) if home_wp else None)
        or str(match.get("home_name", "")).strip()
    )
    away_title = (
        (wp.get_club_title(away_wp) if away_wp else None)
        or str(match.get("away_name", "")).strip()
    )

    # All meta is registered as `string` on the site (see README) and the existing
    # data is stored as strings, so every value goes out as a string.
    # v3 fix (2026-09-15): a match being CREATED for the first time can
    # already be finished (status == "complete") -- this happens whenever a
    # whole season's fixture list is bulk-imported partway through the
    # season, exactly what the AFC CL / T3 rollout did. The existing-match
    # UPDATE path a few dozen lines down already backfills a real score only
    # when status == "complete"; creation needs the identical check, or
    # every already-played match goes in blank and sits in Fixtures forever
    # until some later sync happens to touch it via the update path.
    status = str(match.get("status", "")).lower()
    if status == "complete":
        home_score = str(int(match.get("homeGoalCount", 0) or 0))
        away_score = str(int(match.get("awayGoalCount", 0) or 0))
    else:
        home_score = ""
        away_score = ""

    payload: dict[str, Any] = {
        "title": f"{home_title} vs {away_title}",
        "status": "publish",
        "meta": {
            "home_team": str(home_wp) if home_wp else "",
            "away_team": str(away_wp) if away_wp else "",
            # Plain-text fallback for a side with no WP club post (every T3
            # club, every foreign AFC opponent). NOTE: these two meta keys
            # are brand new -- they need registering for REST on the
            # `matches` CPT (register_post_meta or a new ACF field) before
            # they'll actually persist, and the front-end fixtures/results
            # display needs a small patch to read them when home_team/
            # away_team come back empty. Until both of those land, these
            # matches will still be created correctly here, just render
            # with a blank team name on the site.
            "home_team_name": home_title,
            "away_team_name": away_title,
            "home_score": home_score,
            "away_score": away_score,
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


def _check_and_grade_predictor(wp: WPClient, mws: set[int]) -> None:
    """Auto-grade the Predictor Game for any Thai League 1 matchweek that just
    became fully complete during this sync run (added 2026-09-17).

    Previously grading needed a manual wp-admin button click
    (fs_render_predictor_hub) after every matchweek finished - if nobody
    remembered to click it, the leaderboard silently looked dead. This reuses
    the same "is this matchweek actually finished" check already built for the
    social-media carousel (fs/v1/matchweek-status, Snippet 133) and calls the
    new fs/v1/grade-predictions endpoint (Snippet: "FS Predictor Auto-Grading
    Engine v1") the moment a matchweek both (a) had a score written in THIS
    run and (b) is now fully complete.

    Grading itself is idempotent on the WP side (only touches predictions
    still marked "pending"), so it is safe to check every run a matchweek's
    score changed, not only the first time it completes - an already-graded
    matchweek is just a fast no-op on the WP side.

    Only Thai League 1 is checked - the Predictor Game does not cover Thai
    League 2 or the other competitions.
    """
    for mw in sorted(mws):
        try:
            status = wp.get_matchweek_status(PREDICTOR_LEAGUE, SEASON_LABEL, mw)
        except WPError as exc:
            log.warning("  predictor: could not check matchweek-status for mw %s: %s", mw, exc)
            continue

        if not status.get("complete"):
            log.info(
                "  predictor: mw %s not fully complete yet (%s/%s played) - skipping auto-grade",
                mw, status.get("played"), status.get("total"),
            )
            continue

        try:
            result = wp.grade_predictor_matchweek(mw)
        except WPError as exc:
            log.warning("  predictor: grading call failed for mw %s: %s", mw, exc)
            continue

        if result.get("graded_count"):
            _audit(
                f"PREDICTOR GRADED  mw {mw}  "
                f"graded={result.get('graded_count', 0)}  "
                f"points_awarded={result.get('total_points_awarded', 0)}"
            )
        else:
            log.info(
                "  predictor: mw %s complete, nothing to grade (%s)",
                mw, result.get("message", "no pending predictions"),
            )


PREDICTOR_LEAGUE = "thai-league-1"


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
        "text_only_team": 0,
        "enriched": 0,
        "enrich_skipped": 0,
        "players_created": 0,
        "errors": 0,
    }

    # Thai League 1 matchweeks that got a score written THIS run - checked
    # for predictor auto-grading once the whole sync finishes (added
    # 2026-09-17, see _check_and_grade_predictor()).
    t1_updated_mws: set[int] = set()

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

            if league.get("require_full_club_map", True):
                if home_id not in league["club_map"] or away_id not in league["club_map"]:
                    stats["unmapped_team"] += 1
                    log.warning(
                        "  UNMAPPED team in match %s: homeID=%s awayID=%s (%s vs %s) - skipped, not guessing",
                        fs_id, home_id, away_id,
                        match.get("home_name"), match.get("away_name"),
                    )
                    continue
            else:
                # Being unmapped is expected here (T3 / foreign AFC clubs) --
                # build_create_payload() falls back to a plain-text name for
                # whichever side has no WP club post. Just count it so the
                # summary shows how many came through text-only.
                if home_id not in league["club_map"] or away_id not in league["club_map"]:
                    stats["text_only_team"] += 1

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
                        if league["name"] == "Thai League 1":
                            t1_updated_mws.add(int(new_meta["match_week"]))
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
            except WPHostChallengeError:
                raise  # a host challenge is not a per-match error - abort the run
            except (WPError, KeyError) as exc:
                stats["errors"] += 1
                log.error("  ERROR on match %s: %s", fs_id, exc)

    if t1_updated_mws and not wp.dry_run:
        _check_and_grade_predictor(wp, t1_updated_mws)

    return stats


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _run_club_sync(
    wp: WPClient, fs: FootyStatsClient, *, sample_limit: int = 10
) -> int:
    """Part B: live /player-stats pass over every player."""
    mode = "LIVE" if not wp.dry_run else "DRY RUN (report only, no writes)"
    log.info("Starting club reassignment (Part B) - %s", mode)
    try:
        st = reassign_clubs(wp, fs, CLUB_MAP, audit=_audit, sample_limit=sample_limit)
    except WPHostChallengeError as exc:
        log.error("=== HOST CHALLENGE - club sync did NOT run (not a failure) ===")
        log.error("  %s", exc)
        return EXIT_HOST_CHALLENGE
    except (WPError, FootyStatsError) as exc:
        log.error("Club sync aborted: %s", exc)
        return 2

    log.info("---------------------------------------------")
    log.info("Club reassignment summary (%s)", mode)
    log.info("  players scanned              : %d", st["scanned"])
    log.info("  skipped (no footystats_id)   : %d", st["no_footystats_id"])
    log.info("  skipped (no fs profile)      : %d", st["skipped_no_profile"])
    log.info("  would reassign / reassigned  : %d", st["reassigned"])
    log.info("  would clear / cleared        : %d", st["cleared"])
    log.info("  no action (already correct)  : %d", st["noop"])
    log.info("  insufficient evidence        : %d", st["insufficient"])
    log.info("  thin reassign skipped (<apps): %d", st["thin_reassign_skipped"])
    log.info("  bad FS record (manual review): %d", st["bad_fs_record"])
    log.info("  errors                       : %d", st["errors"])
    log.info("  verify failures (acf write)  : %d", st["verify_failures"])
    for label, key in (
        ("reassign sample", "reassign_sample"),
        ("clear sample", "clear_sample"),
        ("thin-evidence (skipped) sample", "thin_sample"),
        ("bad-FS-record sample", "bad_sample"),
        ("no-action / untracked sample", "noop_untracked_sample"),
        ("insufficient-evidence sample", "insufficient_sample"),
    ):
        if st.get(key):
            log.info("  --- %s ---", label)
            for s in st[key]:
                log.info("    %s", s)
    log.info("---------------------------------------------")

    dump = os.environ.get("CLUB_SYNC_DECISION_DUMP")
    if dump and st.get("decisions"):
        import json as _json
        with open(dump, "w", encoding="utf-8") as fh:
            for d in st["decisions"]:
                fh.write(_json.dumps(d, ensure_ascii=False) + "\n")
        log.info("  decisions written to %s (%d rows)", dump, len(st["decisions"]))
    return 1 if (st["errors"] or st["verify_failures"]) else 0


def _run_player_stats_sync(
    wp: WPClient, fs: FootyStatsClient, *, sample_limit: int = 10
) -> int:
    """Fills fs_advanced_stats_json (full /player-stats row list) for every
    player with a footystats_id. Independent from --club-sync - same data
    source, different field, no reassignment logic."""
    mode = "LIVE" if not wp.dry_run else "DRY RUN (report only, no writes)"
    log.info("Starting player stats population - %s", mode)
    try:
        st = populate_player_stats(wp, fs, audit=_audit, sample_limit=sample_limit)
    except WPHostChallengeError as exc:
        log.error("=== HOST CHALLENGE - player stats sync did NOT run (not a failure) ===")
        log.error("  %s", exc)
        return EXIT_HOST_CHALLENGE
    except (WPError, FootyStatsError) as exc:
        log.error("Player stats sync aborted: %s", exc)
        return 2

    log.info("---------------------------------------------")
    log.info("Player stats population summary (%s)", mode)
    log.info("  players scanned              : %d", st["scanned"])
    log.info("  skipped (no footystats_id)   : %d", st["no_footystats_id"])
    log.info("  updated / would update       : %d", st["updated"])
    log.info("  no rows returned             : %d", st["no_rows"])
    log.info("  bad FS record (manual review): %d", st["bad_fs_record"])
    log.info("  errors                       : %d", st["errors"])
    if st.get("update_sample"):
        log.info("  --- sample ---")
        for s in st["update_sample"]:
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


def _run_reenrich(wp: WPClient, fs: FootyStatsClient, post_ids: list[int]) -> int:
    """Rebuild raw_match_data on already-enriched posts after a logic change."""
    mode = "LIVE" if not wp.dry_run else "DRY RUN (no writes)"
    log.info("Re-enrich raw_match_data on %d posts - %s", len(post_ids), mode)
    try:
        st = reenrich_raw_match_data(wp, fs, post_ids, audit=_audit)
    except (WPHostChallengeError, WPError, FootyStatsError) as exc:
        log.error("Re-enrich aborted: %s", exc)
        return 2
    log.info("---------------------------------------------")
    log.info("Re-enrich summary (%s)", mode)
    log.info("  posts                : %d", st["posts"])
    log.info("  would change / changed: %d", st["changed"])
    log.info("  unchanged (no-op)    : %d", st["unchanged"])
    log.info("  errors               : %d", st["errors"])
    log.info("  verify failures      : %d", st["verify_failures"])
    log.info("---------------------------------------------")
    return 1 if (st["errors"] or st["verify_failures"]) else 0


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
    parser.add_argument("--player-stats", action="store_true",
                        help="run the /player-stats population pass (fills "
                             "fs_advanced_stats_json) instead of the match sync")
    parser.add_argument("--enrich-test", metavar="POST_IDS",
                        help="Part C test: run the enrich path (dry) against "
                             "these comma-separated `matches` post ids and exit")
    parser.add_argument("--reenrich", metavar="POST_IDS",
                        help="rebuild raw_match_data (only) on these comma-"
                             "separated `matches` post ids from a fresh /match; "
                             "use after an event-building logic change. Also "
                             "accepts @file with one id per line.")
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

    if args.reenrich:
        spec = args.reenrich
        if spec.startswith("@"):
            spec = Path(spec[1:]).read_text().replace("\n", ",")
        ids = [int(x) for x in spec.replace(" ", "").split(",") if x.strip()]
        return _run_reenrich(wp, fs, ids)

    if args.club_sync:
        return _run_club_sync(wp, fs)

    if args.player_stats:
        return _run_player_stats_sync(wp, fs)

    mode = "LIVE" if args.live else "DRY RUN (no writes)"
    log.info("Starting match sync - %s", mode)
    try:
        stats = sync(
            wp, fs,
            league_filter=args.league, limit=args.limit,
            enrich=not args.no_enrich,
        )
    except WPHostChallengeError as exc:
        log.error("=== HOST CHALLENGE - sync did NOT run (not a failure) ===")
        log.error("  %s", exc)
        log.error("  The host (SiteGround) served an anti-bot page instead of the")
        log.error("  WP REST API. Nothing was read or written. Retry later, or")
        log.error("  allowlist the runner / move the job to a stable IP.")
        return EXIT_HOST_CHALLENGE
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
    log.info("  text-only team (no page): %d", stats["text_only_team"])
    log.info("  errors                 : %d", stats["errors"])
    log.info("---------------------------------------------")

    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
