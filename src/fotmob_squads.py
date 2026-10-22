#!/usr/bin/env python3
"""Thai League 1 squads from FotMob -> footballsiam.com player pages + club hub.

Simon, 21 Oct 2026: "I would actually prefer to build the Thai League 1 teams
from their squad lists" (FotMob's squads are far more accurate than
FootyStats'). Started with Port; every club is a line in FOTMOB_TEAMS.

Per club:
  1. FotMob squad (/api/data/teams) -> every registered player with FotMob id,
     date of birth, nationality, shirt number, position, height.
  2. Each FotMob player is tied to ONE WordPress `player` post, never by name
     alone:
       a. a post already carrying this fotmob_id;
       b. FootyStats' own TL1 player list for the same club, same date of
          birth (exact, +-1 day, or day/month swapped -- FootyStats has both
          errors) and a name that agrees -> that FootyStats id -> the WP post
          with that footystats_id;
       c. any WP post with the same date of birth and a name that agrees;
       d. nothing -> a new player post (with the FootyStats id when step b
          found one, so Part A enrichment never creates a duplicate).
     Anything ambiguous (two candidates, DOB fits but the name does not) is
     REPORTED and left alone.
  3. Matched/created posts get current_club, shirt number, position, height,
     fotmob_id/fotmob_name; nationality and birthday only where blank (Simon's
     own corrections -- topics/player-facts -- always win).
  4. Players whose current_club is this club but who are not in the FotMob
     squad have current_club cleared (they have left). Reported by name.
  5. Club hub feed (club meta fs_club_hub_json): the squad with per-player
     season stats (apps, starts, minutes, goals, assists, cards, average
     rating) built from FotMob match details of every completed competitive
     match this season, split by competition, plus team aggregates (xG,
     shots, possession) for the league. Read by the "FS Club Hub" snippet.

Writes only with --live. A dry run prints and saves the full report.

    python src/fotmob_squads.py --club port            # dry run
    python src/fotmob_squads.py --club port --live
    python src/fotmob_squads.py --club all --live
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import logging
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import requests

import sync_matches  # noqa: F401  (loads .env)
from footystats_client import FootyStatsClient
from fotmob_player import build_player_hub
from thaileague import TL_CLUB_IDS, ThaiLeague, has_thai
from wp_client import WPClient, WPError, WPHostChallengeError

log = logging.getLogger("fotmob_squads")

FM_BASE = "https://www.fotmob.com/api/data"
SEASON_LABEL = "2026/27"
SEASON_START = dt.date(2026, 8, 15)   # excludes pre-season (President's Cup, July/Aug)
TL1_FS_SEASON = 17367
TL2_FS_SEASON = 17896

# key -> (FotMob team id, WP club post id, FootyStats team id)
FOTMOB_TEAMS: dict[str, tuple[int, int, int]] = {
    "port":             (165255, 7937, 1177),
    "buriram-united":   (165243, 7934, 1186),
    "bangkok-united":   (102231, 7929, 1173),
    "pathum-united":    (165250, 7928, 1182),
    "ratchaburi":       (421346, 7933, 1171),
    "chiangrai-united": (243243, 7930, 1184),
    "chonburi":         (109378, 7931, 1180),
    "prachuap":         (585025, 7938, 7038),
    "rayong":           (584984, 7939, 8374),
    "uthai-thani":      (561565, 7942, 678763),
    "sisaket-united":   (564561, 7961, 678796),
    "rasisalai-united": (2101783, 7959, 688240),
    "lamphun-warrior":  (584947, 7941, 9887),
    "sukhothai":        (584943, 7935, 1172),
    "ayutthaya-united": (1110271, 7940, 9898),
    "pattani":          (585115, 7953, 666944),
    # Thai League 2 (added 22 Oct 2026). FotMob league 9498; Thai League
    # registry tournament 226.
    "khon-kaen-united":   (626802, 7956, 8384),
    "chanthaburi":        (1513932, 7951, 21948),
    "phrae-united":       (578228, 7954, 9889),
    "chainat":            (309016, 7945, 1189),
    "nakhon-ratchasima":  (554037, 7932, 1188),
    "nakhon-pathom":      (165252, 7952, 8381),
    "pattaya-united":     (1513934, 7958, 688234),
    "chiangmai-united":   (1016183, 7949, 9886),
    "songkhla":           (309017, 7955, 21927),
    "mahasarakham":       (1696880, 7960, 678797),
    "kasetsart":          (615409, 7948, 8372),
    "nong-bua-pitchaya":  (626806, 7947, 8371),
    "muangthong-united":  (165251, 7936, 1174),
    "uttaradit":          (584948, 9792, 666933),
    "muang-loei-united":  (1814820, 13879, 666943),
    "police-tero":        (165244, 7944, 1187),
    "kanchanaburi":       (1513933, 7943, 688585),
    "satun":              (585112, 9862, 1023811),
}

T2_KEYS = {
    "khon-kaen-united", "chanthaburi", "phrae-united", "chainat", "nakhon-ratchasima",
    "nakhon-pathom", "pattaya-united", "chiangmai-united", "songkhla", "mahasarakham",
    "kasetsart", "nong-bua-pitchaya", "muangthong-united", "uttaradit", "muang-loei-united",
    "police-tero", "kanchanaburi", "satun",
}


def league_of(key: str) -> tuple[str, str]:
    """(house name, competition slug) of the club's league."""
    return ("Thai League 2", "thai-league-2") if key in T2_KEYS else ("Thai League 1", "thai-league-1")

