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
        "position": position,
    }
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

    placeholders = {
        "Home": _placeholder_ids(md, "team_a"),
        "Away": _placeholder_ids(md, "team_b"),
    }

    def slot(pid: Any, team: str) -> tuple[Any, str, str]:
        """(wp_id, player_name, position) for a lineup/event slot. A detected
        placeholder id resolves to Unknown with no lookup."""
        if pid in placeholders.get(team, ()):
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
        events.append({
            "event_type": et, "event_detail": detail, "event_minute": mn,
            "p1_id": pid, "p1_wp_id": w, "p1_name": nm,
            "event_team": team,
        })

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

    meta = {**stat_meta, **xg_meta, "raw_match_data": rmd}
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
