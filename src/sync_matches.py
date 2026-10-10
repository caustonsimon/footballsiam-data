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

import re
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
    backfill_coach_ids,
    build_player_lookup,
    enrich_match,
    populate_head_coaches,
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

# FootyStats team id -> WP `national_team` post id. Deliberately separate
# from CLUB_MAP: national sides (Thailand, Cambodia, Laos etc. playing as
# countries, not their domestic clubs) get their own CPT (Code Snippet 160)
# rather than inflating/confusing the club count -- Simon, 2026-09-23: "lets
# create a new setup for international teams they should not be counted as
# clubs as it is confusing". Populated by build_national_team_map() in
# enrich.py (mirrors ensure_player()/ensure_head_coach() -- auto-created on
# sync, no hand-typed backfill like the old club maps needed). Starts empty:
# FootyStats has not yet served the team lists for these two leagues (both
# still within its post-"choose league" activation delay as of 2026-09-23),
# so until it's populated both leagues fall back to plain-text
# home_team_name/away_team_name rendering, exactly like Cambodian League did
# on its first day.
NATIONAL_TEAM_MAP: dict[int, int] = {
    # International ASEAN Cup (season 17950) -- 14 teams, populated
    # 2026-09-23 via build_national_team_map() once FootyStats' "league not
    # chosen" activation delay cleared.
    8604: 28802, 8616: 28803, 8617: 28804, 8618: 28805, 8622: 28806,
    8583: 28807, 8593: 28808, 8601: 28809, 8608: 28810, 8609: 28811,
    8612: 28812, 8613: 28813, 8614: 28814, 8737: 28815,
    # International Friendlies (season 16519) -- 177 national sides,
    # populated the same run. If a country never seen before plays a
    # friendly and shows up unmapped, re-run build_national_team_map(wp,
    # fs, 16519, audit=...) and add the new fs_id -> wp_id pairs here --
    # same one-time-refresh pattern as CLUB_MAP, not an auto-live lookup,
    # so a dry run never creates WP posts as a side effect.
    7954: 28816, 7960: 28817, 7969: 28818, 7970: 28819, 7980: 28820,
    7981: 28821, 7998: 28822, 8624: 28823, 8627: 28824, 8630: 28825,
    8632: 28826, 8635: 28827, 8638: 28828, 7990: 28829, 7996: 28830,
    8579: 28831, 8628: 28832, 8629: 28833, 8652: 28834, 8663: 28835,
    8691: 28836, 7955: 28837, 7966: 28838, 7987: 28839, 8004: 28840,
    7999: 28841, 8655: 28842, 8738: 28843, 8659: 28844, 8730: 28845,
    7957: 28846, 7975: 28847, 7986: 28848, 7967: 28849, 8615: 28850,
    8683: 28851, 8588: 28852, 7959: 28853, 7961: 28854, 7963: 28855,
    7979: 28856, 8005: 28857, 8582: 28858, 8585: 28859, 8589: 28860,
    8619: 28861, 8625: 28862, 8634: 28863, 8637: 28864, 8658: 28865,
    8661: 28866, 8685: 28867, 8697: 28868, 8708: 28869, 7983: 28870,
    7984: 28871, 7985: 28872, 8007: 28873, 8674: 28874, 7962: 28875,
    7982: 28876, 8006: 28877, 8639: 28878, 8656: 28879, 7964: 28880,
    8678: 28881, 8682: 28882, 7991: 28883, 8657: 28884, 8676: 28885,
    8686: 28886, 8690: 28887, 8693: 28888, 8709: 28889, 8728: 28890,
    8729: 28891, 8731: 28892, 8741: 28893, 7994: 28894, 8607: 28895,
    8631: 28896, 8692: 28897, 8734: 28898, 8736: 28899, 8580: 28900,
    8669: 28901, 8673: 28902, 8681: 28903, 8688: 28904, 8703: 28905,
    8710: 28906, 8636: 28907, 7977: 28908, 7993: 28909, 8633: 28910,
    8653: 28911, 8680: 28912, 3986: 28913, 8592: 28914, 8605: 28915,
    8718: 28916, 8719: 28917, 9024: 28918, 698333: 28919, 698334: 28920,
    8626: 28921, 8694: 28922, 8698: 28923, 7995: 28924, 7973: 28925,
    8621: 28926, 8666: 28927, 8675: 28928, 8677: 28929, 8696: 28930,
    8711: 28931, 8733: 28932, 9025: 28933, 11517: 28934, 674518: 28935,
    678165: 28936, 7971: 28937, 8699: 28938, 8701: 28939, 8687: 28940,
    8704: 28941, 8695: 28942, 8670: 28943, 8671: 28944, 8702: 28945,
    8713: 28946, 7965: 28947, 7972: 28948, 7974: 28949, 7989: 28950,
    7997: 28951, 7978: 28952, 8581: 28953, 7968: 28954, 7988: 28955,
    8000: 28956, 8003: 28957, 8584: 28958, 7958: 28959, 8595: 28960,
    8620: 28961, 8001: 28962, 8002: 28963, 8590: 28964, 8599: 28965,
    8602: 28966, 8606: 28967, 8654: 28968, 8667: 28969, 8716: 28970,
    7992: 28971, 7956: 28972, 8591: 28973, 8594: 28974, 8715: 28975,
    8706: 28976, 8587: 28977, 8598: 28978, 8600: 28979, 8664: 28980,
    8668: 28981, 8705: 28982, 8722: 28983, 8732: 28984, 8596: 28985,
    8689: 28986, 8603: 28987, 8712: 28988, 8660: 28989, 8679: 28990,
    8700: 28991, 8740: 28992,
}