# FotMob competition names -> house names (sponsor / "FC" stripped)
COMP_NAMES = {
    "Thai League": "Thai League 1",
    "AFC Champions League Elite East": "AFC Champions League Elite",
    "AFC Champions League Elite": "AFC Champions League Elite",
    "AFC Champions League Two": "AFC Champions League Two",
    "ASEAN Club Championship": "ASEAN Club Championship",
    "FA Cup": "Thai FA Cup",
    "League Cup": "Thai League Cup",
}
SKIP_COMPS = ("President's Cup", "Club Friendlies", "Friendlies")

GROUP_POS = {"keepers": "GK", "defenders": "DF", "midfielders": "MF", "attackers": "FW"}
POS_LABEL = {"GK": "Goalkeeper", "DF": "Defender", "MF": "Midfielder", "FW": "Forward"}

# Country names as the site writes them (FotMob mostly agrees)
NAT_FIX = {"Korea Republic": "South Korea", "Côte d'Ivoire": "Ivory Coast",
           "United Kingdom": "England"}  # Simon, 22 Oct 2026

# Thai-script names for foreign players, keyed by FotMob id. The Thai League
# registry only holds these players in English, so the spelling comes from
# Thai media (Siamsport, Matichon, PPTV, Ballthai) and otherwise
# th.soccerway.com's Port squad page. Checked 22 Oct 2026. A stored name_th
# that differs (Simon's own edit) is never overwritten by this table.
THAI_NAMES_FOREIGN: dict[int, str] = {
    187347:  "ไมเคิล ฟาลเคสการ์ด",     # Falkesgaard - PPTV, Soccerway
    799669:  "อัสนาวี มังกูอาลัม",      # Asnawi - Soccerway (Goal TH: อัสนาวี)
    1515510: "มาเธอุส ลินส์",          # Matheus Lins - PPTV, Ballthai, Soccerway
    1262918: "มาร์เซล สกาเลเซ",         # Marcel Scalese - Soccerway
    166471:  "เอริค สเวียตเชนโก้",      # Sviatchenko - Soccerway
    696369:  "โนโบรุ ชิมูระ",          # Noboru Shimura - Soccerway
    638768:  "คาร์ลอส จาโตบา",         # Carlos Jatobá - Siamsport, Matichon
    1101463: "เอสซัม อัล ซูบี",         # Issam Al Sabhi - Soccerway, Bankeela (to confirm)
    1457947: "รวน ริเบย์โร",           # Ruan Ribeiro - Soccerway (to confirm)
    1101035: "บรูโน โมเรรา",           # Bruno - Soccerway, Bankeela
    1132869: "กาก้า เมนเดส",           # Kaká Mendes - Soccerway
    776568:  "รุย คอสต้า",             # Rui Costa - Soccerway
    1198549: "ลูคัส โตคันตินส์",        # Lucas Tocantins - Siamsport, Ballthai
}

FOTMOB_PHOTO = "https://images.fotmob.com/image_resources/playerimages/{}.png"

# Further names (all Thai League 1 clubs) sourced 22 Oct 2026, kept as data.
try:
    _tn = json.loads((Path(__file__).resolve().parent / "data" / "thai_names.json").read_text())
    for _k, _v in _tn.get("names", {}).items():
        THAI_NAMES_FOREIGN.setdefault(int(_k), _v["th"])
except FileNotFoundError:
    pass

CLUB_NAME_FIX = {  # FotMob opponent names -> house names
    "BG Pathum United": "Pathum United", "Port FC": "Port", "Ratchaburi FC": "Ratchaburi",
    "Chonburi FC": "Chonburi", "Prachuap FC": "Prachuap", "Rayong FC": "Rayong",
    "Uthai Thani FC": "Uthai Thani", "Sukhothai FC": "Sukhothai", "Pattani FC": "Pattani",
    "Ayutthaya United FC": "Ayutthaya United", "Lion City Sailors FC": "Lion City Sailors",
    "Kyoto Sanga FC": "Kyoto Sanga",
}


# --------------------------------------------------------------------------
# FotMob
# --------------------------------------------------------------------------

class FotMob:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (footballsiam.com squad sync)"

    def _get(self, path: str, **params: Any) -> Any:
        for attempt in range(3):
            r = self.s.get(f"{FM_BASE}/{path}", params=params, timeout=30)
            if r.ok:
                j = r.json()
                if j is not None:
                    return j
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"FotMob {path} {params} failed ({r.status_code})")

    def team(self, team_id: int) -> dict[str, Any]:
        return self._get("teams", id=team_id)

    def match(self, match_id: int) -> dict[str, Any]:
        return self._get("matchDetails", matchId=match_id)["content"]

    def player(self, player_id: int) -> dict[str, Any]:
        return self._get("playerData", id=player_id)


# --------------------------------------------------------------------------
# Matching helpers
# --------------------------------------------------------------------------

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in s).split())


