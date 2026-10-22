"""Player profile feed from FotMob playerData (added 2026-10-22).

Builds the trimmed JSON the "FS Player Hub" snippet renders (player meta
fs_player_hub_json): bio, positions, this season's FotMob/Opta stat groups
with league percentiles, shot map, the season's match log, career and
trophies. Club and competition names go through house naming (no sponsors,
no "FC").
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

BKK = dt.timezone(dt.timedelta(hours=7))

CLUB_HOUSE = {
    "Port FC": "Port", "Thai Port": "Port", "Port MTI": "Port", "Singhtarua": "Port",
    "BG Pathum United": "Pathum United", "Bangkok Glass": "Pathum United",
    "Muang Thong United": "Muangthong United", "SCG Muangthong United": "Muangthong United",
    "Muangthong United": "Muangthong United",
    "Ratchaburi FC": "Ratchaburi", "Ratchaburi Mitr Phol": "Ratchaburi",
    "Chonburi FC": "Chonburi", "Prachuap FC": "Prachuap", "PT Prachuap": "Prachuap",
    "PTT Rayong": "Rayong", "Rayong FC": "Rayong", "True Bangkok United": "Bangkok United",
    "Uthai Thani FC": "Uthai Thani", "Sukhothai FC": "Sukhothai", "Pattani FC": "Pattani",
    "Ayutthaya United FC": "Ayutthaya United", "Police Tero FC": "Police Tero",
    "Lion City Sailors FC": "Lion City Sailors", "Kyoto Sanga FC": "Kyoto Sanga",
    "Bangkok FC": "Bangkok", "Nakhon Ratchasima FC": "Nakhon Ratchasima",
}

COMP_HOUSE = {
    "Thai League": "Thai League 1", "Thai League 1": "Thai League 1",
    "League Cup": "Thai League Cup", "FA Cup": "Thai FA Cup",
    "AFC Champions League Elite East": "AFC Champions League Elite",
    "AFC Champions League Elite": "AFC Champions League Elite",
    "AFC Champions League Two": "AFC Champions League Two",
    "ASEAN Club Championship": "ASEAN Club Championship",
    "ASEAN Championship": "ASEAN Championship",
    "Piala Presiden": "President's Cup (Indonesia)",
    "Champions Cup": "Thailand Champions Cup",
}

SKIP_COMPS = ("President's Cup", "Piala Presiden", "Club Friendlies")


def house_club(n: str | None) -> str:
    n = str(n or "").strip()
    if n in CLUB_HOUSE:
        return CLUB_HOUSE[n]
    n = re.sub(r"\s+F\.?C\.?$", "", n)
    n = re.sub(r"^F\.?C\.?\s+", "", n)
    return n


def house_comp(n: str | None) -> str:
    n = str(n or "").strip()
    return COMP_HOUSE.get(n, n)


def _bkk_date(utc: str | None) -> str:
    if not utc:
        return ""
    try:
        return dt.datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(BKK).strftime("%Y-%m-%d")
    except ValueError:
        return str(utc)[:10]


def _info(pd: dict[str, Any], title: str) -> Any:
    for it in pd.get("playerInformation") or []:
        if it.get("title") == title:
            v = it.get("value") or {}
            return v.get("dateValue") or v.get("numberValue") or v.get("fallback") or v.get("key")
    return None


def build_player_hub(pd: dict[str, Any], season_start: dt.date) -> dict[str, Any]:
    """Trim FotMob playerData to what the profile page needs."""
    out: dict[str, Any] = {"fotmob_id": pd.get("id"), "fotmob_name": pd.get("name")}

    # bio
    out["foot"] = str(_info(pd, "Preferred foot") or "").capitalize() or None
    ce = (pd.get("contractEnd") or {}).get("utcTime")
    out["contract_end"] = ce[:10] if ce else None
    out["captain"] = bool(pd.get("isCaptain"))
    inj = pd.get("injuryInformation")
    out["injury"] = ({"name": inj.get("name"), "until": (inj.get("expectedReturn") or {}).get("expectedReturnFallback")
                      if isinstance(inj.get("expectedReturn"), dict) else inj.get("expectedReturn")}
                     if isinstance(inj, dict) else None)

    # positions (pitch map)
    posd = pd.get("positionDescription") or {}
    out["primary_position"] = ((posd.get("primaryPosition") or {}).get("label"))
    out["positions"] = [
        {"label": (p.get("strPos") or {}).get("label"), "short": (p.get("strPosShort") or {}).get("label"),
         "top": (p.get("pitchPositionData") or {}).get("top"), "right": (p.get("pitchPositionData") or {}).get("right"),
         "main": bool(p.get("isMainPosition")), "n": p.get("occurences")}
        for p in posd.get("positions") or [] if p.get("pitchPositionData")
    ]

    # this season's deep stats: firstSeasonStats belongs to statSeasons[0].tournaments[0]
    ss = pd.get("statSeasons") or []
    first = (ss[0]["tournaments"][0] if ss and ss[0].get("tournaments") else {}) if ss else {}
    sn = re.sub(r"^(\d{4})/\d{2}(\d{2})$", r"\1/\2", ss[0]["seasonName"]) if ss else ""
    deep_label = f'{house_comp(first.get("name"))} {sn}' if first else None
    fss = pd.get("firstSeasonStats") or {}
    groups = []
    for g in ((fss.get("statsSection") or {}).get("items") or []):
        items = []
        for it in g.get("items") or []:
            items.append({"t": it.get("title"), "v": it.get("statValue"), "p90": it.get("per90"),
                          "pct": it.get("percentileRank"), "pct90": it.get("percentileRankPer90"),
                          "fmt": it.get("statFormat")})
        if items:
            groups.append({"title": g.get("title"), "items": items})
    out["deep"] = {"label": deep_label, "groups": groups,
                   "is_current": bool(ss and ss[0].get("seasonName", "").startswith(str(season_start.year)))}
    shots = []
    for s in fss.get("shotmap") or []:
        shots.append({"x": round(s.get("x") or 0, 1), "y": round(s.get("y") or 0, 1),
                      "goal": s.get("eventType") == "Goal", "on": bool(s.get("isOnTarget")),
                      "xg": round(s.get("expectedGoals") or 0, 2), "min": s.get("min"),
                      "sit": s.get("situation"), "type": s.get("shotType")})
    out["shots"] = shots

    # season match log (competitive, from season start)
    log = []
    for m in pd.get("recentMatches") or []:
        d = _bkk_date((m.get("matchDate") or {}).get("utcTime"))
        if not d or d < season_start.isoformat():
            continue
        comp_raw = m.get("leagueName") or ""
        if any(x in comp_raw for x in SKIP_COMPS):
            continue
        home = bool(m.get("isHomeTeam"))
        gf, ga = (m.get("homeScore"), m.get("awayScore")) if home else (m.get("awayScore"), m.get("homeScore"))
        res = None
        if gf is not None and ga is not None:
            res = "W" if gf > ga else ("L" if gf < ga else "D")
        rp = m.get("ratingProps") or {}
        log.append({
            "date": d, "comp": house_comp(comp_raw), "team": house_club(m.get("teamName")),
            "opp": house_club(m.get("opponentTeamName")), "home": home, "gf": gf, "ga": ga, "res": res,
            "mins": m.get("minutesPlayed") or 0, "g": m.get("goals") or 0, "a": m.get("assists") or 0,
            "yc": m.get("yellowCards") or 0, "rc": m.get("redCards") or 0,
            "rating": (float(rp["rating"]) or None) if rp.get("rating") not in (None, "") else None,
            "potm": bool(m.get("playerOfTheMatch")), "played": bool(m.get("playedInMatch")),
            "bench": bool(m.get("onBench")),
        })
    out["matches"] = sorted(log, key=lambda x: x["date"], reverse=True)

    # career
    car = (pd.get("careerHistory") or {}).get("careerItems") or {}
    def entries(key: str) -> list[dict[str, Any]]:
        rows = []
        for e in ((car.get(key) or {}).get("teamEntries") or []):
            rows.append({"team": house_club(e.get("team")), "from": (e.get("startDate") or "")[:4],
                         "to": (e.get("endDate") or "")[:4] if e.get("endDate") else None,
                         "active": bool(e.get("active")), "apps": e.get("appearances"),
                         "goals": e.get("goals"), "loan": e.get("transferType") == "loan"})
        return rows
    out["career"] = entries("senior")
    out["intl"] = entries("national team")

    # trophies
    tro = []
    for t in ((pd.get("trophies") or {}).get("playerTrophies") or []):
        for tt in t.get("tournaments") or []:
            if tt.get("seasonsWon"):
                tro.append({"team": house_club(t.get("teamName")), "comp": house_comp(tt.get("leagueName")),
                            "won": tt.get("seasonsWon")})
    out["trophies"] = tro

    nm = pd.get("nextMatch") or {}
    if nm.get("matchId"):
        out["next"] = {"home": house_club(nm.get("homeName")), "away": house_club(nm.get("awayName")),
                       "comp": house_comp(nm.get("leagueName")), "utc": nm.get("matchDate")}
    return out
