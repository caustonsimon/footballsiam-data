"""stats_hub.py — builds the Thai League 1 stats hub data and pushes it to footballsiam.com.

Runs from .github/workflows/stats-hub.yml after the nightly sync. Safe to re-run: it recomputes
everything from scratch and the site simply replaces the stored copy.

Env:  FOOTYSTATS_KEY   FootyStats API key
      FS_HUB_TOKEN     shared secret matching the token the WordPress snippet generated
      DRY_RUN=1        build and print the JSON, don't post

Sources
- Players and ratings: FootyStats /league-players + /player-stats (via fs_stats.py).
- Clubs: FootyStats /league-teams?include=stats.
- Display names: the player's `fs_known_as` field on footballsiam.com, else the player page title.
  Players are matched to site pages by FootyStats ID only (never by name).
- Nationality: the player page's `nationality` field on footballsiam.com wins over FootyStats,
  so a correction made on the site (e.g. Ben Davis = Thailand, not Singapore) sticks.
- Club: the player page's `current_club` on footballsiam.com wins over FootyStats' club_team_id,
  so mid-season transfers show the new club. FootyStats is the fallback when it is empty.
"""
import datetime as dt
import html
import json
import os
import sys

import pandas as pd
import requests

import fs_stats as fs

SEASON = fs.SEASONS["T1 2026/27"]
SITE = "https://footballsiam.com"
ENDPOINT = f"{SITE}/wp-json/fs/v1/stats-hub"
UA = {"User-Agent": "footballsiam-data/stats-hub"}

# chart-only short club names (the page shows full names everywhere else)
SHORT = {"Pathum United": "Pathum", "Chiangrai United": "Chiangrai", "Bangkok United": "Bangkok Utd",
         "Buriram United": "Buriram", "Lamphun Warrior": "Lamphun", "Ayutthaya United": "Ayutthaya",
         "Sisaket United": "Sisaket", "Rasisalai United": "Rasisalai", "Muangthong United": "Muangthong",
         "Kanchanaburi Power": "Kanchanaburi"}


def site_clubs():
    """WordPress club post id -> club name as the site writes it (house style, no sponsors)."""
    out, page = {}, 1
    while True:
        r = requests.get(f"{SITE}/wp-json/wp/v2/club", headers=UA, timeout=60,
                         params={"per_page": 100, "page": page, "_fields": "id,title"})
        r.raise_for_status()
        for x in r.json():
            out[int(x["id"])] = html.unescape(x["title"]["rendered"]).strip()
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            return out
        page += 1


def site_players():
    """FootyStats id -> {name, link, nat, club}. Uses the known-as field when set.
    `club` is the player page's current_club (None when the page has none set)."""
    clubs = site_clubs()
    out, page = {}, 1
    while True:
        r = requests.get(f"{SITE}/wp-json/wp/v2/player", headers=UA, timeout=60, params={
            "per_page": 100, "page": page,
            "_fields": "id,title,link,acf.footystats_id,acf.nationality,acf.current_club,meta.fs_known_as"})
        r.raise_for_status()
        rows = r.json()
        for x in rows:
            acf = x.get("acf") or {}
            fid = acf.get("footystats_id")
            if not fid:
                continue
            known = ((x.get("meta") or {}).get("fs_known_as") or "").strip()
            title = html.unescape(x["title"]["rendered"])
            nat = (acf.get("nationality") or "").strip()
            cc = acf.get("current_club") or []
            cc = cc if isinstance(cc, list) else [cc]
            club = next((clubs[int(c)] for c in cc if str(c).isdigit() and int(c) in clubs), None)
            out[int(fid)] = {"name": known or title, "link": x["link"], "has_known": bool(known),
                             "nat": nat, "club": club}
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            return out
        page += 1


def latest_matchweek():
    """(matchweek, complete?) for the latest matchweek with at least one finished match."""
    d = fs._get_fresh("league-matches", season_id=SEASON).get("data", [])
    done = [m for m in d if m.get("status") == "complete"]
    if not done:
        return 0, True
    mw = max(m["game_week"] for m in done)
    return mw, all(m.get("status") == "complete" for m in d if m["game_week"] == mw)


