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
    "port": 46, "buriram-united": 11, "bangkok-united": 40, "pathum-united": 21,
    "ratchaburi": 109, "chiangrai-united": 26, "chonburi": 18, "prachuap": 107,
    "rayong": 31, "uthai-thani": 114, "sisaket-united": 100, "rasisalai-united": 196,
    "lamphun-warrior": 45, "sukhothai": 108, "ayutthaya-united": 128, "pattani": 82,
    # Thai League 2 (tournament-team-public/?tournament=226, 22 Oct 2026)
    "muangthong-united": 106, "nakhon-ratchasima": 50, "kanchanaburi": 180,
    "nong-bua-pitchaya": 119, "police-tero": 94, "chainat": 111, "khon-kaen-united": 48,
    "mahasarakham": 63, "phrae-united": 42, "chiangmai-united": 127, "pattaya-united": 77,
    "songkhla": 182, "chanthaburi": 23, "nakhon-pathom": 19, "kasetsart": 126,
    "uttaradit": 47, "satun": 1158, "muang-loei-united": 102,
}


class ThaiLeague:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (footballsiam.com squad sync)"

    def club_players(self, club_id: int, tournament: int | None = None) -> list[dict[str, Any]]:
        """Every player currently registered to the club. With `tournament`, the
        rows also carry that competition's season totals (match_count,
        total_time, goal_for, cards): 224 T1, 226 T2, 228 T3, 230 FA Cup,
        231 League Cup (2026/27)."""
        url = f"{TL_BASE}player-public/all_players_search/"
        params: dict[str, Any] | None = {"club": club_id, "page_size": 100}
        if tournament:
            params["tournament"] = tournament
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
