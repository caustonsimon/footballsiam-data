#!/usr/bin/env python3
"""Daily sync: FootyStats fixtures/results -> footballsiam.com `matches` CPT.

For Thai League 1 (season_id 17367) and Thai League 2 (season_id 17896):

  * fetch every match from FootyStats
  * find the WP `matches` post whose `footystats_match_id` meta == that match id
  * exists + FootyStats status == "complete" + WP home_score still empty
        -> write home_score, away_score, match_week
  * no post at all
        -> create it (README field + club-ID mapping)
  * a post that already has a score recorded is NEVER touched

Safety: writes only happen with --live. Without it the run is a dry run
(reads still hit WP so the would-be payloads are realistic).

    python src/sync_matches.py              # dry run, both leagues
    python src/sync_matches.py --live       # actually write
    python src/sync_matches.py --check      # just verify credentials/connectivity
    python src/sync_matches.py --league "Thai League 2" --limit 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from typing import Any

from footystats_client import FootyStatsClient, FootyStatsError
from wp_client import WPClient, WPError

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

    payload: dict[str, Any] = {
        "title": f"{home_title} vs {away_title}",
        "status": "publish",
        "meta": {
            "home_team": home_wp,
            "away_team": away_wp,
            "home_score": "",
            "away_score": "",
            "date": ymd,                       # YYYYMMDD - required by fs_get_matches()
            "kick_off_time": kickoff,          # H:i, 24h, Asia/Bangkok
            "match_week": match_week_of(match),
            "season": SEASON_LABEL,
            "footystats_match_id": int(match["id"]),
        },
    }
    payload.update(league["taxonomies"])       # competition / categories / season
    return payload


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

def sync(
    wp: WPClient,
    fs: FootyStatsClient,
    *,
    league_filter: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    stats = {
        "checked": 0,
        "scores_updated": 0,
        "created": 0,
        "skipped_recorded": 0,
        "skipped_incomplete": 0,
        "unmapped_team": 0,
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
                if not is_empty_score(meta.get("home_score")):
                    stats["skipped_recorded"] += 1
                    continue

                if status != "complete":
                    stats["skipped_incomplete"] += 1
                    continue

                new_meta = {
                    "home_score": str(int(match.get("homeGoalCount", 0) or 0)),
                    "away_score": str(int(match.get("awayGoalCount", 0) or 0)),
                    "match_week": match_week_of(match),
                }
                wp.update_match_meta(existing["id"], new_meta)
                stats["scores_updated"] += 1
                log.info("  SCORE   %s  post %s  ->  %s-%s  [wk %s]",
                         fs_id, existing["id"],
                         new_meta["home_score"], new_meta["away_score"],
                         new_meta["match_week"])
            except (WPError, KeyError) as exc:
                stats["errors"] += 1
                log.error("  ERROR on match %s: %s", fs_id, exc)

    return stats


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

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
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        fs = FootyStatsClient()
        wp = WPClient(dry_run=not args.live)
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

    mode = "LIVE" if args.live else "DRY RUN (no writes)"
    log.info("Starting sync - %s", mode)
    try:
        stats = sync(wp, fs, league_filter=args.league, limit=args.limit)
    except (WPError, FootyStatsError) as exc:
        log.error("Sync aborted: %s", exc)
        return 2

    log.info("---------------------------------------------")
    log.info("Summary (%s)", mode)
    log.info("  matches checked      : %d", stats["checked"])
    log.info("  scores updated       : %d", stats["scores_updated"])
    log.info("  new posts created    : %d", stats["created"])
    log.info("  skipped (has score)  : %d", stats["skipped_recorded"])
    log.info("  skipped (not complete): %d", stats["skipped_incomplete"])
    log.info("  unmapped team (skip) : %d", stats["unmapped_team"])
    log.info("  errors               : %d", stats["errors"])
    log.info("---------------------------------------------")

    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