def build():
    wp = site_players()
    teams = fs.team_names(SEASON)
    lp = fs.league_players(SEASON)
    max_min = int(lp["minutes_played_overall"].max())
    threshold = min(900, max(180, round(0.4 * max_min)))
    missing, long_names, club_moves = [], set(), set()

    def person(pid, fallback, club_id, nat, pos, mins):
        w = wp.get(int(pid))
        if not w:
            missing.append(f"{fallback} ({pid})")
        name = w["name"] if w else fallback
        if w and not w["has_known"] and nat != "Thailand" and len(name.split()) > 2:
            long_names.add(name)
        # The site's current_club wins: FootyStats files a mid-season mover's whole league
        # season under one club (e.g. Robert Zulj, Buriram United -> Ratchaburi, Sept 2026).
        fs_club = teams.get(club_id, "?")
        club = (w or {}).get("club") or fs_club
        if club != fs_club:
            club_moves.add(f"{name}: {fs_club} -> {club}")
        return {"name": name, "link": w["link"] if w else None, "club": club,
                "nat": nat, "pos": pos, "mins": int(mins)}

    def base(r):
        return person(r["id"], r["name"], r["club_team_id"], r["nationality"], r.get("position_group") or r.get("position"),
                      r["minutes_played_overall"])

    def site_nat(df):
        """Site nationality overrides FootyStats where the player page has one."""
        df = df.copy()
        df["nationality"] = [((wp.get(int(i)) or {}).get("nat") or n) for i, n in zip(df["id"], df["nationality"])]
        return df

    lp = site_nat(lp)
    co = site_nat(fs.all_positions(SEASON, threshold))
    g = lp[lp.goals_overall > 0].sort_values(["goals_overall", "minutes_played_overall"], ascending=[False, True]).head(10)
    a = lp[lp.assists_overall > 0].sort_values(["assists_overall", "minutes_played_overall"], ascending=[False, True]).head(10)

    co["rating"] = pd.to_numeric(co["average_rating_overall"], errors="coerce")
    co = co[co["rating"] > 0]

    def top(df, n=5):
        res = []
        for _, r in df.sort_values(["rating", "minutes_played_overall"], ascending=[False, False]).head(n).iterrows():
            d = {**base(r), "pos": r["position"], "rating": round(float(r["rating"]), 2)}
            if r["position"] == "Goalkeeper":
                d["cs"] = int(max(r.get("clean_sheets_overall") or 0, 0))
                sp = r.get("save_percentage_overall")
                d["savepct"] = None if sp is None or pd.isna(sp) or sp < 0 else round(float(sp))
            else:
                d["goals"] = int(r["goals_overall"])
                d["assists"] = int(max(r["assists_overall"], 0))
            res.append(d)
        return res

    positions = ["Goalkeeper", "Defender", "Midfielder", "Forward"]
    thai = co[co["nationality"] == "Thailand"]
    xg = co.assign(xg=pd.to_numeric(co["xg_total_overall"], errors="coerce"))
    xg = xg[xg["xg"] > 0].sort_values("xg", ascending=False).head(12)

    clubs = []
    for t in fs._get_fresh("league-teams", season_id=SEASON, include="stats").get("data", []):
        s, n = t["stats"], fs.clean_name(t.get("cleanName") or t["name"])
        clubs.append({"name": n, "short": SHORT.get(n, n), "played": s["seasonMatchesPlayed_overall"],
                      "xgf": s["xg_for_avg_overall"], "xga": s["xg_against_avg_overall"],
                      "gpg": s["seasonScoredAVG_overall"], "gapg": s["seasonConcededAVG_overall"],
                      "cs": s["seasonCS_overall"], "poss": s["possessionAVG_overall"],
                      "shots": s["shotsAVG_overall"], "sot": s["shotsOnTargetAVG_overall"], "cards": s["cardsAVG_overall"]})

    mw, complete = latest_matchweek()
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))
    data = {
        "season": "2026/27", "mw": mw, "mw_complete": complete, "threshold": threshold,
        "updated": f"{now.day} {now:%B %Y}",
        "scorers": [{**base(r), "goals": int(r.goals_overall), "pens": int(max(r.penalty_goals, 0))} for _, r in g.iterrows()],
        "assists": [{**base(r), "assists": int(r.assists_overall)} for _, r in a.iterrows()],
        "positions": {p: top(co[co["position"] == p]) for p in positions},
        "thai": {"Outfield": top(thai[thai["position"] != "Goalkeeper"]), **{p: top(thai[thai["position"] == p]) for p in positions}},
        "league_avg_rating": round(float(co["rating"].mean()), 2),
        "finishing": [{**base(r), "goals": int(r.goals_overall), "xg": round(float(r.xg), 2)} for _, r in xg.iterrows()],
        "clubs": clubs,
    }
    return data, missing, sorted(long_names), sorted(club_moves)


def validate(d):
    """Fail closed: never replace a good page with a half-built one."""
    problems = []
    for k in ("scorers", "clubs", "finishing"):
        if not d[k]:
            problems.append(f"{k} is empty")
    for p, rows in d["positions"].items():
        if not rows:
            problems.append(f"no {p} qualifies")
    if len(d["clubs"]) < 16:
        problems.append(f"only {len(d['clubs'])} clubs")
    return problems


def main():
    data, missing, long_names, club_moves = build()
    if club_moves:
        print("Club taken from the site's current_club instead of FootyStats:", "; ".join(club_moves))
    if missing:
        print("Players with no footballsiam.com page (shown with FootyStats names):", ", ".join(missing))
    if long_names:
        print("Long names with no 'known as' set (add one on the player page):", "; ".join(long_names))
    problems = validate(data)
    if problems:
        sys.exit("Not posting: " + "; ".join(problems))
    if os.environ.get("DRY_RUN") == "1":
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return
    r = requests.post(ENDPOINT, json=data, timeout=60,
                      headers={**UA, "x-fs-hub-token": os.environ["FS_HUB_TOKEN"]})
    print(r.status_code, r.text[:300])
    r.raise_for_status()


if __name__ == "__main__":
    main()
