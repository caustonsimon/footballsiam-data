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
from wp_client import WPClient, WPError, WPHostChallengeError

log = logging.getLogger("fotmob_squads")

FM_BASE = "https://www.fotmob.com/api/data"
SEASON_LABEL = "2026/27"
SEASON_START = dt.date(2026, 8, 15)   # excludes pre-season (President's Cup, July/Aug)
TL1_FS_SEASON = 17367

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
}

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
NAT_FIX = {"Korea Republic": "South Korea", "Côte d'Ivoire": "Ivory Coast"}

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

def run_club(key: str, wp: WPClient, fm: FotMob, fs_rows: list[dict[str, Any]],
             wp_players: list[dict[str, Any]], report: dict[str, Any]) -> None:
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
    used_wp: set[int] = set()
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
            if len(ok) == 1:
                fs_id = int(ok[0][2]["id"])
                wp_hit = by_fsid.get(str(fs_id))
                how = f"footystats dob {ok[0][0]}"
            elif len(ok) > 1:
                rep["review"].append({"fotmob": name, "fotmob_id": m["id"], "why":
                                      "several FootyStats players share this date of birth",
                                      "candidates": [c[2].get("known_as") for c in ok]})
                continue

        if not wp_hit:
            cands = []
            for p in wp_players:
                rel = dob_relation(dob, unix_to_date((p.get("meta") or {}).get("birthday")))
                if not rel:
                    continue
                t = (p.get("title") or {}).get("rendered", "")
                sc = name_score(name, t)
                if sc >= (0.5 if rel == "exact" else 0.65):
                    cands.append((sc, p))
            cands.sort(key=lambda x: -x[0])
            if len(cands) == 1 or (len(cands) > 1 and cands[0][0] - cands[1][0] >= 0.25):
                wp_hit = cands[0][1]
                how = "site dob+name"
            elif cands:
                rep["review"].append({"fotmob": name, "fotmob_id": m["id"], "why":
                                      "more than one player page fits",
                                      "candidates": [(c[1]["id"], (c[1].get("title") or {}).get("rendered")) for c in cands]})
                continue

        if wp_hit and int(wp_hit["id"]) in used_wp:
            rep["review"].append({"fotmob": name, "fotmob_id": m["id"],
                                  "why": f"player page {wp_hit['id']} already claimed by another squad player"})
            continue
        if wp_hit:
            used_wp.add(int(wp_hit["id"]))
        plan.append((m, wp_hit, how or "new", fs_id))

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

        if hit:
            hacf = hit.get("acf") or {}
            hmeta = hit.get("meta") or {}
            if not str(hacf.get("nationality") or "").strip() and nat:
                acf["nationality"] = nat
            if not str(hmeta.get("birthday") or "").strip() and dob:
                meta["birthday"] = str(int(dt.datetime(dob.year, dob.month, dob.day,
                                                       tzinfo=dt.timezone.utc).timestamp()))
            wp_id = int(hit["id"])
            title = (hit.get("title") or {}).get("rendered", "") or m["name"]
            known = str(hmeta.get("fs_known_as") or "").strip()
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
        hub_squad.append({
            "wp_id": wp_id, "fotmob_id": m["id"], "name": known or title,
            "number": m.get("shirtNumber"), "pos": pos, "pos_label": POS_LABEL.get(pos, ""),
            "detail": m.get("positionIdsDesc"), "nat": nat, "ccode": m.get("ccode"),
            "dob": m.get("dateOfBirth"), "height": m.get("height"),
            "injury": bool(m.get("injury")), "stats": st["total"], "by_comp": st["by_comp"],
        })

    # -- leavers -------------------------------------------------------------
    for p in wp_players:
        if club_id_of((p.get("acf") or {}).get("current_club")) == wp_club and int(p["id"]) not in used_wp \
                and int(p["id"]) not in {h["wp_id"] for h in hub_squad if h["wp_id"]}:
            wp.update_player(int(p["id"]), acf={"current_club": None})
            rep["left"].append({"wp": int(p["id"]), "site": (p.get("title") or {}).get("rendered", "")})

    hub = {
        "club": club_title, "wp_club": wp_club, "fotmob_team_id": fm_id,
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
    ap.add_argument("--club", default="port", help="key from FOTMOB_TEAMS, or 'all'")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    keys = list(FOTMOB_TEAMS) if args.club == "all" else [args.club]
    for k in keys:
        if k not in FOTMOB_TEAMS:
            log.error("unknown club %s", k)
            return 2

    wp = WPClient(dry_run=not args.live)
    fs = FootyStatsClient()
    fm = FotMob()
    log.info("Mode: %s", "LIVE" if args.live else "DRY RUN")
    fs_rows = fs.get_league_players(TL1_FS_SEASON)
    log.info("FootyStats TL1 players: %d", len(fs_rows))
    wp_players = list(wp.iter_all_players(
        fields="id,title,acf.footystats_id,acf.current_club,acf.nationality,meta.birthday,"
               "meta.fotmob_id,meta.fs_known_as"))
    log.info("WP players: %d", len(wp_players))

    report: dict[str, Any] = {}
    try:
        for k in keys:
            run_club(k, wp, fm, fs_rows, wp_players, report)
    except WPHostChallengeError:
        log.error("SiteGround challenge - nothing more written")
        return 3

    Path("out").mkdir(exist_ok=True)
    Path("out/squad_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for k, r in report.items():
        log.info("%s: squad %s | matched %d | created %d | review %d | left %d | matches %s",
                 r["club"], r.get("squad_size"), len(r["matched"]), len(r["created"]),
                 len(r["review"]), len(r["left"]), r.get("matches_counted"))
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
