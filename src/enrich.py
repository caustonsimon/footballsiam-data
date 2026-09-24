#!/usr/bin/env python3
"""Match-report enrichment (Part A) and club reassignment (Part B).

Part A - enrich_match()
    When a match first goes `complete`, fetch /match detail and write:
      * team stats  (home/away_corners, offsides, on_target, off_target,
        attempts, fouls, possession)
      * home_xg / away_xg  and  home_xg_prematch / away_xg_prematch
      * raw_match_data  (base64 JSON: player_stats[] with wp_id + position,
        match_events[] with p1_wp_id; own-goal detection)
    Missing players are auto-created (Part A step 5) with last_confirmed_club /
    last_confirmed_date and NO current_club.
    Guard: a match that already has raw_match_data is skipped.

Part B - reassign_clubs()
    Full pass over every `player`. For each, fetches the LIVE FootyStats
    /player-stats list and finds the most recent row that is:
      * league_type == "Domestic League"   (no cups, no internationals)
      * club_team_id > 0
      * appearances_overall > 0            (a played season, not a roster listing)
    ordered by (starting_year, ending_year, last_match_timestamp). Then, ID-only:
      * that club is one of our 34 tracked clubs, differs from current_club
            -> set current_club to the right WP club id
      * that club is NOT tracked, current_club is set
            -> clear current_club
      * no eligible row at all
            -> no action, logged as "insufficient evidence"
    Never name-matches. The derived structures in fs_advanced_stats_json
    (all_seasons_metrics etc.) are NOT used - they collapse multiple
    competitions per season and silently lose transfers.

Both paths log every write so an unattended run leaves an audit trail.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import time
from typing import Any, Callable

from footystats_client import FootyStatsClient, FootyStatsError
from wp_client import WPClient, WPError

log = logging.getLogger("enrich")

BANGKOK_TZ = dt.timezone(dt.timedelta(hours=7))

# ACF field keys on the `player` CPT (needed only for the bare-meta companions;
# writing through the REST `acf` object does not need them, but keeping them
# here documents the field group).
PLAYER_FIELD_KEYS = {
    "footystats_id": "field_6a740e0d96d5b",
    "first_name": "field_69c4fab23e14b",
    "last_name": "field_69c4fadd3e14c",
    "nationality": "field_69c4fae93e14d",
    "position": "field_69c4fef2885f0",
    "current_club": "field_69c4fb633e152",
    "fs_advanced_stats_json": "field_6a7582b95f4c7",
}

# team_a_* / team_b_* FootyStats key  ->  home_* / away_* WP meta key
TEAM_STAT_MAP = {
    "corners": "corners",
    "offsides": "offsides",
    "shotsOnTarget": "on_target",
    "shotsOffTarget": "off_target",
    "shots": "attempts",
    "fouls": "fouls",
    "possession": "possession",
}

# FootyStats player_events / card_details type  ->  (normalized event_type,
# event_detail). FootyStats splits goals into "Goal" / "Penalty" / "Own Goal"
# and dismissals into "Red" / "Second Yellow"; downstream code keys on the plain
# type, so collapse the variants and keep the nuance in event_detail.
# event_team is NEVER changed - an own goal stays on the scorer's real team,
# exactly as FootyStats reports it.
EVENT_TYPE_MAP = {
    "Goal":          ("Goal", ""),
    "Penalty":       ("Goal", "penalty"),
    "Own Goal":      ("Goal", "own goal"),
    "Second Yellow": ("Red", "second yellow"),
}


def _normalize_event(raw_type: Any) -> tuple[str, str]:
    return EVENT_TYPE_MAP.get(str(raw_type), (str(raw_type), ""))


# --------------------------------------------------------------------------
# Position mapping  (the approved rule - first match wins)
# --------------------------------------------------------------------------

def map_position(raw: Any) -> str:
    """FootyStats position string -> GK / DF / MF / FW  ('' if unknowable)."""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if "keeper" in s or s == "gk":
        return "GK"
    if any(w in s for w in ("back", "defender", "defence", "defense",
                            "cb", "rb", "lb", "wb")):
        return "DF"
    if "midfield" in s or s in ("dm", "cm", "am", "mf"):
        return "MF"
    if any(w in s for w in ("forward", "striker", "winger", "wing",
                            "attacker", "fw", "cf")):
        return "FW"
    return ""


# --------------------------------------------------------------------------
# Player lookup  (footystats_id -> {wp_id, title, position})
# --------------------------------------------------------------------------

def build_player_lookup(wp: WPClient) -> dict[str, dict[str, Any]]:
    """Fresh every call - the table grows as players are auto-created."""
    lut: dict[str, dict[str, Any]] = {}
    for p in wp.iter_all_players(
        fields="id,title,acf.footystats_id,acf.position"
    ):
        acf = p.get("acf") or {}
        fid = acf.get("footystats_id")
        if fid in (None, "", 0, "0"):
            continue
        lut[str(fid)] = {
            "wp_id": p["id"],
            "title": (p.get("title") or {}).get("rendered", ""),
            "position": (acf.get("position") or "").strip(),
        }
    return lut


# --------------------------------------------------------------------------
# Part A helpers
# --------------------------------------------------------------------------

def _minute_variants(m: Any) -> set[str]:
    s = str(m).strip()
    return {s, s.split("+")[0].strip()}


def _assist_map(md: dict[str, Any]) -> dict[tuple[str, str], Any]:
    """(scorer FootyStats id, minute) -> assister FootyStats id, from
    team_a_goal_details / team_b_goal_details. Minute is stored both as given
    ("90+5") and as its base ("90") so either form of event_time matches.
    Goals with no assist (assist_player_id -1 / null / 0) are left out."""
    out: dict[tuple[str, str], Any] = {}
    for key in ("team_a_goal_details", "team_b_goal_details"):
        for g in md.get(key) or []:
            aid = g.get("assist_player_id")
            if aid in (None, "", -1, "-1", 0, "0"):
                continue
            for mv in _minute_variants(g.get("time")):
                out.setdefault((str(g.get("player_id")), mv), aid)
    return out


def _pick_player_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Best per-season row for identity fields (name / position / dob)."""
    if not rows:
        return {}
    tl = [r for r in rows
          if r.get("season") == "2025/2026" and "Thai League" in str(r.get("league", ""))]
    if tl:
        return tl[0]
    s2526 = [r for r in rows if r.get("season") == "2025/2026"]
    if s2526:
        return s2526[0]
    return sorted(
        rows,
        key=lambda r: (r.get("ending_year") or 0, r.get("last_match_timestamp") or 0),
    )[-1]


def ensure_player(
    wp: WPClient,
    fs: FootyStatsClient,
    fs_player_id: int,
    lookup: dict[str, dict[str, Any]],
    *,
    last_club_wp_id: int | None,
    last_date_ymd: str,
    audit: Callable[[str], None],
) -> dict[str, Any] | None:
    """Return the lookup entry for a FootyStats player id, creating the WP
    `player` post first if it does not exist yet. Mutates `lookup`.
    """
    key = str(int(fs_player_id))
    if key in lookup:
        return lookup[key]

    try:
        rows = fs.get_player(fs_player_id)
    except FootyStatsError as exc:
        log.warning("  player %s: FootyStats lookup failed (%s) - not created",
                    fs_player_id, exc)
        return None

    e = _pick_player_row(rows)
    name = (e.get("known_as") or e.get("full_name")
            or " ".join(x for x in [e.get("first_name"), e.get("last_name")] if x)).strip()
    if not name:
        log.warning("  player %s: FootyStats has no name - not created", fs_player_id)
        return None
    first = (e.get("first_name") or "").strip()
    last = (e.get("last_name") or "").strip()
    if not first and not last:
        parts = name.split()
        first, last = parts[0], " ".join(parts[1:])
    position = map_position(e.get("position"))
    nationality = (e.get("nationality") or "").strip()
    birthday = e.get("birthday") or 0

    acf = {
        "footystats_id": key,
        "first_name": first,
        "last_name": last,
        "nationality": nationality,
    }
    if position:
        # ACF's position field is a select limited to GK/DF/MF/FW - an empty
        # string is rejected by the REST API (rest_invalid_param). When
        # map_position() can't determine a position, just omit the key
        # entirely rather than sending "".
        acf["position"] = position
    meta = {"last_confirmed_date": last_date_ymd}
    if last_club_wp_id:
        meta["last_confirmed_club"] = str(int(last_club_wp_id))
    if birthday:
        meta["birthday"] = str(int(birthday))

    resp = wp.create_player({
        "title": name,
        "status": "publish",
        "acf": acf,
        "meta": meta,
    })
    new_id = None if wp.dry_run else int(resp.get("id"))
    club_txt = str(last_club_wp_id) if last_club_wp_id else "?"
    audit(
        f"PLAYER CREATE  {name}  fs={key}  wp={new_id if new_id else '(dry-run)'}  "
        f"club={club_txt}  pos={position or '(blank)'}  dob={birthday or '(none)'}"
    )
    entry = {"wp_id": new_id, "title": name, "position": position}
    lookup[key] = entry
    return entry