# The 11 ASEAN Football Federation (AFF) member nations, exactly as
# FootyStats' International Friendlies (season 16519) home_name/away_name
# fields spell them (confirmed 2026-09-23 via a live pull of all 377
# matches -- plain country names, no "National Team" suffix). Used only to
# filter International Friendlies at sync time -- see country_filter below.
# Not applied to International ASEAN Cup (season 17950): that competition
# is inherently ASEAN-scoped already (plus a handful of invited guest
# nations, e.g. Pakistan) and low-volume, so it needs no filtering.
#
# Simon, 2026-09-23 (site-wide Fixtures/Results page, not just the
# national_team profile page filter already built for Snippets 163/164):
# "still pulling too much" -- of 377 International Friendlies matches
# pulled from FootyStats, only 18 involved an ASEAN nation at all; the
# other 359 (e.g. Kazakhstan vs Cameroon, Lebanon vs Sudan) are pure noise
# for a Thai/Southeast Asian football site and were never supposed to be
# synced in the first place. Fixed at the source per this project's
# "thoughtless and app-ready data" principle, rather than patched again at
# display time in Snippet 158.
ASEAN_MEMBERS: frozenset[str] = frozenset({
    "brunei darussalam", "cambodia", "indonesia", "laos", "malaysia",
    "myanmar", "philippines", "singapore", "thailand", "timor-leste",
    "vietnam",
})


