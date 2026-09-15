#!/usr/bin/env python3
"""Thai League 2 full squad import.

Adds missing `player` posts for every T2 club, sourced from FootyStats' full
league player list rather than match-derived appearances. Part A (enrich.py)
only creates a player once they actually appear in an *enriched match* -
which misses anyone who hasn't played a competitive minute yet: new
signings, backup keepers, reserves. This script fills that gap directly
from the season's full player list instead of waiting for them to appear.

NOT YET VERIFIED against a live FootyStats response. Every network path
available while writing this was blocked (this machine's sandbox, the cloud
sandbox, and even a plain web fetch - refused by robots.txt), so the
endpoint name (/league-players) and the exact field names for player id /
club id on each row (see PLAYER_ID_KEYS / CLUB_ID_KEYS below) are a
best-effort guess following FootyStats' usual naming elsewhere in this repo.
Run --check FIRST - it fetches one page and prints every key on the first
two rows, with no WordPress calls at all, specifically so a wrong guess
shows up immediately instead of silently importing nothing (or the wrong
thing).

Reuses enrich.ensure_player() exactly as Part A does, so player-creation
logic (name resolution, position mapping, ACF fields, audit logging) stays
in one place rather than being duplicated here. Unlike Part A's auto-create
(which deliberately leaves current_club blank pending real evidence - see
enrich.py's module docstring), this DOES set current_club right away: a
season's squad list from FootyStats is a direct statement of "plays for this
club now", not a guess in the way a raw appearance history can be.

Safe to re-run against all 18 T2 clubs, not just the 15 with no data yet -
build_player_lookup() skips anyone who already has a footystats_id on file,
so already-imported players (and the historically-backfilled 3 clubs) are
just skipped, not duplicated.

Usage:
    python src/import_t2_squads.py --check     # inspect raw API shape only, no writes at all
    python src/import_t2_squads.py             # dry run - report only, no writes
    python src/import_t2_squads.py --live       # actually create missing players
    python src/import_t2_squads.py --limit 5    # cap how many new players get processed (testing)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from enrich import build_player_lookup, ensure_player
from footystats_client import FootyStatsClient, FootyStatsError
from sync_matches import CLUB_MAP_TL2  # importing this also loads .env as a side effect
from wp_client import WPClient, WPError, WPHostChallengeError

log = logging.getLogger("import_t2_squads")

TL2_SEASON_ID = 17896

# Best-guess candidate key names for the player id / club id on each
# /league-players row - whichever name actually matches the live response
# wins. --check prints the real keys so this can be corrected in one place
# if the guess is wrong.
PLAYER_ID_KEYS = ("id", "player_id")
CLUB_ID_KEYS = ("club_team_id", "team_id", "club_id")


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                         help="Actually create missing players. Default: dry run / report only.")
    parser.add_argument("--check", action="store_true",
                         help="Fetch one page of /league-players and print the raw shape of "
                              "the first 2 rows, then exit. No WordPress calls at all.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Stop after considering this many candidate players (testing).")
    args = parser.parse_args(argv)

    fs = FootyStatsClient()

    if args.check:
        try:
            rows = fs.get_league_players(TL2_SEASON_ID)
        except FootyStatsError as exc:
            log.error("FAILED: %s", exc)
            return 1
        print(f"Got {len(rows)} rows total.")
        for r in rows[:2]:
            print("---")
            for k in sorted(r.keys()):
                print(f"  {k}: {r[k]!r}")
        return 0

    wp = WPClient(dry_run=not args.live)
    lookup = build_player_lookup(wp)
    log.info("Existing players on file with a footystats_id: %d", len(lookup))

    try:
        rows = fs.get_league_players(TL2_SEASON_ID)
    except FootyStatsError as exc:
        log.error("FAILED to fetch /league-players: %s", exc)
        return 1
    log.info("FootyStats /league-players (season %s): %d rows", TL2_SEASON_ID, len(rows))

    considered = 0
    created = 0
    skipped_existing = 0
    skipped_untracked_club = 0
    skipped_no_id = 0
    skipped_no_name = 0
    errors = 0

    for row in rows:
        if args.limit is not None and considered >= args.limit:
            break
        fs_pid = _first_present(row, PLAYER_ID_KEYS)
        club_fid = _first_present(row, CLUB_ID_KEYS)
        if fs_pid is None:
            skipped_no_id += 1
            continue

        name = (row.get("full_name") or row.get("known_as") or "").strip()
        if not name:
            log.info("  fs_pid=%s: skipping - no name on file (likely a dead/junk "
                     "FootyStats ID, e.g. the known 42458)", fs_pid)
            skipped_no_name += 1
            continue

        club_wp_id = CLUB_MAP_TL2.get(int(club_fid)) if club_fid is not None else None
        if club_wp_id is None:
            skipped_untracked_club += 1
            continue

        if str(int(fs_pid)) in lookup:
            skipped_existing += 1
            continue

        considered += 1

        def audit(msg: str) -> None:
            log.info("  %s", msg)

        try:
            entry = ensure_player(
                wp, fs, int(fs_pid), lookup,
                last_club_wp_id=club_wp_id,
                last_date_ymd=time.strftime("%Y-%m-%d"),
                audit=audit,
            )
        except (WPError, WPHostChallengeError, FootyStatsError) as exc:
            log.error("  player %s: FAILED (%s)", fs_pid, exc)
            errors += 1
            continue

        if entry is None:
            continue
        created += 1

        # Squad-list-sourced, so the club is known for certain - set
        # current_club directly rather than leaving it blank the way Part
        # A's match-derived auto-create does.
        if entry.get("wp_id") and not wp.dry_run:
            try:
                wp.update_player(entry["wp_id"], acf={"current_club": [club_wp_id]})
            except WPError as exc:
                log.warning("  player %s: created but current_club write failed (%s)",
                            fs_pid, exc)

    log.info("")
    log.info("=== T2 squad import summary (%s) ===", "LIVE" if args.live else "DRY RUN")
    log.info(
        "considered=%d created=%d skipped_existing=%d "
        "skipped_untracked_club=%d skipped_no_id=%d skipped_no_name=%d errors=%d",
        considered, created, skipped_existing, skipped_untracked_club,
        skipped_no_id, skipped_no_name, errors,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