def ensure_national_team(
    wp: WPClient,
    team_row: dict[str, Any],
    lookup: dict[int, dict[str, Any]],
    *,
    audit: Callable[[str], None],
) -> dict[str, Any] | None:
    """Return the lookup entry for a FootyStats team id, creating the WP
    `national_team` post first if it does not exist yet. Mutates `lookup`.

    Mirrors ensure_player()'s lookup-or-create shape, but for international
    sides (FIFA ASEAN Cup, International Friendlies) -- these are NOT `club`
    posts (Simon, 2026-09-23: "they should not be counted as clubs as it is
    confusing"), they're the new `national_team` CPT (Code Snippet 160).
    `team_row` is one element of FootyStatsClient.get_league_teams()'s
    result list.
    """
    fs_id = team_row.get("id")
    try:
        fs_id = int(fs_id)
    except (TypeError, ValueError):
        return None

    if fs_id in lookup:
        return lookup[fs_id]

    name = str(team_row.get("name") or team_row.get("cleanName") or "").strip()
    if not name:
        log.warning("  national_team %s: FootyStats row has no name - not created", fs_id)
        return None

    meta = {"footystats_id": str(fs_id)}
    country_code = str(team_row.get("country") or "").strip()
    if country_code:
        meta["country_code"] = country_code

    resp = wp.create_national_team({
        "title": name,
        "status": "publish",
        "meta": meta,
    })
    new_id = None if wp.dry_run else int(resp.get("id"))
    audit(f"NATIONAL TEAM CREATE  {name}  fs={fs_id}  wp={new_id if new_id else '(dry-run)'}")

    entry = {"wp_id": new_id, "title": name}
    lookup[fs_id] = entry
    return entry


def build_national_team_map(
    wp: WPClient,
    fs: FootyStatsClient,
    season_id: int,
    *,
    audit: Callable[[str], None],
) -> dict[int, int]:
    """{ footystats_team_id -> wp `national_team` post id } for every team
    in a FootyStats season, auto-creating any national_team post that
    doesn't exist yet. Used to build the `club_map` for an international
    LEAGUES entry in sync_matches.py (International ASEAN Cup, International
    Friendlies) the same way CLUB_MAP_TL1/TL2 are hand-built for domestic
    leagues, except this one is generated automatically rather than typed in
    by hand, since there's no reason to hand-maintain a list of national
    sides the way the original club batch had to be hand-created one at a
    time before this CPT/auto-create pattern existed.
    """
    lookup = wp.index_national_teams_by_footystats_id()
    try:
        teams = fs.get_league_teams(season_id)
    except FootyStatsError as exc:
        log.warning("  national team map for season %s: FootyStats lookup failed (%s)",
                    season_id, exc)
        return {}

    out: dict[int, int] = {}
    for row in teams:
        entry = ensure_national_team(wp, row, lookup, audit=audit)
        if entry and entry.get("wp_id"):
            fs_id = row.get("id")
            try:
                out[int(fs_id)] = int(entry["wp_id"])
            except (TypeError, ValueError):
                continue
    return out


def _side_of(md: dict[str, Any], fs_pid: int) -> str | None:
    """Which side ('Home'/'Away') a FootyStats player id lined up / benched on."""
    for side, key in (("Home", "team_a"), ("Away", "team_b")):
        for p in (md.get("lineups") or {}).get(key, []) or []:
            if p.get("player_id") == fs_pid:
                return side
        for b in (md.get("bench") or {}).get(key, []) or []:
            if b.get("player_in_id") == fs_pid:
                return side
        for c in md.get(f"{key}_card_details", []) or []:
            if c.get("player_id") == fs_pid:
                return side
    return None