def is_asean_country(name: str | None) -> bool:
    return str(name or "").strip().lower() in ASEAN_MEMBERS


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
    {
        "name": "Thai League 3 Central",
        "season_id": 17934,
        "club_map": CLUB_MAP,
        "taxonomies": {"competition": [133], "categories": [134], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "Laos Premier League",
        "season_id": 15549,          # FootyStats: "Laos Laos Premier League", 2025/26 season
        "club_map": CLUB_MAP,
        # This is FootyStats' real, already-concluded 2025/26 Laos season --
        # unlike every other league here, it is NOT the site's current
        # 2026/27 season, so it needs its own season postmeta/term rather
        # than the global SEASON_LABEL/SEASON_TERM_ID (season taxonomy term
        # 115 = "2025", paired with postmeta "2025/2026" -- same pairing
        # already used by the site's 240 archived 2025/26 Thai League 1
        # matches, confirmed 2026-09-22).
        "taxonomies": {"competition": [211], "categories": [210], "season": [115]},
        "season_label": "2025/2026",
        "require_full_club_map": False,
    },
    {
        "name": "Cambodian League",
        "season_id": 17484,          # FootyStats: "Cambodia Cambodian League", 2026/27 season
        "club_map": CLUB_MAP,
        # Unlike Laos, this IS FootyStats' current 2026/27 season, so it
        # uses the site's global SEASON_LABEL/SEASON_TERM_ID like every
        # other current-season league -- no season_label override needed.
        "taxonomies": {"competition": [213], "categories": [212], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    # -- Added 2026-09-23: international/national-team competitions. Simon
    # added both to his FootyStats account; club_map here resolves to
    # `national_team` WP posts (not `club`), see NATIONAL_TEAM_MAP above.
    # require_full_club_map False, same reasoning as every other
    # not-yet-fully-mapped league: falls back to text-only rendering for any
    # side not yet in the map rather than aborting the sync.
    {
        "name": "International ASEAN Cup",
        "season_id": 17950,          # FootyStats: "International ASEAN Cup", 2026 season
        "club_map": NATIONAL_TEAM_MAP,
        "taxonomies": {"competition": [215], "categories": [217], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
    },
    {
        "name": "International Friendlies",
        "season_id": 16519,          # FootyStats: "International International Friendlies", 2026 season
        "club_map": NATIONAL_TEAM_MAP,
        "taxonomies": {"competition": [216], "categories": [218], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
        # Only sync a friendly if at least one side is an ASEAN nation --
        # see ASEAN_MEMBERS above. Applied in sync()'s per-league loop.
        "country_filter": "asean",
    },
    # -- Added 2026-09-29: ASEAN Club Championship (sponsored as the Shopee
    # Cup; house name drops the sponsor). FootyStats added it on request
    # 2026-09-28. The 42 group-stage posts (33171-33212) were seeded by hand
    # on 2026-09-28 and have since been given their footystats_match_id, so
    # this entry UPDATES them (scores/enrichment) rather than creating
    # duplicates. competition term 122 (pre-existing "ASEAN Club
    # Championship"), category term 235.
    {
        "name": "ASEAN Club Championship",
        "season_id": 17970,          # FootyStats: "Asia ASEAN Club Championship", 2026/27
        "club_map": {**CLUB_MAP, 9481: 28792},   # + Svay Rieng (Cambodian club post)
        "taxonomies": {"competition": [122], "categories": [235], "season": [SEASON_TERM_ID]},
        "require_full_club_map": False,
        # FootyStats still carries several clubs under old/sponsor names;
        # map them to house names for any post the sync creates itself
        # (qualifying play-offs, knockout rounds). Existing posts' names are
        # never touched by the update path.
        "name_overrides": {
            "Home United": "Lion City Sailors",
            "Công An Nhân Dân": "Công An Hà Nội",
            "Ho Chi Minh City": "Công An Hồ Chí Minh City",
            "Kuching FA": "Kuching City",
            "Persib": "Persib Bandung",
            "Port FC": "Port",
            "Tampines Rovers": "Tampines Rovers",
        },
        "skip_placeholder_teams": True,
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


_PLACEHOLDER_TEAM_RE = re.compile(r"\b(ranked|winner|winners|loser|runner-up|tbd|tbc)\b", re.IGNORECASE)


def is_placeholder_team(name: str | None) -> bool:
    """True for FootyStats knockout-slot placeholders such as "2nd ranked Group A"
    or "Winner QF1" -- fixtures whose sides aren't known yet. Syncing those would
    create posts titled with the placeholder, then leave them orphaned once
    FootyStats swaps in the real teams under a different match id. Added
    2026-09-29 for the ASEAN Club Championship (season 17970)."""
    return bool(_PLACEHOLDER_TEAM_RE.search(str(name or "")))


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


POSTPONED_STATUSES = {"postponed", "suspended", "canceled", "cancelled", "abandoned"}


def unplayed_status_update(match: dict[str, Any], meta: dict[str, Any]) -> dict[str, str]:
    """Meta changes for a game FootyStats has not marked complete (added 2026-10-10).

    * match_status: the FootyStats status when it is postponed / suspended /
      cancelled / abandoned, otherwise "" (so a rescheduled game drops its flag).
    * date / kick_off_time: the Bangkok date and time from FootyStats' date_unix,
      when they differ from what the post holds (a rescheduled game moves).
    Returns {} when nothing changed, so a re-run writes nothing (idempotent).
    """
    upd: dict[str, str] = {}
    status = str(match.get("status", "")).strip().lower()
    want_status = status if status in POSTPONED_STATUSES else ""
    if str(meta.get("match_status") or "").strip().lower() != want_status:
        upd["match_status"] = want_status
    unix = match.get("date_unix")
    try:
        unix_i = int(unix)
    except (TypeError, ValueError):
        unix_i = 0
    if unix_i > 0:
        ymd, kickoff = bangkok_date_time(unix_i)
        if str(meta.get("date") or "").strip() != ymd:
            upd["date"] = ymd
        if str(meta.get("kick_off_time") or "").strip() != kickoff:
            upd["kick_off_time"] = kickoff
    return upd


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
    overrides = league.get("name_overrides") or {}
    home_raw = str(match.get("home_name", "")).strip()
    away_raw = str(match.get("away_name", "")).strip()
    home_title = (
        (wp.get_club_title(home_wp) if home_wp else None)
        or overrides.get(home_raw, home_raw)
    )
    away_title = (
        (wp.get_club_title(away_wp) if away_wp else None)
        or overrides.get(away_raw, away_raw)
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
            "season": league.get("season_label", SEASON_LABEL),
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
        "status_updated": 0,
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

        if league.get("country_filter") == "asean":
            before = len(matches)
            matches = [
                m for m in matches
                if is_asean_country(m.get("home_name")) or is_asean_country(m.get("away_name"))
            ]
            log.info(
                "  country_filter=asean: kept %d/%d (dropped %d with no ASEAN side)",
                len(matches), before, before - len(matches),
            )

        if league.get("skip_placeholder_teams"):
            before = len(matches)
            matches = [
                m for m in matches
                if not (is_placeholder_team(m.get("home_name")) or is_placeholder_team(m.get("away_name")))
            ]
            if before != len(matches):
                log.info(
                    "  skip_placeholder_teams: dropped %d knockout slot(s) with no real team yet",
                    before - len(matches),
                )

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
                        # -- unplayed game: status + reschedule (added 2026-10-10)
                        # The Matchday Watcher (Snippet 192) treats a game as done
                        # once it has a score OR match_status is postponed /
                        # suspended / cancelled / abandoned, and it reads the
                        # game's date to know which matchday it belongs to. So for
                        # an unplayed game, keep match_status and the Bangkok
                        # date/kick-off in step with FootyStats (authoritative,
                        # matched by footystats_match_id, never by name). Only
                        # writes when something actually changed.
                        upd = unplayed_status_update(match, meta)
                        if upd:
                            wp.update_match_meta(existing["id"], upd)
                            stats["status_updated"] += 1
                            _audit(
                                f"STATUS post {existing['id']}  fs {fs_id}  {upd}  "
                                f"({match.get('home_name')} v {match.get('away_name')})"
                            )
                else:
                    stats["skipped_recorded"] += 1
                    # A game that has been played clears any old postponed flag.
                    if str(meta.get("match_status") or "").strip() and status == "complete":
                        wp.update_match_meta(existing["id"], {"match_status": ""})
                        stats["status_updated"] += 1
                        _audit(f"STATUS post {existing['id']}  fs {fs_id}  cleared (played)")

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
    wp: WPClient,
    fs: FootyStatsClient,
    *,
    sample_limit: int = 10,
    skip_existing: bool = True,
) -> int:
    """Fills fs_advanced_stats_json (full /player-stats row list) for every
    player with a footystats_id. Independent from --club-sync - same data
    source, different field, no reassignment logic.

    FootyStats enforces a 1,800 request/hour cap, so a single run over all
    ~2,172 players cannot complete in one pass. skip_existing=True (the
    default) skips any player who already has a populated
    fs_advanced_stats_json before spending a request on them, so repeat runs
    (scheduled or manual) make forward progress instead of re-covering the
    same first ~1,800 players every time. When the rate limit is hit
    mid-run, populate_player_stats() stops cleanly (rate_limited_stop=True)
    rather than retry-hammering every remaining player until the job's
    timeout kills it.
    """
    mode = "LIVE" if not wp.dry_run else "DRY RUN (report only, no writes)"
    log.info(
        "Starting player stats population - %s (skip_existing=%s)",
        mode, skip_existing,
    )
    try:
        st = populate_player_stats(
            wp, fs, audit=_audit, sample_limit=sample_limit,
            skip_existing=skip_existing,
        )
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
    log.info("  skipped (already had profile): %d", st.get("already_had_profile", 0))
    log.info("  updated / would update       : %d", st["updated"])
    log.info("  no rows returned             : %d", st["no_rows"])
    log.info("  bad FS record (manual review): %d", st["bad_fs_record"])
    log.info("  errors                       : %d", st["errors"])
    if st.get("rate_limited_stop"):
        log.warning(
            "  STOPPED EARLY: FootyStats hourly rate limit reached. "
            "Re-run later (skip_existing=%s) to continue with the "
            "remaining players once the quota resets.",
            skip_existing,
        )
    if st.get("update_sample"):
        log.info("  --- sample ---")
        for s in st["update_sample"]:
            log.info("    %s", s)
    log.info("---------------------------------------------")

    return 1 if st["errors"] else 0


def _run_head_coach_sync(
    wp: WPClient, fs: FootyStatsClient, *, sample_limit: int = 10
) -> int:
    """Phase 2: resolves each club's current head coach from captured
    match-level coach ids (home_coach_id/away_coach_id, only present on
    matches enriched since 2026-09-22 - see enrich_match()'s coach_meta
    block) and stores the coach's managerial record from FootyStats'
    /manager endpoint. Labeled "Head Coach" everywhere - never "Manager"
    (Simon's explicit naming decision).

    No FootyStats hourly-cap batching flag here (unlike --player-stats):
    coverage is naturally bounded by how many clubs have a captured coach
    id yet (grows as new matches are enriched), which will be well under
    ~20 clubs for a long time - nowhere near the 1,800/hour cap. Revisit if
    that changes.
    """
    mode = "LIVE" if not wp.dry_run else "DRY RUN (report only, no writes)"
    log.info("Starting head coach population - %s", mode)
    try:
        st = populate_head_coaches(wp, fs, audit=_audit, sample_limit=sample_limit)
    except WPHostChallengeError as exc:
        log.error("=== HOST CHALLENGE - head coach sync did NOT run (not a failure) ===")
        log.error("  %s", exc)
        return EXIT_HOST_CHALLENGE
    except (WPError, FootyStatsError) as exc:
        log.error("Head coach sync aborted: %s", exc)
        return 2

    log.info("---------------------------------------------")
    log.info("Head coach population summary (%s)", mode)
    log.info("  matches scanned               : %d", st["matches_scanned"])
    log.info("  clubs with a captured coach id: %d", st["clubs_with_evidence"])
    log.info("  clubs scanned                 : %d", st["clubs_scanned"])
    log.info("  no evidence yet (skipped)     : %d", st["no_evidence"])
    log.info("  updated / would update        : %d", st["updated"])
    log.info("  no rows returned              : %d", st["no_rows"])
    log.info("  bad FS record (manual review) : %d", st["bad_fs_record"])
    log.info("  errors                        : %d", st["errors"])
    if st.get("rate_limited_stop"):
        log.warning(
            "  STOPPED EARLY: FootyStats hourly rate limit reached. "
            "Re-run later to continue with the remaining clubs."
        )
    if st.get("update_sample"):
        log.info("  --- sample ---")
        for s in st["update_sample"]:
            log.info("    %s", s)
    if st.get("no_evidence_sample"):
        log.info("  --- no-evidence sample ---")
        for s in st["no_evidence_sample"]:
            log.info("    %s", s)
    log.info("---------------------------------------------")

    return 1 if st["errors"] else 0


def _run_coach_backfill(wp: WPClient, fs: FootyStatsClient) -> int:
    """One-time: backfill home_coach_id/away_coach_id onto already-enriched
    matches missing them, so populate_head_coaches() has immediate coverage
    instead of waiting for new matches. See backfill_coach_ids() docstring.
    """
    mode = "LIVE" if not wp.dry_run else "DRY RUN (report only, no writes)"
    log.info("Starting coach-id backfill - %s", mode)
    try:
        st = backfill_coach_ids(wp, fs, audit=_audit, season=SEASON_LABEL)
    except WPHostChallengeError as exc:
        log.error("=== HOST CHALLENGE - coach-id backfill did NOT run (not a failure) ===")
        log.error("  %s", exc)
        return EXIT_HOST_CHALLENGE
    except (WPError, FootyStatsError) as exc:
        log.error("Coach-id backfill aborted: %s", exc)
        return 2

    log.info("---------------------------------------------")
    log.info("Coach-id backfill summary (%s)", mode)
    log.info("  matches scanned               : %d", st["scanned"])
    log.info("  already had coach meta        : %d", st["already_had_coach_meta"])
    log.info("  skipped (no footystats id)    : %d", st["no_footystats_id"])
    log.info("  skipped (not played yet)      : %d", st["skipped_unplayed"])
    log.info("  skipped (other seasons)       : %d", st["skipped_other_season"])
    log.info("  updated / would update        : %d", st["updated"])
    log.info("  no coach data on /match        : %d", st["no_coach_data"])
    log.info("  errors                        : %d", st["errors"])
    if st.get("rate_limited_stop"):
        log.warning(
            "  STOPPED EARLY: FootyStats hourly rate limit reached. "
            "Re-run later to continue with the remaining matches."
        )
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
    if st.get("rate_limited_stop"):
        log.warning("  STOPPED EARLY: FootyStats hourly limit hit - re-run after the reset")
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
    parser.add_argument("--refresh-player-stats", action="store_true",
                        help="with --player-stats, re-fetch players that already "
                             "have fs_advanced_stats_json instead of skipping them "
                             "(default is to skip, so repeat runs make forward "
                             "progress against the FootyStats hourly rate limit)")
    parser.add_argument("--head-coach-sync", action="store_true",
                        help="run the head-coach population pass (resolves each "
                             "club's current Head Coach from captured match coach "
                             "ids, fetches /manager, writes fs_head_coach_json + "
                             "head_coach) instead of the match sync")
    parser.add_argument("--backfill-coach-ids", action="store_true",
                        help="one-time: backfill home_coach_id/away_coach_id onto "
                             "already-enriched matches missing them, so "
                             "--head-coach-sync has immediate coverage instead of "
                             "waiting for new matches to be enriched one at a time")
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
        return _run_player_stats_sync(
            wp, fs, skip_existing=not args.refresh_player_stats
        )

    if args.head_coach_sync:
        return _run_head_coach_sync(wp, fs)

    if args.backfill_coach_ids:
        return _run_coach_backfill(wp, fs)

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
    log.info("  status/date updated    : %d", stats["status_updated"])
    log.info("  unmapped team (skip)   : %d", stats["unmapped_team"])
    log.info("  text-only team (no page): %d", stats["text_only_team"])
    log.info("  errors                 : %d", stats["errors"])
    log.info("---------------------------------------------")

    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
