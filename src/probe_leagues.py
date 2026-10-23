"""List the FootyStats leagues chosen on the account for given countries, with
the current season id and how many matches/teams it serves (23 Oct 2026,
ahead of adding Indonesia, Malaysia, Singapore and Vietnam)."""
from __future__ import annotations

import json
import sys

import sync_matches  # noqa: F401  (loads .env)
from footystats_client import FootyStatsClient

COUNTRIES = [c.lower() for c in (sys.argv[1:] or ["Indonesia", "Malaysia", "Singapore", "Vietnam"])]

fs = FootyStatsClient()
d = fs._get("league-list", {"chosen_leagues_only": "true"})
rows = d.get("data") if isinstance(d, dict) else d
out = []
for lg in rows or []:
    country = str(lg.get("country", "")).lower()
    if not any(c in country for c in COUNTRIES):
        continue
    seasons = sorted(lg.get("season") or [], key=lambda s: s.get("year", 0))
    for s in seasons[-2:]:
        item = {"country": lg.get("country"), "league": lg.get("league_name") or lg.get("name"),
                "season_id": s.get("id"), "year": s.get("year")}
        try:
            item["matches"] = len(fs.get_league_matches(int(s["id"])))
            item["teams"] = len(fs.get_league_teams(int(s["id"])))
        except Exception as exc:  # noqa: BLE001
            item["error"] = str(exc)[:120]
        out.append(item)
        print(json.dumps(item, ensure_ascii=False))
if not out:
    print("no chosen leagues found for", COUNTRIES)
