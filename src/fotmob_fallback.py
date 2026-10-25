"""FotMob fallback for late scores (Simon, 26 Oct 2026).

"If we don't have results by 11pm every night we should be using FotMob as a
backup." On 25 Oct FootyStats still had no score for Uthai Thani v Sukhothai at
02:00, although the sync ran every 10 minutes, so the results card was held.
FotMob had it as full time (1-2) all along.

How it works (called at the end of every sync_matches.sync() run):

  * Only from 23:00 Bangkok, for that day's games, and until 12:00 the next
    morning for the previous day's games (the Watcher keeps dispatching the sync
    every 10 minutes until 02:00 and hourly after that; the 03:00 schedule also
    runs).
  * A game qualifies when its WP post still has no score, no match_status
    (postponed etc.), and kicked off at least 2h15m ago.
  * The game is found on FotMob by date and FotMob team id (from
    fotmob_squads.FOTMOB_TEAMS, keyed by WP club post id), never by name. At
    least one side must be mapped, every mapped side must match, the kick-off
    must be within 3 hours of ours, and exactly one FotMob game may match.
  * Only a FotMob game marked finished (FT / AET / Pen) and not cancelled gives a
    score. home.score / away.score is the score after extra time without
    penalties, the site's convention for a drawn tie.
  * Writes home_score / away_score (and match_week unchanged), then
    score_source='fotmob' and fotmob_match_id. The last two need Snippet 205
    (wordpress/score-source-meta.php); until it is active WordPress drops them
    silently and only the score is kept.

Once a post has a score the main sync never re-scores it, so FootyStats can't
overwrite the FotMob score. check_clash() is called by the sync for every
already-scored post: if score_source is 'fotmob' and FootyStats now says
something different, it writes score_clash, and Snippet 205 emails Simon. The
site score is left alone (Simon, 26 Oct 2026: "keep FotMob, email me").
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("fotmob_fallback")

BKK = ZoneInfo("Asia/Bangkok")
FM_MATCHES = "https://www.fotmob.com/api/data/matches"
START_HOUR = 23          # from 23:00 Bangkok on match day
MORNING_END_HOUR = 12    # ... until 12:00 the next morning
MIN_AFTER_KO = dt.timedelta(minutes=135)
KO_TOLERANCE = dt.timedelta(hours=3)
FINISHED = {"FT", "AET", "AP", "PEN", "PENS"}


def _wp_to_fotmob() -> dict[int, int]:
    from fotmob_squads import FOTMOB_TEAMS  # lazy: fotmob_squads imports sync_matches
    return {int(wp): int(fm) for fm, wp, _fs in FOTMOB_TEAMS.values()}


def candidate_dates(now: dt.datetime) -> list[str]:
    """Bangkok YYYYMMDD dates whose missing scores may be taken from FotMob now."""
    now = now.astimezone(BKK)
    out = []
    if now.hour >= START_HOUR:
        out.append(now.strftime("%Y%m%d"))
    if now.hour < MORNING_END_HOUR:
        out.append((now - dt.timedelta(days=1)).strftime("%Y%m%d"))
    return out


def _ko(meta: dict[str, Any]) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(
            f"{meta.get('date')} {str(meta.get('kick_off_time') or '').strip()}", "%Y%m%d %H:%M"
        ).replace(tzinfo=BKK)
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def fotmob_day(yyyymmdd: str, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """Every FotMob match on that Bangkok date (all leagues, flattened)."""
    s = session or requests.Session()
    r = s.get(FM_MATCHES, params={"date": yyyymmdd, "timezone": "Asia/Bangkok"},
              headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    out = []
    for lg in r.json().get("leagues") or []:
        for m in lg.get("matches") or []:
            m["_league"] = lg.get("name")
            out.append(m)
    return out


def _finished_score(m: dict[str, Any]) -> tuple[int, int] | None:
    st = m.get("status") or {}
    short = str((st.get("reason") or {}).get("short") or "").upper().replace(".", "")
    if not st.get("finished") or st.get("cancelled") or short not in FINISHED:
        return None
    hs, as_ = _int((m.get("home") or {}).get("score")), _int((m.get("away") or {}).get("score"))
    if hs is None or as_ is None:
        return None
    return hs, as_


def _fm_ko(m: dict[str, Any]) -> dt.datetime | None:
    raw = (m.get("status") or {}).get("utcTime")
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def find_fotmob_match(meta: dict[str, Any], day: list[dict[str, Any]],
                      wp2fm: dict[int, int]) -> tuple[dict[str, Any] | None, str]:
    """The single FotMob game for this WP post, or (None, reason)."""
    fh = wp2fm.get(_int(meta.get("home_team")) or -1)
    fa = wp2fm.get(_int(meta.get("away_team")) or -1)
    if fh is None and fa is None:
        return None, "neither club has a FotMob id"
    ko = _ko(meta)
    hits = []
    for m in day:
        if fh is not None and (m.get("home") or {}).get("id") != fh:
            continue
        if fa is not None and (m.get("away") or {}).get("id") != fa:
            continue
        mk = _fm_ko(m)
        if ko and mk and abs(mk - ko) > KO_TOLERANCE:
            continue
        hits.append(m)
    if len(hits) != 1:
        return None, f"{len(hits)} FotMob matches fit"
    return hits[0], "ok"


def run(wp, index: dict[int, dict[str, Any]], *, now: dt.datetime | None = None,
        audit: Callable[[str], None] = log.info,
        fetch: Callable[[str], list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """Fill missing scores from FotMob. Returns one dict per score written."""
    now = (now or dt.datetime.now(BKK)).astimezone(BKK)
    dates = candidate_dates(now)
    if not dates:
        log.info("FotMob fallback: not active before %02d:00 Bangkok", START_HOUR)
        return []

    pending = []
    for fs_id, post in index.items():
        meta = post.get("meta") or {}
        if str(meta.get("date")) not in dates:
            continue
        if str(meta.get("home_score") or "").strip() or str(meta.get("match_status") or "").strip():
            continue
        ko = _ko(meta)
        if ko is None or now - ko < MIN_AFTER_KO:
            continue
        pending.append((fs_id, post))
    if not pending:
        log.info("FotMob fallback: nothing missing for %s", ", ".join(dates))
        return []

    log.info("FotMob fallback: %d game(s) still without a score", len(pending))
    wp2fm = _wp_to_fotmob()
    fetch = fetch or fotmob_day
    days: dict[str, list[dict[str, Any]] | None] = {}
    written = []
    for fs_id, post in pending:
        meta = post.get("meta") or {}
        title = (post.get("title") or {}).get("rendered", "")
        d = str(meta["date"])
        if d not in days:
            try:
                days[d] = fetch(d)
            except (requests.RequestException, ValueError) as exc:
                log.warning("  FotMob fetch for %s failed: %s", d, exc)
                days[d] = None
        if days[d] is None:
            continue
        m, why = find_fotmob_match(meta, days[d], wp2fm)
        if m is None:
            log.info("  %s (post %s): no FotMob match (%s)", title, post["id"], why)
            continue
        score = _finished_score(m)
        if score is None:
            short = ((m.get("status") or {}).get("reason") or {}).get("short") or "not started"
            log.info("  %s (post %s): FotMob %s not finished (%s)", title, post["id"], m.get("id"), short)
            continue
        hs, as_ = score
        wp.update_match_meta(post["id"], {"home_score": str(hs), "away_score": str(as_),
                                          "match_week": str(meta.get("match_week") or "")})
        try:
            wp.update_match_meta(post["id"], {"score_source": "fotmob", "fotmob_match_id": str(m.get("id"))})
        except Exception as exc:  # the score is what matters; provenance is best-effort
            log.warning("  could not record score_source on post %s: %s", post["id"], exc)
        meta.update(home_score=str(hs), away_score=str(as_), score_source="fotmob")
        audit(f"SCORE  post {post['id']}  fs {fs_id}  {hs}-{as_}  [wk {meta.get('match_week')}]  "
              f"FROM FOTMOB {m.get('id')}  ({title})")
        written.append({"post_id": post["id"], "fs_id": fs_id, "home_score": hs, "away_score": as_,
                        "match_week": meta.get("match_week"), "fotmob_id": m.get("id")})
    return written


def check_clash(wp, post: dict[str, Any], match: dict[str, Any],
                audit: Callable[[str], None] = log.info) -> bool:
    """FootyStats has a full-time score for a post we scored from FotMob: flag a
    difference in score_clash (Snippet 205 emails Simon). Never changes the score."""
    meta = post.get("meta") or {}
    if str(meta.get("score_source") or "") != "fotmob":
        return False
    fs_h = int(match.get("homeGoalCount", 0) or 0)
    fs_a = int(match.get("awayGoalCount", 0) or 0)
    if (str(fs_h), str(fs_a)) == (str(meta.get("home_score")).strip(), str(meta.get("away_score")).strip()):
        return False
    note = f"FootyStats {fs_h}-{fs_a} (match {match.get('id')}); site/FotMob {meta.get('home_score')}-{meta.get('away_score')}"
    if str(meta.get("score_clash") or "") == note:
        return False
    wp.update_match_meta(post["id"], {"score_clash": note})
    audit(f"CLASH  post {post['id']}  {note}")
    return True