def _collect_player_ids(md: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for key in ("team_a", "team_b"):
        for p in (md.get("lineups") or {}).get(key, []) or []:
            pid = p.get("player_id")
            if isinstance(pid, int) and pid not in seen:
                seen.add(pid); ids.append(pid)
        for b in (md.get("bench") or {}).get(key, []) or []:
            pid = b.get("player_in_id")
            if isinstance(pid, int) and pid not in (-1,) and pid not in seen:
                seen.add(pid); ids.append(pid)
        for c in md.get(f"{key}_card_details", []) or []:
            pid = c.get("player_id")
            if isinstance(pid, int) and pid not in seen:
                seen.add(pid); ids.append(pid)
    return ids


def _placeholder_ids(md: dict[str, Any], side_key: str) -> set[Any]:
    """FootyStats sometimes reuses one player_id as an unknown-player placeholder
    across several squad slots. Detect it generically: any player_id that shows
    up 2+ times, at 2+ different shirt numbers, within one team's combined
    lineup + bench. (A real player has exactly one shirt number per match, so
    two distinct shirts for the same id means it is not an identity.)
    """
    from collections import defaultdict
    shirts: dict[Any, set] = defaultdict(set)
    for p in (md.get("lineups") or {}).get(side_key, []) or []:
        pid = p.get("player_id")
        if pid is not None:
            shirts[pid].add(p.get("shirt_number"))
    for b in (md.get("bench") or {}).get(side_key, []) or []:
        pid = b.get("player_in_id")
        if pid not in (None, -1):
            shirts[pid].add(b.get("player_in_shirt_number"))
    return {pid for pid, s in shirts.items() if len(s) >= 2}


def _build_raw_match_data(
    md: dict[str, Any], lookup: dict[str, dict[str, Any]]
) -> tuple[str, int]:
    """(base64 JSON string, own_goal_count)."""

    # Once an id is exposed as a placeholder anywhere in the match (2+ shirt
    # numbers within one team), treat it as a placeholder for EVERY slot in
    # that match - a single lone occurrence on the other side is the same
    # sentinel, not a real player.
    placeholder_ids = _placeholder_ids(md, "team_a") | _placeholder_ids(md, "team_b")

    def slot(pid: Any, team: str) -> tuple[Any, str, str]:
        """(wp_id, player_name, position) for a lineup/event slot. A detected
        placeholder id resolves to Unknown with no lookup."""
        if pid in placeholder_ids:
            return None, "Unknown", ""
        e = lookup.get(str(pid)) if pid is not None else None
        if e:
            return e["wp_id"], e["title"], e["position"]
        return None, str(pid), ""

    player_stats: list[dict[str, Any]] = []
    for side, key in (("Home", "team_a"), ("Away", "team_b")):
        for p in (md.get("lineups") or {}).get(key, []) or []:
            pid = p.get("player_id")
            w, nm, ps = slot(pid, side)
            player_stats.append({
                "player_id": pid, "wp_id": w, "team": side,
                "player_name": nm, "position": ps, "is_starter": True,
            })
        for b in (md.get("bench") or {}).get(key, []) or []:
            pid = b.get("player_in_id")
            if pid in (None, -1):
                continue
            w, nm, ps = slot(pid, side)
            player_stats.append({
                "player_id": pid, "wp_id": w, "team": side,
                "player_name": nm, "position": ps, "is_starter": False,
            })

    home_goal_mins: set[str] = set()
    for g in md.get("homeGoals", []) or []:
        home_goal_mins |= _minute_variants(g)
    away_goal_mins: set[str] = set()
    for g in md.get("awayGoals", []) or []:
        away_goal_mins |= _minute_variants(g)

    # Assists: team_a/b_goal_details carry assist_player_id per goal, keyed here
    # by (scorer id, minute). The match-report shortcode already renders
    # p2_name / p2_wp_id on goal events as the assist line.
    assists = _assist_map(md)

    events: list[dict[str, Any]] = []
    dedupe: set[tuple[str, str, str]] = set()
    own_goals = 0

    def add_event(etype: Any, minute: Any, pid: Any, team: str, detail: str = "") -> None:
        et = str(etype)
        mn = str(minute)
        k = (et, str(pid), mn.split("+")[0].strip())
        if k in dedupe:
            return
        dedupe.add(k)
        w, nm, _ = slot(pid, team)
        ev: dict[str, Any] = {
            "event_type": et, "event_detail": detail, "event_minute": mn,
            "p1_id": pid, "p1_wp_id": w, "p1_name": nm,
            "event_team": team,
        }
        if et == "Goal" and "own" not in detail:
            aid = assists.get((str(pid), mn)) or assists.get((str(pid), mn.split("+")[0].strip()))
            if aid is not None and str(aid) != str(pid):
                aw, an, _ = slot(aid, team)
                # skip unresolved ids (placeholder "Unknown" or a bare number)
                if an and an != "Unknown" and not an.isdigit():
                    ev.update({"p2_id": aid, "p2_wp_id": aw, "p2_name": an})
        events.append(ev)

    def handle(pid: Any, ev: dict[str, Any], team: str) -> None:
        nonlocal own_goals
        raw_type = ev.get("event_type")
        minute = ev.get("event_time")
        norm_type, detail = _normalize_event(raw_type)
        # event_team is always the player's own team - never flipped.
        if raw_type == "Own Goal":
            # trust FootyStats' explicit tag
            own_goals += 1
        elif raw_type == "Goal":
            # fallback: an untagged goal whose minute matches an OPPONENT goal
            # (and none of this team's) is an own goal FootyStats did not flag.
            mv = _minute_variants(minute)
            own = home_goal_mins if team == "Home" else away_goal_mins
            opp = away_goal_mins if team == "Home" else home_goal_mins
            if not (mv & own) and (mv & opp):
                detail = "own goal"
                own_goals += 1
        add_event(norm_type, minute, pid, team, detail)

    for side, key in (("Home", "team_a"), ("Away", "team_b")):
        for p in (md.get("lineups") or {}).get(key, []) or []:
            for ev in p.get("player_events", []) or []:
                handle(p.get("player_id"), ev, side)
        for b in (md.get("bench") or {}).get(key, []) or []:
            for ev in b.get("player_in_events", []) or []:
                handle(b.get("player_in_id"), ev, side)
    for key, side in (("team_a_card_details", "Home"), ("team_b_card_details", "Away")):
        for c in md.get(key, []) or []:
            nt, dt = _normalize_event(c.get("card_type"))
            add_event(nt, c.get("time"), c.get("player_id"), side, dt)

    blob = json.dumps(
        {"player_stats": player_stats, "match_events": events},
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return base64.b64encode(blob).decode("ascii"), own_goals


def enrich_match(
    wp: WPClient,
    fs: FootyStatsClient,
    fs_match: dict[str, Any],
    wp_post: dict[str, Any],
    club_map: dict[int, int],
    lookup: dict[str, dict[str, Any]],
    *,
    audit: Callable[[str], None],
) -> str:
    """Enrich one match. Returns one of: 'skipped_has_data', 'enriched',
    'error'. `wp_post` is the indexed post dict (needs id + meta).
    """
    post_id = int(wp_post["id"])
    fs_id = int(fs_match["id"])
    meta_now = wp_post.get("meta") or {}

    # -- Part A guard: already enriched (this sync or the historical backfill)
    if str(meta_now.get("raw_match_data") or "").strip():
        audit(f"ENRICH SKIP  post {post_id}  fs {fs_id}  (raw_match_data already set)")
        return "skipped_has_data"

    try:
        md = fs.get_match(fs_id)
    except FootyStatsError as exc:
        log.error("  ENRICH post %s fs %s: /match fetch failed: %s", post_id, fs_id, exc)
        return "error"

    # -- team stats -----------------------------------------------------
    stat_meta: dict[str, str] = {}
    for fs_key, wp_suffix in TEAM_STAT_MAP.items():
        for pfx, side in (("team_a_", "home_"), ("team_b_", "away_")):
            v = md.get(pfx + fs_key)
            if v is not None:
                stat_meta[side + wp_suffix] = str(v)

    # -- xG -----------------------------------------------------------
    xg_acf: dict[str, Any] = {}
    if md.get("team_a_xg") is not None:
        xg_acf["home_xg"] = md["team_a_xg"]
    if md.get("team_b_xg") is not None:
        xg_acf["away_xg"] = md["team_b_xg"]
    xg_meta: dict[str, str] = {}
    if md.get("team_a_xg_prematch") is not None:
        xg_meta["home_xg_prematch"] = str(md["team_a_xg_prematch"])
    if md.get("team_b_xg_prematch") is not None:
        xg_meta["away_xg_prematch"] = str(md["team_b_xg_prematch"])

    # -- head coach IDs (Phase 2: Head Coach data) -----------------------
    # /match-details carries coach_a_ID/coach_b_ID directly (confirmed via
    # FootyStats' own manager-individual docs, which tie back to these same
    # ids). Stored as plain match meta - resolving them into an actual head
    # coach per club is a separate pass (populate_head_coaches(), below),
    # since a coach can be correct for many matches and we don't want to
    # call /manager once per match.
    coach_meta: dict[str, str] = {}
    if md.get("coach_a_ID") not in (None, -1):
        coach_meta["home_coach_id"] = str(md["coach_a_ID"])
    if md.get("coach_b_ID") not in (None, -1):
        coach_meta["away_coach_id"] = str(md["coach_b_ID"])

    # -- auto-create any missing players ------------------------------
    ymd = _bangkok_ymd(md.get("date_unix") or fs_match.get("date_unix"))
    home_club_wp = club_map.get(int(md.get("homeID", 0) or 0))
    away_club_wp = club_map.get(int(md.get("awayID", 0) or 0))
    for pid in _collect_player_ids(md):
        if str(pid) in lookup:
            continue
        side = _side_of(md, pid)
        club_wp = home_club_wp if side == "Home" else away_club_wp if side == "Away" else None
        ensure_player(
            wp, fs, pid, lookup,
            last_club_wp_id=club_wp, last_date_ymd=ymd, audit=audit,
        )

    # -- raw_match_data --------------------------------------------
    rmd, own_goals = _build_raw_match_data(md, lookup)

    meta = {**stat_meta, **xg_meta, **coach_meta, "raw_match_data": rmd}
    try:
        wp.update_match(post_id, meta=meta, acf=xg_acf or None)
    except WPError as exc:
        log.error("  ENRICH post %s fs %s: write failed: %s", post_id, fs_id, exc)
        return "error"

    n_players = rmd and len(json.loads(base64.b64decode(rmd)).get("player_stats", []))
    audit(
        f"ENRICH  post {post_id}  fs {fs_id}  "
        f"stats={len(stat_meta)}  xg={xg_acf.get('home_xg')}-{xg_acf.get('away_xg')}  "
        f"xg_pre={xg_meta.get('home_xg_prematch')}-{xg_meta.get('away_xg_prematch')}  "
        f"coaches={coach_meta.get('home_coach_id')}-{coach_meta.get('away_coach_id')}  "
        f"players={n_players}  own_goals={own_goals}"
    )

    if not wp.dry_run:
        _verify_enrich_write(wp, post_id, meta, xg_acf)
    return "enriched"


def _bangkok_ymd(unix_ts: Any) -> str:
    try:
        return dt.datetime.fromtimestamp(int(unix_ts), BANGKOK_TZ).strftime("%Y%m%d")
    except (TypeError, ValueError, OSError):
        return ""


def _verify_enrich_write(
    wp: WPClient, post_id: int, meta: dict[str, Any], acf: dict[str, Any]
) -> None:
    """Read the post back and warn loudly if a key did not persist - catches a
    missing register_post_meta() snippet before it hides a whole season of gaps.
    """
    try:
        back = wp.get_match(
            post_id,
            fields="id,meta.raw_match_data,meta.home_xg_prematch,meta.home_corners,acf.home_xg",
        )
    except WPError as exc:
        log.warning("  verify post %s: read-back failed: %s", post_id, exc)
        return
    bmeta = back.get("meta") or {}
    bacf = back.get("acf") or {}
    problems = []
    if meta.get("raw_match_data") and not bmeta.get("raw_match_data"):
        problems.append("raw_match_data")
    if "home_xg_prematch" in meta and not bmeta.get("home_xg_prematch"):
        problems.append("home_xg_prematch (register-match-stats-meta.php not updated?)")
    if "home_corners" in meta and not bmeta.get("home_corners"):
        problems.append("home_corners")
    if acf.get("home_xg") is not None and bacf.get("home_xg") in (None, "", False):
        problems.append("home_xg")
    if problems:
        log.warning("  VERIFY post %s: these keys did NOT persist -> %s",
                    post_id, ", ".join(problems))


# --------------------------------------------------------------------------
# Rebuild raw_match_data for already-enriched posts (logic changed)
# --------------------------------------------------------------------------

def _event_type_counts(rmd_b64: str) -> dict[str, int]:
    try:
        obj = json.loads(base64.b64decode(rmd_b64))
    except Exception:
        return {}
    c: dict[str, int] = {}
    for e in obj.get("match_events", []):
        key = e.get("event_type", "?")
        if e.get("event_detail"):
            key += f"/{e['event_detail']}"
        c[key] = c.get(key, 0) + 1
    return c


def reenrich_raw_match_data(
    wp: WPClient,
    fs: FootyStatsClient,
    post_ids: list[int],
    *,
    audit: Callable[[str], None],
    pause: float = 0.15,
) -> dict[str, Any]:
    """Rebuild ONLY `raw_match_data` for the given `matches` posts from a fresh
    /match fetch (team stats / xG are left alone). Honours wp.dry_run.

    Use after a change to the event-building logic - the normal enrich guard
    skips posts that already have raw_match_data.
    """
    stats = {"posts": len(post_ids), "changed": 0, "unchanged": 0,
             "errors": 0, "verify_failures": 0}
    lookup = build_player_lookup(wp)
    log.info("  player lookup: %d", len(lookup))

    for pid in post_ids:
        try:
            post = wp.get_match(pid, fields="id,title,meta.footystats_match_id,"
                                            "meta.raw_match_data")
        except WPError as exc:
            stats["errors"] += 1
            log.error("  post %s: fetch failed: %s", pid, exc)
            continue
        meta = post.get("meta") or {}
        title = (post.get("title") or {}).get("rendered", "")
        fs_id = meta.get("footystats_match_id")
        if not fs_id:
            stats["errors"] += 1
            log.error("  post %s (%s): no footystats_match_id", pid, title)
            continue
        try:
            md = fs.get_match(int(fs_id))
        except FootyStatsError as exc:
            stats["errors"] += 1
            log.error("  post %s fs %s: /match fetch failed: %s", pid, fs_id, exc)
            continue

        old_rmd = str(meta.get("raw_match_data") or "")
        new_rmd, _ = _build_raw_match_data(md, lookup)
        if new_rmd == old_rmd:
            stats["unchanged"] += 1
            continue

        old_c, new_c = _event_type_counts(old_rmd), _event_type_counts(new_rmd)
        delta = {k: new_c.get(k, 0) - old_c.get(k, 0)
                 for k in set(old_c) | set(new_c)
                 if new_c.get(k, 0) != old_c.get(k, 0)}
        audit(f"REENRICH  post {pid}  fs {fs_id}  {title}  events {old_c} -> {new_c}"
              f"  delta {delta}")
        stats["changed"] += 1
        if not wp.dry_run:
            try:
                wp.update_match(pid, meta={"raw_match_data": new_rmd})
            except WPError as exc:
                stats["errors"] += 1
                log.error("  post %s: write failed: %s", pid, exc)
                continue
            back = wp.get_match(pid, fields="id,meta.raw_match_data")
            if (back.get("meta") or {}).get("raw_match_data") != new_rmd:
                stats["verify_failures"] += 1
                log.warning("  VERIFY post %s: raw_match_data did not persist", pid)
        time.sleep(pause)

    return stats


# --------------------------------------------------------------------------
# Part B  -  club reassignment / clearing
# --------------------------------------------------------------------------

def _first_club_id(v: Any) -> int | None:
    """Normalise an acf.current_club value to a single int WP post id."""
    if not v:
        return None
    x = v[0] if isinstance(v, list) else v
    if isinstance(x, dict):
        x = x.get("id") or x.get("ID")
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _int_or(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _pick_domestic_row(
    rows: list[dict[str, Any]], *, min_end_year: int = 0, min_apps: int = 1
) -> dict[str, Any] | None:
    """From a live /player-stats list, the most recent row that is a *played*,
    *recent* domestic-league season. None -> insufficient evidence, no action.

    Filter (all hard):
      league_type == "Domestic League"   - excludes internationals and every
                                           cup (AFC CL, President's Cup, AFC U23
                                           - whose club_team_id is a select side)
      club_team_id > 0
      appearances_overall >= min_apps    - a roster listing / one-off cameo is
                                           not confirmed evidence
      ending_year >= min_end_year        - a 2-3-season-old appearance is not
                                           evidence of a player's CURRENT club
    Order: (starting_year, ending_year, last_match_timestamp) ascending; take last.
    """
    eligible = [
        r for r in rows
        if isinstance(r, dict)
        and str(r.get("league_type", "")) == "Domestic League"
        and _int_or(r.get("club_team_id")) > 0
        and _int_or(r.get("appearances_overall")) >= min_apps
        and _int_or(r.get("ending_year")) >= min_end_year
    ]
    if not eligible:
        return None
    return sorted(
        eligible,
        key=lambda r: (
            _int_or(r.get("starting_year")),
            _int_or(r.get("ending_year")),
            _int_or(r.get("last_match_timestamp")),
        ),
    )[-1]


def _verify_current_club(
    wp: WPClient, post_id: int, expected: int | None
) -> bool:
    """Read a player back and confirm current_club landed. expected None -> blank."""
    try:
        back = wp.get_player(post_id, fields="id,acf.current_club")
    except WPError as exc:
        log.warning("  verify post %s: read-back failed: %s", post_id, exc)
        return False
    got = _first_club_id((back.get("acf") or {}).get("current_club"))
    return got == expected


def reassign_clubs(
    wp: WPClient,
    fs: FootyStatsClient,
    club_map: dict[int, int],
    *,
    audit: Callable[[str], None],
    sample_limit: int = 10,
    pause: float = 0.1,
    min_end_year: int | None = None,
    min_reassign_apps: int = 3,
    require_profile: bool = True,
) -> dict[str, Any]:
    """Full pass over every `player`. Fetches live /player-stats per player.
    Honours wp.dry_run (no writes when dry).

    min_end_year: ignore domestic-league rows that ended before this year.
      Defaults to (this year - 1) - only the current and immediately previous
      season count as evidence of a player's current club.
    min_reassign_apps: a *reassignment* needs at least this many appearances in
      the decisive row (a one-off cameo is too weak to move current_club).
      Clears use appearances >= 1 (the direction is "blank", lower risk).
    require_profile: skip players with no fs_advanced_stats_json - i.e. the
      Part-A auto-created players, whose current_club is deliberately blank and
      must not be filled from a best-guess most-recent-appearance.
    """
    if min_end_year is None:
        min_end_year = dt.date.today().year - 1
    log.info("  config: ending_year >= %d, reassign needs apps >= %d, "
             "require fs_advanced_stats_json = %s",
             min_end_year, min_reassign_apps, require_profile)
    stats: dict[str, Any] = {
        "scanned": 0, "no_footystats_id": 0, "skipped_no_profile": 0,
        "reassigned": 0, "cleared": 0, "noop": 0,
        "insufficient": 0, "thin_reassign_skipped": 0, "bad_fs_record": 0,
        "errors": 0, "verify_failures": 0,
        "reassign_sample": [], "clear_sample": [],
        "noop_untracked_sample": [], "insufficient_sample": [],
        "thin_sample": [], "bad_sample": [],
        "decisions": [],  # one row per considered player, for offline analysis
    }

    for p in wp.iter_all_players(
        fields="id,title,acf.footystats_id,acf.current_club,acf.fs_advanced_stats_json"
    ):
        stats["scanned"] += 1
        acf = p.get("acf") or {}
        pid = int(p["id"])
        name = (p.get("title") or {}).get("rendered", "") or f"player {pid}"
        fs_id = acf.get("footystats_id")
        if fs_id in (None, "", 0, "0"):
            stats["no_footystats_id"] += 1
            continue
        has_profile = bool(str(acf.get("fs_advanced_stats_json") or "").strip())
        if require_profile and not has_profile:
            stats["skipped_no_profile"] += 1
            continue

        try:
            rows = fs.get_player(int(fs_id))
        except FootyStatsError as exc:
            stats["errors"] += 1
            log.error("  club-sync post %s (%s) fs=%s: /player-stats failed: %s",
                      pid, name, fs_id, exc)
            continue

        # Sanity: a valid player id returns rows that carry a name. Bogus /
        # deleted ids (e.g. WP "Player 42458") come back as an all-empty-name
        # grab-bag with impossible per-season appearance counts - never act on it.
        if not any(str(r.get("known_as") or r.get("full_name") or "").strip()
                   for r in rows):
            stats["bad_fs_record"] += 1
            line = (f"BAD-FS-RECORD  {name}  (post {pid})  fs={fs_id}  "
                    f"- /player-stats has no named rows; no action, needs manual review")
            audit(line)
            if len(stats["bad_sample"]) < sample_limit:
                stats["bad_sample"].append(line)
            time.sleep(pause)
            continue

        row = _pick_domestic_row(rows, min_end_year=min_end_year, min_apps=1)
        cur_wp = _first_club_id(acf.get("current_club"))

        if row is None:
            stats["insufficient"] += 1
            line = (f"NO-EVIDENCE  {name}  (post {pid})  fs={fs_id}  "
                    f"current_club={cur_wp if cur_wp else '(blank)'}  "
                    f"- no played Domestic-League season ending >= {min_end_year}; "
                    f"no action")
            audit(line)
            if len(stats["insufficient_sample"]) < sample_limit:
                stats["insufficient_sample"].append(line)
            time.sleep(pause)
            continue

        fs_club = _int_or(row.get("club_team_id"))
        mapped_wp = club_map.get(fs_club)
        apps = _int_or(row.get("appearances_overall"))
        ctx = (f"fs club {fs_club}, {row.get('season')} {row.get('league')}, "
               f"apps={apps}")
        stats["decisions"].append({
            "post": pid, "name": name, "fs_id": fs_id,
            "cur_wp": cur_wp, "fs_club": fs_club, "mapped_wp": mapped_wp,
            "season": row.get("season"), "league": row.get("league"),
            "end_year": _int_or(row.get("ending_year")), "apps": apps,
        })

        try:
            if mapped_wp is not None:
                if cur_wp == mapped_wp:
                    stats["noop"] += 1
                elif apps < min_reassign_apps:
                    stats["thin_reassign_skipped"] += 1
                    line = (f"THIN-EVIDENCE  {name}  (post {pid})  "
                            f"would move to {wp.get_club_title(mapped_wp)} [{mapped_wp}] "
                            f"but only {ctx}; no action")
                    audit(line)
                    if len(stats["thin_sample"]) < sample_limit:
                        stats["thin_sample"].append(line)
                else:
                    old_txt = wp.get_club_title(cur_wp) if cur_wp else "(blank)"
                    new_txt = wp.get_club_title(mapped_wp)
                    line = (f"REASSIGN  {name}  (post {pid})  "
                            f"{old_txt} [{cur_wp}]  ->  {new_txt} [{mapped_wp}]  ({ctx})")
                    audit(line)
                    if len(stats["reassign_sample"]) < sample_limit:
                        stats["reassign_sample"].append(line)
                    if not wp.dry_run:
                        wp.update_player(pid, acf={"current_club": [mapped_wp]})
                        if not _verify_current_club(wp, pid, mapped_wp):
                            stats["verify_failures"] += 1
                            log.warning("  VERIFY post %s: current_club did NOT "
                                        "land as %s", pid, mapped_wp)
                    stats["reassigned"] += 1
            else:
                if cur_wp is None:
                    stats["noop"] += 1
                    if len(stats["noop_untracked_sample"]) < sample_limit:
                        stats["noop_untracked_sample"].append(
                            f"{name} (post {pid}): already blank; {ctx} not tracked")
                else:
                    old_txt = wp.get_club_title(cur_wp)
                    line = (f"CLEAR     {name}  (post {pid})  "
                            f"was {old_txt} [{cur_wp}]  ->  (blank)  "
                            f"(moved to untracked {ctx})")
                    audit(line)
                    if len(stats["clear_sample"]) < sample_limit:
                        stats["clear_sample"].append(line)
                    if not wp.dry_run:
                        # the ACF relationship field enforces "min 1 post", so an
                        # empty array is rejected (rest_too_few_items) - null clears it.
                        wp.update_player(pid, acf={"current_club": None})
                        if not _verify_current_club(wp, pid, None):
                            stats["verify_failures"] += 1
                            log.warning("  VERIFY post %s: current_club did NOT "
                                        "clear", pid)
                    stats["cleared"] += 1
        except WPError as exc:
            stats["errors"] += 1
            log.error("  club-sync post %s (%s): %s", pid, name, exc)

        time.sleep(pause)

    return stats


# --------------------------------------------------------------------------
# Player stats population (new 2026-09-22 - fills the previously-unused
# fs_advanced_stats_json field, closing the gap between what FootyStats
# already provides via FootyStatsClient.get_player() / /player-stats and
# what was actually being stored)
# --------------------------------------------------------------------------

def _build_all_seasons_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Groups raw /player-stats rows (one row per season+competition) into
    the season-keyed shape the Player Pages front end actually reads
    (fs_player_advanced_stats / fs_player_match_log in the "FS Player Pages
    - MASTER ENGINE" snippet, via its fs_p_aggregate_season_metrics() PHP
    helper): one entry per season, keyed by FootyStats' own `season` string,
    with counting stats (appearances/minutes/goals/assists/conceded/clean
    sheets, overall+home+away, plus cards/penalties) summed across every
    competition played that season, and the richer per-90/percentile
    `detailed` block kept from whichever competition-row for that season has
    the most appearances (FootyStats only returns detailed metrics for some
    competitions/seasons - older or lower-tier ones often come back
    detailed: null, so this picks the row most likely to have it).

    2026-09-22 fix: the original populate_player_stats() stored the raw FootyStats
    rows flat, under a top-level "rows" key. The front-end code was already
    built (before this pass) expecting "all_seasons_metrics" as a
    season-keyed dict - so despite real data being written, the site showed
    "No seasonal analysis metrics recorded" for every player. This function
    is the fix: it derives that expected shape from the same raw rows,
    which are still kept in the payload under "rows" too (nothing is
    discarded, this is purely additive).
    """
    sum_keys = [
        "appearances_overall", "minutes_played_overall", "goals_overall", "assists_overall",
        "conceded_overall", "clean_sheets_overall",
        "appearances_home", "minutes_played_home", "goals_home", "assists_home",
        "conceded_home", "clean_sheets_home",
        "appearances_away", "minutes_played_away", "goals_away", "assists_away",
        "conceded_away", "clean_sheets_away",
        "yellow_cards_overall", "red_cards_overall", "penalty_misses", "penalty_goals",
    ]
    # FootyStats' /player-stats response has a real, confirmed data-quality
    # quirk: it sometimes repeats the exact same season+competition row two
    # or three times verbatim (same competition_id, club_team_id, every stat
    # identical). Summing those as if they were separate competitions would
    # silently triple-count a player's goals/apps for that season. Dedupe on
    # a stable identity key before aggregating - real multi-competition
    # seasons (different competition_id) are untouched.
    seen_rows: set[tuple[Any, Any, Any, Any]] = set()
    deduped_rows: list[dict[str, Any]] = []
    for row in rows:
        ident = (row.get("season"), row.get("competition_id"), row.get("club_team_id"),
                 row.get("appearances_overall"))
        if ident in seen_rows:
            continue
        seen_rows.add(ident)
        deduped_rows.append(row)

    by_season: dict[str, dict[str, Any]] = {}
    for row in deduped_rows:
        season = row.get("season")
        if not season:
            continue
        if season not in by_season:
            # Seed with a full copy of this row - carries every field FootyStats
            # sent (age, nationality, position, and any top-level advanced
            # metrics), matching how the PHP aggregator seeds on first sight.
            by_season[season] = dict(row)
            continue
        existing = by_season[season]
        for key in sum_keys:
            existing[key] = int(existing.get(key) or 0) + int(row.get(key) or 0)
        pg = int(existing.get("penalty_goals") or 0)
        pm = int(existing.get("penalty_misses") or 0)
        if (pg + pm) > 0:
            existing["penalty_success"] = round((pg / (pg + pm)) * 100)
        # Prefer whichever single competition-row for this season actually
        # carries the richer 'detailed' block, favouring the one with more
        # appearances - same tie-break the PHP side already used.
        if not existing.get("detailed") and row.get("detailed"):
            existing["detailed"] = row["detailed"]
        elif (row.get("appearances_overall") or 0) > (existing.get("appearances_overall") or 0) \
                and row.get("detailed"):
            existing["detailed"] = row["detailed"]
    return by_season


def _pick_latest_season_key(all_seasons_metrics: dict[str, Any]) -> str | None:
    """Picks the most recent season key by parsing the leading year out of
    each raw FootyStats season string (e.g. "2026/2027" -> 2026, "2020" ->
    2020). Ties (shouldn't happen - keys are unique) fall back to string
    order."""
    if not all_seasons_metrics:
        return None

    def _year(key: str) -> int:
        digits = "".join(ch for ch in key[:4] if ch.isdigit())
        return int(digits) if digits else 0

    return max(all_seasons_metrics.keys(), key=lambda k: (_year(k), k))


def populate_player_stats(
    wp: WPClient,
    fs: FootyStatsClient,
    *,
    audit: Callable[[str], None],
    sample_limit: int = 10,
    pause: float = 0.1,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Full pass over every `player`. Fetches live /player-stats per player
    (the same endpoint reassign_clubs() already calls, but this time keeps
    the whole row list - not just club_team_id/appearances_overall) and
    stores it as JSON in fs_advanced_stats_json.

    Deliberately independent from reassign_clubs(): same data source, but
    writes a different field and carries no reassignment risk. Honours
    wp.dry_run (no writes when dry).

    Each row is one season+competition the player appears in (per FootyStats'
    own shape - not deduplicated or restructured here, so nothing about the
    original data is lost or reinterpreted). Downstream code (player page
    rendering, reassign_clubs' require_profile gate) picks what it needs.

    skip_existing (default True): skip any player that already has a
    non-empty fs_advanced_stats_json. FootyStats caps requests at 1,800/hour
    (2026-09-22: a full run over ~2,200 players hit that cap partway through
    and the remaining players 429'd for the rest of the run). This flag is
    what makes repeated runs converge on full coverage instead of re-fetching
    the same first ~1,800 players every time - a second run an hour later
    picks up exactly where the first stopped. Pass skip_existing=False to
    force a full refresh of every player regardless of what's already stored.
    """
    log.info("  config: storing full /player-stats row list per player "
             "(no filtering - identical shape to what FootyStats returns); "
             "skip_existing=%s", skip_existing)
    stats: dict[str, Any] = {
        "scanned": 0, "no_footystats_id": 0, "already_had_profile": 0,
        "updated": 0, "no_rows": 0, "bad_fs_record": 0, "errors": 0,
        "rate_limited_stop": False, "update_sample": [],
    }
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    fields = "id,title,acf.footystats_id"
    if skip_existing:
        fields += ",acf.fs_advanced_stats_json"

    for p in wp.iter_all_players(fields=fields):
        stats["scanned"] += 1
        acf = p.get("acf") or {}
        pid = int(p["id"])
        name = (p.get("title") or {}).get("rendered", "") or f"player {pid}"
        fs_id = acf.get("footystats_id")
        if fs_id in (None, "", 0, "0"):
            stats["no_footystats_id"] += 1
            continue

        if skip_existing and str(acf.get("fs_advanced_stats_json") or "").strip():
            stats["already_had_profile"] += 1
            continue

        try:
            rows = fs.get_player(int(fs_id))
        except FootyStatsError as exc:
            # The FootyStats client already retries a 429 with backoff and
            # only raises once those retries are exhausted - at that point
            # the hourly quota (1,800 req) is genuinely gone for up to an
            # hour, so hammering every remaining player with more retries
            # just burns CI minutes on guaranteed failures. Stop cleanly
            # instead: what's already written stands, and (with
            # skip_existing, the default) the next run picks up here.
            if "429" in str(exc) or "Rate limit" in str(exc):
                stats["rate_limited_stop"] = True
                log.warning("  RATE LIMIT reached at post %s (%s) - stopping "
                            "this run cleanly rather than retrying every "
                            "remaining player. %d written this run; re-run "
                            "(skip_existing=%s) once the hourly quota resets "
                            "to continue from here.",
                            pid, name, stats["updated"], skip_existing)
                break
            stats["errors"] += 1
            log.error("  player-stats post %s (%s) fs=%s: /player-stats failed: %s",
                      pid, name, fs_id, exc)
            time.sleep(pause)
            continue

        # Same sanity check as reassign_clubs() - a bogus/deleted FootyStats
        # id comes back as an all-empty-name grab-bag; never store that.
        if not any(str(r.get("known_as") or r.get("full_name") or "").strip()
                   for r in rows):
            stats["bad_fs_record"] += 1
            line = (f"BAD-FS-RECORD  {name}  (post {pid})  fs={fs_id}  "
                    f"- /player-stats has no named rows; skipped, needs manual review")
            audit(line)
            if len(stats["update_sample"]) < sample_limit:
                stats["update_sample"].append(line)
            time.sleep(pause)
            continue

        if not rows:
            stats["no_rows"] += 1
            time.sleep(pause)
            continue

        all_seasons_metrics = _build_all_seasons_metrics(rows)
        latest_key = _pick_latest_season_key(all_seasons_metrics)
        latest_season_metrics = all_seasons_metrics.get(latest_key, {}) if latest_key else {}

        # bio: cheap top-level fallbacks the front end already knows how to
        # use when a player's own ACF fields are empty (see fs_p_age_dob(),
        # fs_player_top_bar/bio_bar in the Player Pages snippet). Pulled from
        # whichever row is most likely to carry them; every one of these has
        # a working ACF-field fallback already, so this is a nice-to-have,
        # not load-bearing.
        bio_source = rows[0] if rows else {}
        bio = {
            k: v for k, v in {
                "birthday": bio_source.get("birthday"),
                "height": bio_source.get("height"),
                "shirt_number": (bio_source.get("detailed") or {}).get("shirt_number"),
            }.items() if v not in (None, "", 0)
        }

        payload = json.dumps({
            "synced_at": now_iso,
            "rows": rows,
            "all_seasons_metrics": all_seasons_metrics,
            "latest_season_metrics": latest_season_metrics,
            "bio": bio,
        }, separators=(",", ":"))
        try:
            if not wp.dry_run:
                wp.update_player(pid, acf={"fs_advanced_stats_json": payload})
            stats["updated"] += 1
            if len(stats["update_sample"]) < sample_limit:
                stats["update_sample"].append(
                    f"STATS  {name}  (post {pid})  fs={fs_id}  "
                    f"{len(rows)} row(s), {len(payload)} bytes")
        except WPError as exc:
            stats["errors"] += 1
            log.error("  player-stats post %s (%s): write failed: %s", pid, name, exc)

        time.sleep(pause)

    return stats


# --------------------------------------------------------------------------
# Head coach population (Phase 2, new 2026-09-22 - resolves each club's
# current head coach from captured match-level coach ids, then fetches and
# stores the coach's managerial record from FootyStats' /manager endpoint)
# --------------------------------------------------------------------------

def _club_ids(meta: dict[str, Any]) -> tuple[int | None, int | None]:
    """(home_wp_club_id, away_wp_club_id) from a match post's meta. Both are
    plain string-encoded WP post ids (see sync_matches.py's match-creation
    meta, e.g. "home_team": str(home_wp)) - not a serialized array or ACF
    relationship object.
    """
    def _pid(v: Any) -> int | None:
        try:
            iv = int(str(v).strip())
        except (TypeError, ValueError):
            return None
        return iv or None
    return _pid(meta.get("home_team")), _pid(meta.get("away_team"))


def _manager_row_name(row: dict[str, Any]) -> str:
    """Best-effort display name from one /manager row. Live-verified
    2026-09-22: real rows carry both "known_as" and "full_name" (e.g.
    "Worrawoot Srimaka" for manager id 21976) - the extra fallbacks are
    just defensive.
    """
    for k in ("known_as", "full_name", "manager_name", "name"):
        v = row.get(k)
        if v:
            return str(v).strip()
    return ""


def ensure_head_coach(
    wp: WPClient,
    coach_id: int,
    rows: list[dict[str, Any]],
    coach_name: str,
    payload: str,
    club_wp_id: int,
    *,
    index: dict[int, dict[str, Any]],
    audit: Callable[[str], None],
) -> int | None:
    """Create or update the `head_coach` WP post for one resolved FootyStats
    manager id (mirrors ensure_player()'s auto-create pattern). `index` -
    from wp.index_head_coaches_by_manager_id(), built once by the caller -
    is kept in sync so later lookups within the same run see a coach just
    created. Returns the WP post id, or None in dry-run (nothing is
    actually created then) or if the CPT/REST meta isn't registered yet
    (Code Snippet 143 not active - treated as a soft no-op, not an error,
    since fs_head_coach_json/head_coach on the club post still get written
    either way).
    """
    nat_row = next((r for r in rows if str(r.get("nationality") or "").strip()),
                    rows[0] if rows else {})
    nationality = str(nat_row.get("nationality") or "").strip()
    birthday = nat_row.get("birthday") or 0
    meta = {
        "footystats_manager_id": str(coach_id),
        "fs_head_coach_json": payload,
        "current_club": str(club_wp_id),
    }
    if nationality:
        meta["nationality"] = nationality
    if birthday:
        meta["birthday"] = str(int(birthday))

    existing = index.get(coach_id)
    if existing is not None:
        pid = int(existing["id"])
        if not wp.dry_run:
            wp.update_head_coach(pid, meta=meta)
        return pid

    resp = wp.create_head_coach({
        "title": coach_name,
        "status": "publish",
        "meta": meta,
    })
    new_id = None if wp.dry_run else int(resp.get("id"))
    audit(f"HEAD COACH CREATE  {coach_name}  fs={coach_id}  "
          f"wp={new_id if new_id else '(dry-run)'}  club={club_wp_id}")
    if new_id is not None:
        index[coach_id] = {"id": new_id, "title": {"rendered": coach_name}}
    return new_id


def populate_head_coaches(
    wp: WPClient,
    fs: FootyStatsClient,
    *,
    audit: Callable[[str], None],
    sample_limit: int = 10,
    pause: float = 0.1,
) -> dict[str, Any]:
    """Two-pass. Honours wp.dry_run (no writes when dry).

    Pass 1: scan wp.iter_all_matches() once to find, per WP club id, the
    most recent match (by the "date" meta - YYYYMMDD string, safe to sort
    lexically) carrying a captured home_coach_id/away_coach_id for that
    side. "Most recent evidence wins" - same pattern reassign_clubs() uses
    for current_club, but keyed off our own match CPT meta rather than
    FootyStats' own team-stats blob, since coach ids are only ever captured
    match-by-match (see enrich_match()'s coach_meta block).

    Coverage note: only matches enriched AFTER the coach_meta capture
    shipped (2026-09-22) carry home_coach_id/away_coach_id -
    enrich_match() never re-enriches an already-enriched match
    (raw_match_data guard), so this will NOT retroactively resolve coaches
    from historical matches. Coverage grows as new matches are played and
    enriched; a one-off backfill re-enrich pass could close that gap later
    if wanted, but is out of scope here.

    Pass 2: for each club, resolve its coach id from pass 1, call
    fs.get_manager() (endpoint + coach_a_ID/coach_b_ID field names and the
    /manager row shape all live-verified 2026-09-22), apply the same
    bad-record sanity check and 429-clean-stop pattern as
    populate_player_stats(), and write:
      - fs_head_coach_json: the full /manager row list, as plain WP post
        META (deliberately NOT acf - unlike fs_team_stats_json /
        fs_advanced_stats_json, no fs_head_coach_json ACF field has been
        registered on the club CPT yet, and post meta needs no such
        registration to write or to read back via get_post_meta() /
        get_field()'s meta fallback - same approach already used for
        raw_match_data on the matches CPT).
      - head_coach: the coach's best-effort display name into the existing
        plain-text ACF field already rendered by fs_club_top_bar() in the
        Club Pages Master Engine (get_field('head_coach', $cid) ?: 'TBA') -
        so this one pass makes that already-built UI show real names with
        no front-end changes needed.
      - head_coach_post_id: the WP `head_coach` post id, via
        ensure_head_coach() (Phase 3, mirrors ensure_player()'s auto-create
        pattern) - the relational link, club -> coach. The coach's own post
        stores current_club (WP club post id) as the reverse link.

    Every meta field this writes - on `matches`, `club`, and `head_coach` -
    needs an explicit register_post_meta(..., show_in_rest: true) call to
    actually persist via the REST API the Python pipeline uses (an
    unregistered key is silently dropped, no error - found the hard way
    2026-09-22). See Code Snippets 142 (matches/club fields) and 143 (the
    head_coach CPT + its own fields) - BOTH must be active in wp-admin
    before this function's writes do anything at all.

    Labeling: always "Head Coach" in any UI-facing text this pass touches
    or documents - never "Manager" (Simon's explicit naming decision,
    2026-09-22) - even though the underlying FootyStats endpoint and field
    names are /manager, manager_id, etc.
    """
    stats: dict[str, Any] = {
        "matches_scanned": 0, "clubs_with_evidence": 0,
        "clubs_scanned": 0, "no_evidence": 0, "bad_coach_id": 0,
        "updated": 0, "no_rows": 0, "bad_fs_record": 0, "errors": 0,
        "rate_limited_stop": False, "update_sample": [], "no_evidence_sample": [],
    }
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hc_index = wp.index_head_coaches_by_manager_id()
    log.info("  head_coach index: %d existing coach post(s)", len(hc_index))

    # -- Pass 1: most-recent coach id per WP club id ---------------------
    best: dict[int, tuple[str, str]] = {}  # wp_club_id -> (date, coach_id)
    for m in wp.iter_all_matches():
        stats["matches_scanned"] += 1
        meta = m.get("meta") or {}
        date = str(meta.get("date") or "").strip()
        if not date:
            continue
        home_id, away_id = _club_ids(meta)
        home_coach = str(meta.get("home_coach_id") or "").strip()
        away_coach = str(meta.get("away_coach_id") or "").strip()
        if home_id and home_coach:
            cur = best.get(home_id)
            if cur is None or date > cur[0]:
                best[home_id] = (date, home_coach)
        if away_id and away_coach:
            cur = best.get(away_id)
            if cur is None or date > cur[0]:
                best[away_id] = (date, away_coach)
    stats["clubs_with_evidence"] = len(best)
    log.info("  pass 1: %d matches scanned, %d clubs have a captured coach id",
             stats["matches_scanned"], stats["clubs_with_evidence"])

    # -- Pass 2: resolve + fetch + write ---------------------------------
    for c in wp.iter_all_clubs(fields="id,title,acf.head_coach"):
        stats["clubs_scanned"] += 1
        pid = int(c["id"])
        name = (c.get("title") or {}).get("rendered", "") or f"club {pid}"

        evidence = best.get(pid)
        if evidence is None:
            stats["no_evidence"] += 1
            if len(stats["no_evidence_sample"]) < sample_limit:
                stats["no_evidence_sample"].append(
                    f"{name} (post {pid}): no captured coach id yet "
                    f"(needs a newly-enriched match)")
            continue

        _, coach_id_str = evidence
        try:
            coach_id = int(coach_id_str)
        except ValueError:
            stats["bad_coach_id"] += 1
            continue

        try:
            rows = fs.get_manager(coach_id)
        except FootyStatsError as exc:
            if "429" in str(exc) or "Rate limit" in str(exc):
                stats["rate_limited_stop"] = True
                log.warning("  RATE LIMIT reached at club %s (%s) - stopping "
                            "this run cleanly. %d written this run; re-run "
                            "once the hourly quota resets to continue.",
                            pid, name, stats["updated"])
                break
            stats["errors"] += 1
            log.error("  head-coach post %s (%s) coach=%s: /manager failed: %s",
                      pid, name, coach_id, exc)
            time.sleep(pause)
            continue

        if not rows:
            stats["no_rows"] += 1
            time.sleep(pause)
            continue

        # Same sanity check as populate_player_stats()/reassign_clubs() - a
        # bogus id shouldn't come back with named rows; never store that.
        if not any(_manager_row_name(r) for r in rows):
            stats["bad_fs_record"] += 1
            line = (f"BAD-FS-RECORD  {name}  (post {pid})  coach={coach_id}  "
                    f"- /manager has no named rows; skipped, needs manual review")
            audit(line)
            if len(stats["update_sample"]) < sample_limit:
                stats["update_sample"].append(line)
            time.sleep(pause)
            continue

        coach_name = next((n for n in (_manager_row_name(r) for r in rows) if n), "")

        payload = json.dumps({
            "synced_at": now_iso,
            "manager_id": coach_id,
            "rows": rows,
        }, separators=(",", ":"))

        hc_post_id = ensure_head_coach(
            wp, coach_id, rows, coach_name, payload, pid,
            index=hc_index, audit=audit,
        )

        club_meta = {"fs_head_coach_json": payload}
        if hc_post_id is not None:
            club_meta["head_coach_post_id"] = str(hc_post_id)

        try:
            if not wp.dry_run:
                wp.update_club(
                    pid,
                    meta=club_meta,
                    acf={"head_coach": coach_name} if coach_name else None,
                )
            stats["updated"] += 1
            if len(stats["update_sample"]) < sample_limit:
                stats["update_sample"].append(
                    f"HEAD COACH  {name}  (post {pid})  coach={coach_id}  "
                    f"\"{coach_name}\"  hc_post={hc_post_id}  "
                    f"{len(rows)} row(s), {len(payload)} bytes")
        except WPError as exc:
            stats["errors"] += 1
            log.error("  head-coach post %s (%s): write failed: %s", pid, name, exc)

        time.sleep(pause)

    return stats


def backfill_coach_ids(
    wp: WPClient,
    fs: FootyStatsClient,
    *,
    audit: Callable[[str], None],
    pause: float = 0.1,
) -> dict[str, Any]:
    """One-time backfill (Phase 2 follow-up, 2026-09-22): for every already-
    enriched `matches` post missing home_coach_id/away_coach_id, re-fetch
    /match and write just those two meta fields - raw_match_data / team
    stats / xG are left alone (same "re-fetch, write a narrow slice" shape
    as reenrich_raw_match_data(), but for coach ids instead).

    Without this, populate_head_coaches() only has evidence from matches
    enriched AFTER the coach_meta capture shipped, so coverage would grow
    one match at a time as the season progresses. Running this once gives
    immediate coverage across every match already on the site (~1,613 as of
    2026-09-22 - one FootyStats request each, well inside the 1,800/hour
    cap for a single run, but stops cleanly on a 429 exactly like
    populate_player_stats() in case the quota has already been spent on
    something else this hour).
    """
    stats: dict[str, Any] = {
        "scanned": 0, "already_had_coach_meta": 0, "no_footystats_id": 0,
        "updated": 0, "no_coach_data": 0, "errors": 0,
        "rate_limited_stop": False,
    }
    for post in wp.iter_all_matches():
        stats["scanned"] += 1
        meta = post.get("meta") or {}
        pid = int(post["id"])
        title = (post.get("title") or {}).get("rendered", "") or f"match {pid}"

        if str(meta.get("home_coach_id") or "").strip() or str(meta.get("away_coach_id") or "").strip():
            stats["already_had_coach_meta"] += 1
            continue

        fs_id = meta.get("footystats_match_id")
        if not fs_id:
            stats["no_footystats_id"] += 1
            continue

        try:
            md = fs.get_match(int(fs_id))
        except FootyStatsError as exc:
            if "429" in str(exc) or "Rate limit" in str(exc):
                stats["rate_limited_stop"] = True
                log.warning("  RATE LIMIT reached at post %s (%s) - stopping "
                            "this run cleanly. %d written this run; re-run "
                            "to continue.", pid, title, stats["updated"])
                break
            stats["errors"] += 1
            log.error("  coach-backfill post %s (%s) fs=%s: /match fetch failed: %s",
                      pid, title, fs_id, exc)
            time.sleep(pause)
            continue

        coach_meta: dict[str, str] = {}
        if md.get("coach_a_ID") not in (None, -1):
            coach_meta["home_coach_id"] = str(md["coach_a_ID"])
        if md.get("coach_b_ID") not in (None, -1):
            coach_meta["away_coach_id"] = str(md["coach_b_ID"])

        if not coach_meta:
            stats["no_coach_data"] += 1
            time.sleep(pause)
            continue

        try:
            if not wp.dry_run:
                wp.update_match(pid, meta=coach_meta)
            stats["updated"] += 1
        except WPError as exc:
            stats["errors"] += 1
            log.error("  coach-backfill post %s (%s): write failed: %s", pid, title, exc)

        time.sleep(pause)

    return stats
