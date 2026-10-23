"""One-off (re-runnable): tie Thai League 3 matches and crests to the club pages.

Simon, 23 Oct 2026: "the t3 names in tables and fixtures have no logos and
are not clickable". T3 fixtures were imported with names only (FootyStats'
T3 team ids are not in CLUB_MAP), so tables and fixture lists could not link
or show a crest. This:

  1. sets home_team / away_team on every 2026/27 match whose side is a
     Thai League 3 club, matched by its exact stored name against the
     confirmed list in tl3_squads.T3_CLUBS (never fuzzy) - T3 regions, the
     FA Cup and League Cup alike. Only empty ids are filled.
  2. sets club_logo_url on each T3 club page that has none, from the Thai
     League registry crest (data/t3_registry_teams.json).

New matches are linked at creation by sync_matches.t3_club_by_name().
Dry run by default; --live writes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import sync_matches  # noqa: F401  (loads .env)
from sync_matches import t3_club_by_name
from tl3_squads import T3_CLUBS
from wp_client import WPClient

log = logging.getLogger("backfill_t3_links")


def blank(v: Any) -> bool:
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "").strip().strip('"[] ') in ("", "0")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    wp = WPClient(dry_run=not args.live)

    # 1. matches
    n = linked = 0
    unmatched: dict[str, int] = {}
    for m in wp.iter_all_matches():
        meta = m.get("meta") or {}
        if str(meta.get("season") or "") not in ("2026/2027", "2026/27"):
            continue
        n += 1
        upd: dict[str, str] = {}
        for side in ("home", "away"):
            if not blank(meta.get(f"{side}_team")):
                continue
            name = str(meta.get(f"{side}_team_name") or "")
            cid = t3_club_by_name(name)
            if cid:
                upd[f"{side}_team"] = str(cid)
            elif name:
                unmatched[name] = unmatched.get(name, 0) + 1
        if upd:
            wp.update_match_meta(int(m["id"]), upd)
            linked += 1
    log.info("matches 2026/27: %d | linked: %d", n, linked)
    log.info("still text-only (not T3 clubs - AFC, Laos, Cambodia, amateur cup sides...): %d names", len(unmatched))
    for k, v in sorted(unmatched.items(), key=lambda x: -x[1])[:60]:
        log.info("   %3d  %s", v, k)

    # 2. crests
    teams = json.loads((Path(__file__).resolve().parent / "data" / "t3_registry_teams.json").read_text())
    set_logo = 0
    for c in T3_CLUBS:
        if not c.wp:
            continue
        cur = wp.get_club(c.wp, fields="id,acf.club_logo_url,featured_media")
        if str((cur.get("acf") or {}).get("club_logo_url") or "").strip() or cur.get("featured_media"):
            continue
        logo = (teams.get(str(c.reg)) or {}).get("logo")
        if logo:
            wp.update_club(c.wp, acf={"club_logo_url": logo})
            set_logo += 1
    log.info("club crests set: %d", set_logo)
    Path("out").mkdir(exist_ok=True)
    Path("out/t3_unmatched_names.json").write_text(json.dumps(unmatched, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
