"""Thai League official registration data (thaileague.co.th's public API).

The league's own player registry: Thai-script names, the registered date of
birth and nationality, shirt numbers, and the official season photo (500px
transparent cut-out in the current kit). It is the authoritative source for
names, dates of birth and nationality; FotMob stays the source for match
data. Public, no key. Added 2026-10-22.

Photos: Simon chose the Thai League registration photos for player pages
(22 Oct 2026), knowing they belong to the league/club.
"""

from __future__ import annotations

import time
from typing import Any

import requests

TL_BASE = "https://competition.tl.prod.c0d1um.io/thaileague/api/"

# FOTMOB_TEAMS key -> Thai League registry club id (the `club` field on
# player-public/all_players_search). Add a club here to switch it on.
TL_CLUB_IDS: dict[str, int] = {
    "port": 46,
}


class ThaiLeague:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (footballsiam.com squad sync)"

    def club_players(self, club_id: int) -> list[dict[str, Any]]:
        """Every player currently registered to the club."""
        url = f"{TL_BASE}player-public/all_players_search/"
        params: dict[str, Any] | None = {"club": club_id, "page_size": 100}
        out: list[dict[str, Any]] = []
        while url:
            for attempt in range(3):
                r = self.s.get(url, params=params, timeout=30)
                if r.ok:
                    break
                time.sleep(2 * (attempt + 1))
            r.raise_for_status()
            d = r.json()
            out += [x for x in d.get("results", []) if x.get("club") == club_id]
            url, params = d.get("next"), None
        return out

    def photo_bytes(self, url: str) -> bytes:
        r = self.s.get(url, timeout=60)
        r.raise_for_status()
        return r.content


def has_thai(s: str | None) -> bool:
    return any("฀" <= ch <= "๿" for ch in str(s or ""))