def name_score(a: str, b: str) -> float:
    """0..1. Token overlap (handles 'Kaka Mendes' v 'Francisco Clailson Mendes
    da Silva') blended with a character ratio (handles Thai romanisation:
    'Chamrasamee' v 'Chamratsamee', 'Kanitsribumphen' v 'Kanitsribampen')."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    stop = {"da", "de", "do", "dos", "das", "e", "van", "der", "al", "el"}
    ta2, tb2 = ta - stop or ta, tb - stop or tb
    best_tok = 0.0
    for x in ta2:
        for y in tb2:
            if len(x) >= 3 and len(y) >= 3:
                best_tok = max(best_tok, difflib.SequenceMatcher(None, x, y).ratio())
    overlap = len(ta2 & tb2) / min(len(ta2), len(tb2))
    whole = difflib.SequenceMatcher(None, na, nb).ratio()
    return max(overlap, whole, best_tok if best_tok >= 0.8 else 0.0)


def strict_name(a: str, b: str) -> float:
    """Whole-name similarity, for breaking ties between candidates that both
    pass name_score() (which forgives single-token matches: 'Nontharat' v
    'Nonthawat'). 1.0 only for the same name once accents/case are gone."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb or sorted(na.split()) == sorted(nb.split()):
        return 1.0
    ta, tb = na.split(), nb.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if set(short) <= set(long_) and (len(short) >= 2 or len(short[0]) >= 5):
        return 0.95   # one name is the other's short form: "Felipe Nunes" / "Felipe Nunes Silva"
    return difflib.SequenceMatcher(None, na, nb).ratio()


def pick_one(name: str, cands: list[tuple[Any, str]]) -> Any | None:
    """cands: (item, candidate_name). The single clear best by strict_name(),
    or None when it is still a genuine tie."""
    if len(cands) == 1:
        return cands[0][0]
    ranked = sorted(((strict_name(name, n), i, it) for i, (it, n) in enumerate(cands)), key=lambda x: -x[0])
    if ranked and ranked[0][0] >= 0.85 and ranked[0][0] - ranked[1][0] >= 0.1:
        return ranked[0][2]
    return None


def dob_relation(a: dt.date | None, b: dt.date | None) -> str | None:
    """'exact', 'near' (+-1 day: timezone of FootyStats' unix birthday) or
    'swap' (day/month transposed). None if unrelated."""
    if not a or not b:
        return None
    if a == b:
        return "exact"
    if abs((a - b).days) == 1:
        return "near"
    if a.year == b.year and a.month == b.day and a.day == b.month:
        return "swap"
    return None


def unix_to_date(v: Any) -> dt.date | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return dt.datetime.fromtimestamp(n, dt.timezone.utc).date()


def iso_date(s: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def club_id_of(v: Any) -> int | None:
    if not v:
        return None
    x = v[0] if isinstance(v, list) else v
    if isinstance(x, dict):
        x = x.get("id") or x.get("ID")
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Season stats from FotMob match details
# --------------------------------------------------------------------------

def _stat_value(groups: Any, key: str) -> Any:
    for g in groups or []:
        st = (g.get("stats") or {}).get(key)
        if st:
            return (st.get("stat") or {}).get("value")
    return None


def team_season(fm: FotMob, team_id: int, team_json: dict[str, Any]) -> dict[str, Any]:
    """Per-player and team aggregates from every completed competitive match."""
    fixtures = team_json["fixtures"]["allFixtures"]["fixtures"]
    players: dict[int, dict[str, Any]] = {}
    team = {"by_comp": {}, "matches": []}
    today = dt.datetime.now(dt.timezone.utc)

    for f in fixtures:
        st = f.get("status") or {}
        comp_raw = (f.get("tournament") or {}).get("name", "")
        if not st.get("finished") or st.get("cancelled") or any(s in comp_raw for s in SKIP_COMPS):
            continue
        when = dt.datetime.fromisoformat(st["utcTime"].replace("Z", "+00:00"))
        if when.date() < SEASON_START or when > today:
            continue
        comp = COMP_NAMES.get(comp_raw, comp_raw)
        try:
            c = fm.match(int(f["id"]))
        except Exception as exc:  # noqa: BLE001 - one bad match must not stop a club
            log.warning("  match %s: details failed (%s) - skipped", f["id"], exc)
            continue
        time.sleep(0.4)
        home = f["home"]["id"] == team_id
        side = "homeTeam" if home else "awayTeam"
        opp = f["away"] if home else f["home"]
        lu = (c.get("lineup") or {}).get(side) or {}
        gf = f["home"].get("score") if home else f["away"].get("score")
        ga = f["away"].get("score") if home else f["home"].get("score")

        # team stats (league and cups alike when FotMob has them)
        tstats: dict[str, Any] = {}
        try:
            groups = c["stats"]["Periods"]["All"]["stats"]
            idx = 0 if home else 1
            for g in groups:
                for s in g.get("stats", []):
                    if s.get("title") in ("Expected goals (xG)", "Ball possession", "Total shots",
                                          "Shots on target", "Big chances", "Corners") and s.get("stats"):
                        tstats.setdefault(s["title"], s["stats"][idx])
        except (KeyError, TypeError):
            pass
        team["matches"].append({
            "date": when.astimezone(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d"),
            "comp": comp, "home": home,
            "opp": CLUB_NAME_FIX.get(opp.get("name", ""), opp.get("name", "")),
            "gf": gf, "ga": ga, "stats": tstats,
            "formation": lu.get("formation"),
        })

        # minutes from the line-up (starters / subs with substitution events)
        length = 120 if "aet" in str(st.get("reason", {}).get("short", "")).lower() else 90
        appeared: dict[int, dict[str, Any]] = {}
        for role in ("starters", "subs"):
            for p in lu.get(role) or []:
                perf = p.get("performance") or {}
                subs = perf.get("substitutionEvents") or []
                on = 0 if role == "starters" else None
                off = length
                for e in subs:
                    if e.get("type") == "subIn":
                        on = int(e.get("time") or 0)
                    elif e.get("type") == "subOut":
                        off = int(e.get("time") or length)
                if on is None:
                    continue  # unused substitute
                appeared[int(p["id"])] = {
                    "start": role == "starters", "mins": max(0, min(off, length) - on),
                    "rating": perf.get("rating"),
                }

        # goals / assists / cards from matchFacts (own goals excluded)
        goals: dict[int, int] = {}
        assists: dict[int, int] = {}
        yel: dict[int, int] = {}
        red: dict[int, int] = {}
        for e in ((c.get("matchFacts") or {}).get("events") or {}).get("events") or []:
            if e.get("isHome") is not None and bool(e["isHome"]) != home:
                continue
            pid = (e.get("player") or {}).get("id")
            if e.get("type") == "Goal" and not e.get("ownGoal") and not e.get("isPenaltyShootoutEvent") and pid:
                goals[int(pid)] = goals.get(int(pid), 0) + 1
                aid = e.get("assistPlayerId")
                if aid:
                    assists[int(aid)] = assists.get(int(aid), 0) + 1
            elif e.get("type") == "Card" and pid:
                card = str(e.get("card") or "")
                if card == "Yellow":
                    yel[int(pid)] = yel.get(int(pid), 0) + 1
                elif card in ("Red", "YellowRed"):
                    red[int(pid)] = red.get(int(pid), 0) + 1

        for pid, a in appeared.items():
            row = players.setdefault(pid, {"total": _blank(), "by_comp": {}})
            for bucket in (row["total"], row["by_comp"].setdefault(comp, _blank())):
                bucket["apps"] += 1
                bucket["starts"] += 1 if a["start"] else 0
                bucket["mins"] += a["mins"]
                bucket["goals"] += goals.get(pid, 0)
                bucket["assists"] += assists.get(pid, 0)
                bucket["yellow"] += yel.get(pid, 0)
                bucket["red"] += red.get(pid, 0)
                if a["rating"]:
                    bucket["rating_sum"] += float(a["rating"])
                    bucket["rated"] += 1

    for row in players.values():
        for b in [row["total"], *row["by_comp"].values()]:
            b["rating"] = round(b["rating_sum"] / b["rated"], 2) if b["rated"] else None
            del b["rating_sum"], b["rated"]
    return {"players": players, "team": team}


def _blank() -> dict[str, Any]:
    return {"apps": 0, "starts": 0, "mins": 0, "goals": 0, "assists": 0,
            "yellow": 0, "red": 0, "rating_sum": 0.0, "rated": 0}


# --------------------------------------------------------------------------
# Main pass
# --------------------------------------------------------------------------

def match_registry(squad: list[dict[str, Any]], regs: list[dict[str, Any]]) -> dict[int, tuple[dict[str, Any], str]]:
    """FotMob squad member id -> (Thai League registration, how). Date of
    birth first; then shirt number + name (dates disagree for a handful:
    day/month swapped or a day out); never name alone."""
    out: dict[int, tuple[dict[str, Any], str]] = {}
    used: set[int] = set()
    for m in squad:
        dob = iso_date(m.get("dateOfBirth"))
        en = lambda r: r.get("full_name_en") or ""
        c = [r for r in regs if r["id"] not in used and iso_date(r.get("birth_date")) == dob]
        c = [r for r in c if name_score(m["name"], en(r)) >= 0.4 or str(r.get("shirt_number")) == str(m.get("shirtNumber"))]
        how = "dob"
        if len(c) != 1:
            c = [r for r in regs if r["id"] not in used and str(r.get("shirt_number")) == str(m.get("shirtNumber"))
                 and name_score(m["name"], en(r)) >= 0.5]
            how = "shirt+name"
        if len(c) != 1:
            c = [r for r in regs if r["id"] not in used and dob_relation(dob, iso_date(r.get("birth_date")))
                 and name_score(m["name"], en(r)) >= 0.6]
            how = "dob~+name"
        if len(c) == 1:
            out[int(m["id"])] = (c[0], how)
            used.add(c[0]["id"])
    return out


def run_club(key: str, wp: WPClient, fm: FotMob, fs_rows: list[dict[str, Any]],
             wp_players: list[dict[str, Any]], report: dict[str, Any],
             tl: ThaiLeague | None = None, claimed: set[int] | None = None) -> None:
    # claimed: player pages already given to a squad player in this run (any
    # club). Never matched twice, never cleared as a leaver by a later club.
    claimed = claimed if claimed is not None else set()
    fm_id, wp_club, fs_club = FOTMOB_TEAMS[key]
    tj = fm.team(fm_id)
    club_title = wp.get_club_title(wp_club)
    log.info("=== %s (FotMob %s, WP %s) ===", club_title, fm_id, wp_club)

    squad: list[dict[str, Any]] = []
    coach = None
    for g in tj["squad"]["squad"]:
        if g["title"] == "coach":
            coach = (g["members"] or [{}])[0].get("name")
            continue
        for m in g["members"]:
            squad.append({**m, "_pos": GROUP_POS.get(g["title"], "")})

    by_fotmob = {str((p.get("meta") or {}).get("fotmob_id") or ""): p for p in wp_players}
    by_fsid = {str((p.get("acf") or {}).get("footystats_id") or ""): p for p in wp_players}
    fs_club_rows = [r for r in fs_rows if r.get("club_team_id") == fs_club]

    rep = report.setdefault(key, {"club": club_title, "matched": [], "created": [],
                                  "review": [], "left": [], "coach_fotmob": coach})
    used_wp = claimed
    title_of = lambda p: (p.get("title") or {}).get("rendered", "")
    plan: list[tuple[dict[str, Any], dict[str, Any] | None, str, int | None]] = []

    for m in squad:
        dob = iso_date(m.get("dateOfBirth"))
        name = m["name"]
        wp_hit = by_fotmob.get(str(m["id"]))
        how = "fotmob_id" if wp_hit else ""
        fs_id = None

        if not wp_hit:
            cands = []
            for r in fs_club_rows:
                rel = dob_relation(dob, unix_to_date(r.get("birthday")))
                if rel:
                    nm = r.get("known_as") or r.get("full_name") or ""
                    sc = max(name_score(name, nm), name_score(name, r.get("full_name") or ""))
                    cands.append((rel, sc, r))
            ok = [c for c in cands if c[0] == "exact" or c[1] >= 0.55]
            one = pick_one(name, [(c, c[2].get("known_as") or c[2].get("full_name") or "") for c in ok]) if ok else None
            if one:
                fs_id = int(one[2]["id"])
                wp_hit = by_fsid.get(str(fs_id))
                if wp_hit and int(wp_hit["id"]) in used_wp:
                    wp_hit = None
                how = f"footystats dob {one[0]}"
            elif len(ok) > 1:
                names_ok = [c[2].get("known_as") or c[2].get("full_name") or "" for c in ok]
                dup = all(strict_name(names_ok[0], n) >= 0.8 for n in names_ok[1:])
                if dup:
                    # FootyStats holds the same player twice (Pobsayai /
                    # Popsayai, 22 Oct 2026): use the entry that already has a
                    # site page, else go on without a FootyStats id.
                    linked = [c for c in ok if str(c[2]["id"]) in by_fsid
                              and int(by_fsid[str(c[2]["id"])]["id"]) not in used_wp]
                    if len(linked) == 1:
                        fs_id = int(linked[0][2]["id"])
                        wp_hit = by_fsid[str(fs_id)]
                        how = "footystats duplicate entry, the one with a page"
                    rep["review"].append({"fotmob": name, "fotmob_id": m["id"], "why":
                                          "FootyStats lists this player twice (handled)",
                                          "candidates": names_ok})
                else:
                    rep["review"].append({"fotmob": name, "fotmob_id": m["id"], "why":
                                          "several FootyStats players share this date of birth",
                                          "candidates": names_ok})
                    continue

        if not wp_hit:
            cands = []
            for p in wp_players:
                if int(p["id"]) in used_wp:
                    continue
                rel = dob_relation(dob, unix_to_date((p.get("meta") or {}).get("birthday")))
                if not rel:
                    continue
                t = (p.get("title") or {}).get("rendered", "")
                # Site-wide, a shared birthday is common (3,000+ pages), so the
                # name must agree strictly: romanisation variants pass
                # (Prachya Fatsuphap / Prachaya Fadsupap), different people
                # sharing a birthday do not (Pongrawit Jantawong / Pongsil
                # Tana, T2 dry run 22 Oct 2026). Word order is ignored.
                srt = lambda s: " ".join(sorted(s.lower().replace("-", " ").split()))
                sc = max(strict_name(name, t), strict_name(srt(name), srt(t)))
                if sc >= (0.75 if rel == "exact" else 0.82):
                    cands.append((sc, p))
            cands.sort(key=lambda x: -x[0])
            one = pick_one(name, [(c[1], title_of(c[1])) for c in cands]) if cands else None
            if len(cands) > 1 and cands[0][0] - cands[1][0] >= 0.25:
                one = cands[0][1]
            if one:
                wp_hit = one
                how = "site dob+name"
            elif cands:
                rep["review"].append({"fotmob": name, "fotmob_id": m["id"], "why":
                                      "more than one player page fits",
                                      "candidates": [(c[1]["id"], (c[1].get("title") or {}).get("rendered")) for c in cands]})
                continue

        if not wp_hit:
            # e. already on this club's books on the site with the same name
            #    (date of birth missing or wrong on one side -- e.g. FootyStats
            #    has Suphanan Bureerat born 10 Oct, FotMob 10 Dec).
            cands = []
            for p in wp_players:
                if int(p["id"]) in used_wp:
                    continue
                acf_p, meta_p = p.get("acf") or {}, p.get("meta") or {}
                at_club = club_id_of(acf_p.get("current_club")) == wp_club or \
                    str(meta_p.get("last_confirmed_club") or "") == str(wp_club)
                if not at_club:
                    continue
                # whole-name similarity, not name_score(): one shared-looking
                # token ("Thanawat"/"Thanawit") must not tie two players
                names = [title_of(p), str(meta_p.get("fs_known_as") or "")]
                sc = max(strict_name(name, n) for n in names if n) if any(names) else 0.0
                sdob = unix_to_date(meta_p.get("birthday"))
                # A clashing DOB only blocks a near-miss name: the site's DOBs
                # came from FootyStats, which gets them wrong (Jordan Gele,
                # Steven van Dijk), so the same name at the same club still wins.
                if sdob and dob and not dob_relation(dob, sdob) and sc < 0.95:
                    continue
                if sc >= 0.8:
                    cands.append((sc, p))
            cands.sort(key=lambda x: -x[0])
            one = pick_one(name, [(c[1], title_of(c[1])) for c in cands]) if cands else None
            if one:
                wp_hit = one
                site_dob = unix_to_date((wp_hit.get("meta") or {}).get("birthday"))
                how = f"same club + name (site DOB {site_dob}, FotMob {dob})"
            elif cands:
                rep["review"].append({"fotmob": name, "fotmob_id": m["id"], "why":
                                      "two player pages at this club fit the name",
                                      "candidates": [(c[1]["id"], (c[1].get("title") or {}).get("rendered")) for c in cands]})
                continue

        if not wp_hit:
            # f. the same name exactly anywhere on the site (moved club; DOB
            #    missing or agreeing), so a transfer never makes a duplicate.
            cands = []
            for p in wp_players:
                if int(p["id"]) in used_wp:
                    continue
                names = [title_of(p), str((p.get("meta") or {}).get("fs_known_as") or "")]
                if not any(n and strict_name(name, n) == 1.0 for n in names):
                    continue
                sdob = unix_to_date((p.get("meta") or {}).get("birthday"))
                if sdob and dob and not dob_relation(dob, sdob):
                    continue
                cands.append(p)
            if len(cands) == 1:
                wp_hit = cands[0]
                how = "exact name on site (club changed)"
            elif len(cands) > 1:
                rep["review"].append({"fotmob": name, "fotmob_id": m["id"], "why": "several pages carry this exact name",
                                      "candidates": [(c["id"], title_of(c)) for c in cands]})
                continue

        if wp_hit and int(wp_hit["id"]) in used_wp:
            rep["review"].append({"fotmob": name, "fotmob_id": m["id"],
                                  "why": f"player page {wp_hit['id']} already claimed by another squad player"})
            continue
        if wp_hit:
            used_wp.add(int(wp_hit["id"]))
        plan.append((m, wp_hit, how or "new", fs_id))

    # -- Thai League registry (Thai names, official DOB/nationality, photo) --
    reg_by: dict[int, tuple[dict[str, Any], str]] = {}
    if tl and key in TL_CLUB_IDS:
        regs = tl.club_players(TL_CLUB_IDS[key])
        reg_by = match_registry([m for m, *_ in plan], regs)
        rep["registry"] = {"registered": len(regs), "matched": len(reg_by)}
        for m, *_ in plan:
            if int(m["id"]) not in reg_by:
                rep["review"].append({"fotmob": m["name"], "why": "not found in the Thai League registry"})

    # -- season stats ------------------------------------------------------
    season = team_season(fm, fm_id, tj)

    hub_squad = []
    for m, hit, how, fs_id in plan:
        pos = m["_pos"] or ""
        nat = NAT_FIX.get(m.get("cname") or "", m.get("cname") or "")
        acf: dict[str, Any] = {"current_club": [wp_club]}
        if m.get("shirtNumber") not in (None, ""):
            acf["shirt_number"] = str(m["shirtNumber"])
        if pos:
            acf["position"] = pos
        if m.get("height"):
            acf["height_cm"] = str(m["height"])
        meta: dict[str, Any] = {"fotmob_id": str(m["id"]), "fotmob_name": m["name"]}
        dob = iso_date(m.get("dateOfBirth"))
        reg, reg_how = reg_by.get(int(m["id"]), (None, ""))
        name_th = ""
        stored_th = str(((hit or {}).get("meta") or {}).get("name_th") or "").strip()
        if not (reg and has_thai(reg.get("full_name"))):
            name_th = stored_th or THAI_NAMES_FOREIGN.get(int(m["id"]), "")
            if name_th and name_th != stored_th:
                meta["name_th"] = name_th
        if reg:
            # The league registry is authoritative for DOB and nationality.
            meta["tl_player_id"] = str(reg["id"])
            if has_thai(reg.get("full_name")):
                name_th = " ".join(str(reg["full_name"]).split())
                meta["name_th"] = name_th
            rdob = iso_date(reg.get("birth_date"))
            if rdob and rdob != dob:
                rep.setdefault("dob_fixed", []).append({"player": m["name"], "fotmob": str(dob), "thai_league": str(rdob)})
            dob = rdob or dob
            rnat = ((reg.get("nationality") or {}).get("name") or "").strip()
            if rnat:
                nat = NAT_FIX.get(rnat, rnat)
            if reg.get("height"):
                acf["height_cm"] = str(reg["height"])

        if hit:
            hacf = hit.get("acf") or {}
            hmeta = hit.get("meta") or {}
            old_nat = str(hacf.get("nationality") or "").strip()
            # The site's nationality is never overwritten (Simon's call per
            # player: registry passports are quota registrations, not always
            # the sporting nationality). Blank -> filled; differences reported.
            if nat and not old_nat:
                acf["nationality"] = nat
            elif old_nat and nat and old_nat != nat:
                rep.setdefault("nationality_differs", []).append({"player": m["name"], "site": old_nat, "registry_or_fotmob": nat})
            if old_nat:
                nat = old_nat
            if dob and (reg or not str(hmeta.get("birthday") or "").strip()):
                meta["birthday"] = str(int(dt.datetime(dob.year, dob.month, dob.day,
                                                       tzinfo=dt.timezone.utc).timestamp()))
            wp_id = int(hit["id"])
            title = (hit.get("title") or {}).get("rendered", "") or m["name"]
            known = str(hmeta.get("fs_known_as") or "").strip()
            if not known and nat != "Thailand":
                # Thai names keep the site's romanisation (Simon's corrections);
                # foreign players go by the name they are known by on FotMob
                # (Kaka Mendes, not Francisco Clailson Mendes da Silva).
                known = m["name"]
            wp.update_player(wp_id, meta=meta, acf=acf)
            rep["matched"].append({"wp": wp_id, "site": title, "fotmob": m["name"], "how": how})
        else:
            parts = m["name"].split()
            acf_new = {**acf, "first_name": parts[0], "last_name": " ".join(parts[1:]),
                       "nationality": nat}
            if fs_id:
                acf_new["footystats_id"] = str(fs_id)
            if dob:
                meta["birthday"] = str(int(dt.datetime(dob.year, dob.month, dob.day,
                                                       tzinfo=dt.timezone.utc).timestamp()))
            resp = wp.create_player({"title": m["name"], "status": "publish",
                                     "acf": acf_new, "meta": meta})
            wp_id = None if wp.dry_run else int(resp["id"])
            title, known = m["name"], ""
            rep["created"].append({"wp": wp_id, "fotmob": m["name"], "footystats_id": fs_id})

        st = season["players"].get(int(m["id"]), {"total": None, "by_comp": {}})

        # -- official photo (Thai League registration) -----------------------
        photo_src = (reg or {}).get("photo") or ""
        if wp_id and not photo_src and not ((hit or {}).get("featured_media")):
            photo_src = FOTMOB_PHOTO.format(int(m["id"]))  # fallback: FotMob headshot
        if wp_id and photo_src and (tl or photo_src.startswith("https://images.fotmob.com")):
            reg = dict(reg or {}, photo=photo_src)
            old_src = str(((hit or {}).get("meta") or {}).get("tl_photo_src") or "")
            if old_src != reg["photo"]:
                try:
                    tmp = Path("out/photos"); tmp.mkdir(parents=True, exist_ok=True)
                    slug = norm(known or title).replace(" ", "-") or str(wp_id)
                    fn = tmp / f"{slug}-{key}-2026-27.png"
                    fn.write_bytes(tl.photo_bytes(reg["photo"]))
                    media = wp.upload_media(str(fn), title=f"{known or title} ({club_title}, {SEASON_LABEL})",
                                            alt_text=f"{known or title}, {club_title}")
                    if not wp.dry_run:
                        wp._json_or_raise(wp._request("POST", f"wp/v2/player/{wp_id}", json={
                            "featured_media": int(media["id"]),
                            "acf": {"player_photo_url": media.get("source_url", "")},
                            "meta": {"tl_photo_src": reg["photo"]}}), f"photo {wp_id}")
                    rep.setdefault("photos", []).append(known or title)
                except Exception as exc:  # noqa: BLE001 - a photo must not stop the squad
                    log.warning("  photo for %s failed: %s", known or title, exc)

        # -- profile page feed (FotMob playerData) -----------------------------
        if wp_id:
            try:
                ph = build_player_hub(fm.player(int(m["id"])), SEASON_START)
                time.sleep(0.3)
                ph.update({
                    "name": known or title, "name_th": name_th, "number": m.get("shirtNumber"),
                    "pos": pos, "pos_label": POS_LABEL.get(pos, ""), "nat": nat,
                    "dob": dob.isoformat() if dob else None,
                    "height": (reg or {}).get("height") or m.get("height"), "weight": (reg or {}).get("weight"),
                    "club": club_title, "wp_club": wp_club, "season_label": SEASON_LABEL,
                    "season": st["total"], "by_comp": st["by_comp"],
                    "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d %H:%M"),
                })
                wp.update_player(wp_id, meta={"fs_player_hub_json": json.dumps(ph, ensure_ascii=False)})
                Path("out/players").mkdir(parents=True, exist_ok=True)
                Path(f"out/players/{wp_id}.json").write_text(json.dumps(ph, ensure_ascii=False, indent=1))
            except Exception as exc:  # noqa: BLE001
                log.warning("  profile feed for %s failed: %s", known or title, exc)

        hub_squad.append({
            "wp_id": wp_id, "fotmob_id": m["id"], "name": known or title,
            "number": m.get("shirtNumber"), "pos": pos, "pos_label": POS_LABEL.get(pos, ""),
            "detail": m.get("positionIdsDesc"), "nat": nat, "ccode": m.get("ccode"),
            "name_th": name_th, "dob": dob.isoformat() if dob else m.get("dateOfBirth"),
            "height": (reg or {}).get("height") or m.get("height"), "weight": (reg or {}).get("weight"),
            "injury": bool(m.get("injury")), "stats": st["total"], "by_comp": st["by_comp"],
        })

    # -- leavers -------------------------------------------------------------
    for p in wp_players:
        if club_id_of((p.get("acf") or {}).get("current_club")) == wp_club and int(p["id"]) not in claimed \
                and int(p["id"]) not in {h["wp_id"] for h in hub_squad if h["wp_id"]}:
            wp.update_player(int(p["id"]), acf={"current_club": None}, meta={"fs_player_hub_json": ""})
            rep["left"].append({"wp": int(p["id"]), "site": (p.get("title") or {}).get("rendered", "")})

    hub = {
        "club": club_title, "wp_club": wp_club, "fotmob_team_id": fm_id,
        "league": league_of(key)[0], "league_slug": league_of(key)[1],
        "season": SEASON_LABEL, "updated": dt.datetime.now(
            dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d %H:%M"),
        "source": "FotMob", "squad": hub_squad, "team": season["team"],
        "venue": ((tj.get("overview") or {}).get("venue") or {}).get("widget", {}).get("name"),
    }
    wp.update_club(wp_club, meta={"fs_club_hub_json": json.dumps(hub, ensure_ascii=False),
                                  "fotmob_team_id": str(fm_id)})
    rep["squad_size"] = len(hub_squad)
    rep["matches_counted"] = len(season["team"]["matches"])
    out = Path("out"); out.mkdir(exist_ok=True)
    (out / f"hub_{key}.json").write_text(json.dumps(hub, ensure_ascii=False, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--club", default="port",
                    help="key from FOTMOB_TEAMS, or 't1', 't2', 'all' (T1 then T2)")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    if args.club == "all":
        keys = list(FOTMOB_TEAMS)
    elif args.club == "t1":
        keys = [k for k in FOTMOB_TEAMS if k not in T2_KEYS]
    elif args.club == "t2":
        keys = [k for k in FOTMOB_TEAMS if k in T2_KEYS]
    else:
        keys = [args.club]
    for k in keys:
        if k not in FOTMOB_TEAMS:
            log.error("unknown club %s", k)
            return 2

    wp = WPClient(dry_run=not args.live)
    fs = FootyStatsClient()
    fm = FotMob()
    tl = ThaiLeague()
    log.info("Mode: %s", "LIVE" if args.live else "DRY RUN")
    fs_rows = []
    if any(k not in T2_KEYS for k in keys):
        fs_rows += fs.get_league_players(TL1_FS_SEASON)
    if any(k in T2_KEYS for k in keys):
        fs_rows += fs.get_league_players(TL2_FS_SEASON)
    log.info("FootyStats players (TL1/TL2 as needed): %d", len(fs_rows))
    wp_players = list(wp.iter_all_players(
        fields="id,title,acf.footystats_id,acf.current_club,acf.nationality,meta.birthday,"
               "meta.fotmob_id,meta.fs_known_as,meta.last_confirmed_club,meta.tl_photo_src,meta.name_th,featured_media"))
    log.info("WP players: %d", len(wp_players))

    report: dict[str, Any] = {}
    claimed: set[int] = set()
    # Shared across clubs: a page claimed by one club is never matched again
    # and never cleared as a leaver by a club that runs later.
    try:
        for k in keys:
            try:
                run_club(k, wp, fm, fs_rows, wp_players, report, tl, claimed)
            except WPHostChallengeError:
                raise
            except Exception as exc:  # noqa: BLE001 - one club must not stop the rest
                log.error("%s FAILED: %s", k, exc)
                report.setdefault(k, {"club": k, "matched": [], "created": [], "review": [], "left": []})["error"] = str(exc)
    except WPHostChallengeError:
        log.error("SiteGround challenge - nothing more written")
        return 3

    Path("out").mkdir(exist_ok=True)
    Path("out/squad_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for k, r in report.items():
        log.info("%s: squad %s | matched %d | created %d | review %d | left %d | matches %s",
                 r["club"], r.get("squad_size"), len(r["matched"]), len(r["created"]),
                 len(r["review"]), len(r["left"]), r.get("matches_counted"))
        for k2 in ("registry", "photos", "dob_fixed", "nationality_fixed"):
            if r.get(k2):
                log.info("  %s: %s", k2.upper(), r[k2])
        for x in r["review"]:
            log.warning("  REVIEW %s", x)
        for x in r["created"]:
            log.info("  NEW    %s", x)
        for x in r["left"]:
            log.info("  LEFT   %s", x)
        for x in r["matched"]:
            log.info("  MATCH  %s <- %s (%s)", x["site"], x["fotmob"], x["how"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
