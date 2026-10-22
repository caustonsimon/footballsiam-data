"""Thai League 3 squads from the Thai League registry -> footballsiam.com.

Simon, 22 Oct 2026: build out all Thai League 3 clubs. FotMob does not cover
T3, so the league's own public registry (thaileague.py) is the only source:
registered squads with Thai and English names, date of birth, nationality,
shirt number, position, height and the official photo, plus per-competition
season totals (apps, starts, minutes, goals, cards). The registry hardly
records assists (4 against 108 goals in T1), so T3 pages show none.

Per club (69, in six regions):
  1. Registry squad for the T3 tournament (228) and the club's totals in the
     league, the FA Cup (230) and the League Cup (231).
  2. Each registered player is tied to ONE WordPress `player` post, never by
     name alone:
       a. a post already carrying this tl_player_id;
       b. a post with the same date of birth (exact, +-1 day, day/month swap)
          and a name that agrees strictly (word order ignored);
       c. the same name exactly anywhere on the site, date of birth missing or
          agreeing (a transfer never makes a duplicate);
       d. nothing -> a new player post.
     Two candidates, or a page FotMob already ties to a T1/T2 squad, are
     REPORTED and left alone.
  3. Matched/created posts get current_club, shirt number, position, height,
     tl_player_id, Thai name and date of birth (the registry is
     authoritative); nationality only where blank (Simon's corrections win);
     the registry photo as player_photo_url where the page has no photo.
  4. Pages whose current_club is this club but who are no longer registered
     (and are not FotMob-managed) have current_club cleared.
  5. Club hub feed (club meta fs_club_hub_json, same shape as
     fotmob_squads.py) and a profile feed per player (fs_player_hub_json).

Dry run by default; --live writes. --club <registry id|title> or all.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

import requests

import sync_matches  # noqa: F401  (loads .env)
from fotmob_squads import (NAT_FIX, POS_LABEL, SEASON_LABEL, dob_relation, iso_date, norm,
                           strict_name, unix_to_date)
from thaileague import TL_BASE, has_thai
from wp_client import WPClient, WPHostChallengeError

log = logging.getLogger("tl3_squads")

T3_TOURNAMENT = 228
CUPS = {230: "Thai FA Cup", 231: "Thai League Cup"}
REGION_LABEL = {
    "t3_north": "Thai League 3 North", "t3_north_east": "Thai League 3 North East",
    "t3_south": "Thai League 3 South", "t3_east": "Thai League 3 East",
    "t3_west": "Thai League 3 West", "t3_central": "Thai League 3 Central",
}


class T3Club(NamedTuple):
    reg: int            # Thai League registry club id
    wp: int | None      # WordPress club post (None: create)
    title: str          # house name (the club post's title)
    region: str         # competition slug on the site
    names: list[str]    # how the site's T3 fixtures name the club (first = displayed)


# Registry club -> site club, 22 Oct 2026. Names checked against the site's
# T3 fixtures and Snippet 153's FootyStats corrections; the seven clubs the
# registry names differently were confirmed by stadium / official page
# (FC Yala = Jalor City, facebook.com/jalorcity2018; Burapha United plays at
# Banbueng Stadium; Pluakdaeng United = FootyStats' "Rayong United"; UMT =
# "Ubon Ratchathani"; Nara FC = Narathiwat Stadium).
T3_CLUBS: list[T3Club] = [
    T3Club(24, 15106, 'Angthong', 't3_central', ['Angthong', 'Ang Thong']),
    T3Club(1240, 16192, 'Ayutthaya PK', 't3_central', ['Ayutthaya PK', 'Ayutthaya']),
    T3Club(295, 16186, 'Bangkapi', 't3_central', ['Bangkapi']),
    T3Club(84, 13887, 'Chamchuri United', 't3_central', ['Chamchuri United']),
    T3Club(885, 15103, 'Futera United', 't3_central', ['Futera United']),
    T3Club(73, 15102, 'Kasembundit University', 't3_central', ['Kasembundit University', 'Kasem Bundit University']),
    T3Club(227, 9730, 'Lopburi Poma', 't3_central', ['Lopburi Poma', 'Lopburi City']),
    T3Club(25, 13886, 'North Bangkok University', 't3_central', ['North Bangkok University']),
    T3Club(35, 9840, 'Pathumthani University', 't3_central', ['Pathumthani University', 'Pathum Thani University']),
    T3Club(55, 9774, 'Prime Bangkok', 't3_central', ['Prime Bangkok']),
    T3Club(181, 15104, 'Royal Thai Air Force', 't3_central', ['Royal Thai Air Force', 'Welfare Royal Air Force']),
    T3Club(1241, 15105, 'Singburi Warriors', 't3_central', ['Singburi Warriors', 'Singbury Warriors']),
    T3Club(56, 16213, 'Banbueng', 't3_east', ['Banbueng']),
    T3Club(996, 9739, 'Burapha United', 't3_east', ['Banbueng City', 'Burapha United']),
    T3Club(36, 15396, 'Chachoengsao Hi-Tek FC', 't3_east', ['Chachoengsao Hi-Tek FC', 'Cha Choeng Sao FC']),
    T3Club(129, 9741, 'Customs United', 't3_east', ['Customs United']),
    T3Club(899, 9766, 'Fleet United', 't3_east', ['Fleet United', 'Royal Thai Fleet']),
    T3Club(1372, 16222, 'MNK SC', 't3_east', ['MNK SC', 'MNK']),
    T3Club(37, 9772, 'Marines', 't3_east', ['Marines', 'Marines Eureka']),
    T3Club(112, 13900, 'Navy FC', 't3_east', ['Navy FC']),
    T3Club(874, 9833, 'Padriew City', 't3_east', ['Padriew City', 'Paet Riew City']),
    T3Club(902, 9751, 'Pattaya City', 't3_east', ['Pattaya City']),
    T3Club(74, 15394, 'Pluakdaeng United', 't3_east', ['Rayong United', 'Pluakdaeng United']),
    T3Club(508, 9842, 'Chattrakarn City', 't3_north', ['Chattrakarn City']),
    T3Club(117, 13872, 'Chiangmai', 't3_north', ['Chiangmai']),
    T3Club(521, 13873, 'Chiangrai', 't3_north', ['Chiangrai']),
    T3Club(53, 13871, 'Chiangrai City', 't3_north', ['Chiangrai City', 'Chiangrai TSC']),
    T3Club(34, 14967, 'Kamphaengphet', 't3_north', ['Kamphaengphet', 'Kamphaeng Phet']),
    T3Club(561, 9845, 'Khelang United', 't3_north', ['Khelang United']),
    T3Club(51, 13868, 'Maejo United', 't3_north', ['Maejo United']),
    T3Club(49, 14969, 'Northern Nakhonmaesot United', 't3_north', ['Northern Nakhonmaesot United', 'Nakhon Mae Sot United']),
    T3Club(498, 16211, 'Paknampho', 't3_north', ['Paknampho', 'Paknampho NSRU']),
    T3Club(1215, 9788, 'Phichit United', 't3_north', ['Phichit United']),
    T3Club(39, 9778, 'Phitsanulok', 't3_north', ['Phitsanulok']),
    T3Club(1371, 16205, 'Thailand National Sports University Lampang Campus', 't3_north', ['Thailand National Sports University Lampang Campus', 'TNSU Lampang']),
    T3Club(1375, 16197, 'Esan Fighter', 't3_north_east', ['Esan Fighter']),
    T3Club(118, 9775, 'Khon Kaen', 't3_north_east', ['Khon Kaen', 'Khonkaen']),
    T3Club(104, 13877, 'Khonkaen Mordindang', 't3_north_east', ['Khonkaen Mordindang']),
    T3Club(562, 13882, 'Korat City', 't3_north_east', ['Korat City', 'Suranaree Black Cat']),
    T3Club(338, 16203, 'Pitchaya Bundit College', 't3_north_east', ['Pitchaya Bundit College']),
    T3Club(426, 9783, 'Roi Et', 't3_north_east', ['Roi Et']),
    T3Club(99, 13875, 'Surin City', 't3_north_east', ['Surin City', 'Surin']),
    T3Club(929, 13878, 'The Eastern University of Management and Technology', 't3_north_east', ['Ubon Ratchathani', 'The Eastern University of Management and Technology']),
    T3Club(41, 13883, 'Ubon Kruanapat', 't3_north_east', ['Ubon Kruanapat', 'Ubon Eastern']),
    T3Club(335, 9838, 'Udon Banjan United', 't3_north_east', ['Udon Banjan United', 'Udon Banchan']),
    T3Club(178, 9755, 'Udon United', 't3_north_east', ['Udon United']),
    T3Club(98, 13880, 'Yasothon', 't3_north_east', ['Yasothon']),
    T3Club(1208, 13864, 'Chumphon United', 't3_south', ['Chumphon United']),
    T3Club(878, 13867, 'Muang Trang United', 't3_south', ['Muang Trang United']),
    T3Club(90, 7957, 'Nakhon Si United', 't3_south', ['Nakhon Si United', 'Nakhon Si Thammarat']),
    T3Club(1360, 16177, 'Nara', 't3_south', ['Narathiwat', 'Nara']),
    T3Club(175, 13866, 'Phuket Andaman', 't3_south', ['Phuket Andaman']),
    T3Club(89, 13865, 'Ranong United', 't3_south', ['Ranong United']),
    T3Club(393, 9734, 'Samui United', 't3_south', ['Samui United']),
    T3Club(80, 9728, 'Surat Thani City', 't3_south', ['Surat Thani City']),
    T3Club(87, 14873, 'Yala', 't3_south', ['Jalor City', 'Yala']),
    T3Club(1157, 13863, 'Yala City', 't3_south', ['Yala City']),
    T3Club(62, 13893, 'Assumption United', 't3_west', ['Assumption United']),
    T3Club(27, 7950, 'Bangkok', 't3_west', ['Bangkok']),
    T3Club(66, 16058, 'Huahin City', 't3_west', ['Huahin City', 'Hua Hin City']),
    T3Club(92, 9777, 'Muangnont', 't3_west', ['Muangnont']),
    T3Club(1366, 16179, 'Phetchaburi', 't3_west', ['Phetchaburi']),
    T3Club(76, 13891, 'Rajpracha', 't3_west', ['Rajpracha']),
    T3Club(91, 13895, 'Royal Thai Army', 't3_west', ['Royal Thai Army', 'Army']),
    T3Club(879, 16059, 'Samutsakhon City', 't3_west', ['Samutsakhon City', 'Samut Sakhon City']),
    T3Club(946, 9784, 'Samutsongkhram City', 't3_west', ['Samutsongkhram City', 'Samut Songkhram City']),
    T3Club(67, None, 'Suphanburi', 't3_west', ['Suphanburi']),
    T3Club(398, 9835, 'Thapluang United', 't3_west', ['Thapluang United', 'Thap Luang United']),
    T3Club(96, 9828, 'Thonburi United', 't3_west', ['Thonburi University', 'Thonburi United']),
]

# Thai-script names for registered players the registry holds only in English
# (foreign players), keyed by registry player id. Football Siam
# transliterations; a name_th on the page wins.
try:
    THAI_NAMES_T3: dict[str, str] = json.loads((Path(__file__).resolve().parent / "data" / "thai_names_t3.json").read_text())["names"]
except FileNotFoundError:
    THAI_NAMES_T3 = {}

LOWER = {"da", "de", "do", "dos", "das", "van", "von", "der", "den", "di", "del", "la", "le", "bin", "al"}


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

class Registry:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (footballsiam.com T3 squad sync)"

    def players(self, tournament: int, club: int) -> list[dict[str, Any]]:
        url: str | None = f"{TL_BASE}player-public/all_players_search/"
        params: dict[str, Any] | None = {"tournament": tournament, "club": club, "page_size": 100}
        out: list[dict[str, Any]] = []
        while url:
            for attempt in range(4):
                try:
                    r = self.s.get(url, params=params, timeout=40)
                    if r.ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(3 * (attempt + 1))
            r.raise_for_status()
            d = r.json()
            out += [x for x in d.get("results", []) if x.get("club") == club]
            url, params = d.get("next"), None
        return out

    def teams(self, tournament: int) -> dict[int, dict[str, Any]]:
        r = self.s.get(f"{TL_BASE}tournament-team-public/", params={"tournament": tournament, "page_size": 100},
                       timeout=60)
        r.raise_for_status()
        return {int(t["club"]): t for t in r.json()}


def clean_latin(s: str) -> str:
    """Drop stray Thai marks from an English name ('๋James oise Jesuikhode')."""
    c = re.sub(r"[฀-๿]", "", str(s or ""))
    return " ".join(c.split())


def nice_name(s: str) -> str:
    """'NATTHAWUT PHUBUN' -> 'Natthawut Phubun'; keeps mixed-case input as it is."""
    s = clean_latin(s)
    if not s:
        return s
    words = s.split()
    s = " ".join(w for i, w in enumerate(words) if i == 0 or w.lower() != words[i - 1].lower())  # "Kennedy Kennedy"
    letters = [ch for ch in s if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) < 0.6 and s != s.lower():
        # already mixed case as written ("Zaw Min Tun"); fix stray all-caps words ("George Kehinde OLATUNDE")
        return " ".join(w.title() if len(w) > 3 and w.isupper() else w for w in s.split())
    out = []
    for i, w in enumerate(s.lower().split()):
        if i and w in LOWER:
            out.append(w)
        else:
            out.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(out)


def reg_stats(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if not r or not r.get("match_count"):
        return None
    red = int(r.get("red_card_serious_foul_play") or 0) + int(r.get("red_card_violent_conduct") or 0) \
        + int(r.get("second_yellow_count") or 0)
    return {"apps": int(r.get("match_count") or 0), "starts": int(r.get("total_line_up") or 0),
            "mins": int(r.get("total_time") or 0), "goals": int(r.get("goal_for") or 0),
            "assists": 0, "yellow": int(r.get("yellow_card") or 0), "red": red, "rating": None}


def add(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    if not a:
        return dict(b) if b else None
    if not b:
        return a
    return {k: (a[k] or 0) + (b[k] or 0) if k != "rating" else None for k in a}


def srt(s: str) -> str:
    return " ".join(sorted(s.lower().replace("-", " ").split()))


def name_agrees(a: str, b: str) -> float:
    return max(strict_name(a, b), strict_name(srt(a), srt(b)))


def name_key(s: str) -> str:
    return " ".join(sorted(norm(s).replace("-", " ").split()))


def other_reg(p: dict[str, Any], rid: str) -> bool:
    """The page already belongs to a DIFFERENT registered player (its
    tl_player_id is someone else's): twins share a birthday and nearly a name
    (Kittikon / Kittiphong Khetpara, Thitathorn / Thitawee Auksornsri)."""
    t = str((p.get("meta") or {}).get("tl_player_id") or "").strip()
    return bool(t) and t != rid


def title_of(p: dict[str, Any]) -> str:
    import html
    return html.unescape((p.get("title") or {}).get("rendered", "") or "")


def club_ids(v: Any) -> set[int]:
    out: set[int] = set()
    for x in (v if isinstance(v, list) else [v]):
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    return out


# --------------------------------------------------------------------------
# One club
# --------------------------------------------------------------------------

def run_club(c: T3Club, wp: WPClient, reg: Registry, team: dict[str, Any] | None,
             wp_players: list[dict[str, Any]], by_tl: dict[str, dict[str, Any]],
             claimed: set[int], report: dict[str, Any], idx: dict[str, Any]) -> None:
    rep: dict[str, Any] = {"club": c.title, "matched": [], "created": [], "review": [], "left": []}
    report[c.title] = rep
    league = REGION_LABEL[c.region]
    log.info("=== %s (%s, registry %s)", c.title, league, c.reg)

    wp_club = c.wp
    if not wp_club:
        resp = wp._request("POST", "wp/v2/club", json={"title": c.title, "status": "publish"}) if not wp.dry_run else None
        wp_club = int(wp._json_or_raise(resp, "create club")["id"]) if resp is not None else None
        rep["club_created"] = wp_club or "(dry run)"
        log.info("  club page created: %s", wp_club)

    squad = reg.players(T3_TOURNAMENT, c.reg)
    cups: dict[str, dict[int, dict[str, Any]]] = {}
    for tid, label in CUPS.items():
        try:
            cups[label] = {int(r["id"]): r for r in reg.players(tid, c.reg)}
        except Exception as exc:  # noqa: BLE001
            log.warning("  %s stats failed: %s", label, exc)
            cups[label] = {}
    rep["registered"] = len(squad)

    hub_squad: list[dict[str, Any]] = []
    in_squad: set[int] = set()
    for r in squad:
        rid = str(r["id"])
        name_en = nice_name(r.get("full_name_en") or "")
        name_th = " ".join(str(r.get("full_name") or "").split()) if has_thai(r.get("full_name")) else ""
        name_th = name_th or THAI_NAMES_T3.get(rid, "")
        dob = iso_date(r.get("birth_date"))
        raw_nat = ((r.get("nationality") or {}).get("name") or "").strip()
        nat = NAT_FIX.get(raw_nat, raw_nat)
        pos = (r.get("position_short_name") or "").upper()
        pos = pos if pos in POS_LABEL else ""

        # -- match to a player page -----------------------------------------
        hit, how = by_tl.get(rid), "tl_player_id"
        if hit is None:
            how = ""
            cands = []
            near = []
            if dob:
                for d in {dob, dob - dt.timedelta(days=1), dob + dt.timedelta(days=1)} | (
                        {dt.date(dob.year, dob.day, dob.month)} if dob.day <= 12 else set()):
                    near += idx["dob"].get(d, [])
            for p in near:
                if int(p["id"]) in claimed or other_reg(p, rid):
                    continue
                rel = dob_relation(dob, unix_to_date((p.get("meta") or {}).get("birthday")))
                if not rel:
                    continue
                sc = name_agrees(name_en, title_of(p))
                if sc >= (0.75 if rel == "exact" else 0.82):
                    cands.append((sc, p))
            cands.sort(key=lambda x: -x[0])
            if len(cands) == 1 or (len(cands) > 1 and cands[0][0] - cands[1][0] >= 0.2):
                hit, how = cands[0][1], "dob+name"
            elif cands:
                rep["review"].append({"player": name_en, "tl_id": rid, "why": "several pages share this birthday and name",
                                      "candidates": [(x[1]["id"], title_of(x[1])) for x in cands]})
                continue
        if hit is None:
            cands = []
            for p in idx["name"].get(name_key(name_en), []):
                if int(p["id"]) in claimed or other_reg(p, rid):
                    continue
                sdob = unix_to_date((p.get("meta") or {}).get("birthday"))
                if sdob and dob and not dob_relation(dob, sdob):
                    continue
                cands.append(p)
            if len(cands) == 1:
                hit, how = cands[0], "exact name on site"
            elif len(cands) > 1:
                rep["review"].append({"player": name_en, "tl_id": rid, "why": "several pages carry this exact name",
                                      "candidates": [(x["id"], title_of(x)) for x in cands]})
                continue
        if hit is not None and str((hit.get("meta") or {}).get("fotmob_id") or "").strip():
            # FotMob-managed page (T1/T2 squad): never take it over from here.
            rep["review"].append({"player": name_en, "tl_id": rid, "why":
                                  f"page {hit['id']} ({title_of(hit)}) belongs to a T1/T2 squad (FotMob)"})
            continue
        if hit is not None and int(hit["id"]) in claimed:
            rep["review"].append({"player": name_en, "tl_id": rid, "why": f"page {hit['id']} already claimed"})
            continue

        acf: dict[str, Any] = {"current_club": [wp_club] if wp_club else []}
        if r.get("shirt_number") not in (None, ""):
            acf["shirt_number"] = str(r["shirt_number"])
        if pos:
            acf["position"] = pos
        if r.get("height"):
            acf["height_cm"] = str(r["height"])
        meta: dict[str, Any] = {"tl_player_id": rid}
        if dob:
            meta["birthday"] = str(int(dt.datetime(dob.year, dob.month, dob.day, tzinfo=dt.timezone.utc).timestamp()))
        photo = r.get("photo") or ""

        if hit is not None:
            wp_id = int(hit["id"])
            claimed.add(wp_id)
            hm, ha = hit.get("meta") or {}, hit.get("acf") or {}
            stored_th = str(hm.get("name_th") or "").strip()
            if name_th and not stored_th:
                meta["name_th"] = name_th
            name_th = stored_th or name_th
            old_nat = str(ha.get("nationality") or "").strip()
            if nat and not old_nat:
                acf["nationality"] = nat
            elif old_nat and nat and old_nat != nat:
                rep.setdefault("nationality_differs", []).append({"player": title_of(hit), "site": old_nat, "registry": nat})
            nat = old_nat or nat
            if photo and not hit.get("featured_media") and not str(ha.get("player_photo_url") or "").strip():
                acf["player_photo_url"] = photo
                meta["tl_photo_src"] = photo
            title = title_of(hit)
            wp.update_player(wp_id, meta=meta, acf=acf)
            rep["matched"].append({"wp": wp_id, "site": title, "registry": name_en, "how": how})
        else:
            parts = name_en.split()
            acf_new = {**acf, "first_name": parts[0] if parts else "", "last_name": " ".join(parts[1:]),
                       "nationality": nat}
            if photo:
                acf_new["player_photo_url"] = photo
                meta["tl_photo_src"] = photo
            if name_th:
                meta["name_th"] = name_th
            resp = wp.create_player({"title": name_en, "status": "publish", "acf": acf_new, "meta": meta})
            wp_id = None if wp.dry_run else int(resp["id"])
            if wp_id:
                claimed.add(wp_id)
            title = name_en
            rep["created"].append({"wp": wp_id, "registry": name_en, "dob": str(dob) if dob else None})
        if wp_id:
            in_squad.add(wp_id)

        by_comp: dict[str, Any] = {}
        lg = reg_stats(r)
        if lg:
            by_comp[league] = lg
        for label, rows in cups.items():
            s = reg_stats(rows.get(int(r["id"])))
            if s:
                by_comp[label] = s
        total = None
        for s in by_comp.values():
            total = add(total, s)

        entry = {
            "wp_id": wp_id, "tl_id": int(r["id"]), "name": title, "number": r.get("shirt_number"),
            "pos": pos, "pos_label": POS_LABEL.get(pos, ""), "nat": nat, "name_th": name_th,
            "dob": dob.isoformat() if dob else None, "height": r.get("height"), "weight": r.get("weight"),
            "injury": False, "stats": total, "by_comp": by_comp, "photo_src": photo,
        }
        hub_squad.append(entry)

        if wp_id:
            ph = {
                "source": "Thai League", "no_assists": True,
                "name": title, "name_th": name_th, "number": r.get("shirt_number"), "pos": pos,
                "pos_label": POS_LABEL.get(pos, ""), "nat": nat, "dob": entry["dob"],
                "height": r.get("height"), "weight": r.get("weight"), "foot": None,
                "club": c.title, "wp_club": wp_club, "league": league, "season_label": SEASON_LABEL,
                "season": total, "by_comp": by_comp, "matches": [], "deep": {}, "shots": [], "career": [],
                "intl": [], "trophies": [], "next": None, "injury": None, "captain": False,
                "contract_end": None, "positions": [], "primary_position": None,
                "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d %H:%M"),
            }
            wp.update_player(wp_id, meta={"fs_player_hub_json": json.dumps(ph, ensure_ascii=False)})

    # -- leavers ---------------------------------------------------------------
    if wp_club:
        for p in wp_players:
            pid = int(p["id"])
            if pid in in_squad or pid in claimed or wp_club not in club_ids((p.get("acf") or {}).get("current_club")):
                continue
            if str((p.get("meta") or {}).get("fotmob_id") or "").strip():
                continue
            wp.update_player(pid, acf={"current_club": None}, meta={"fs_player_hub_json": ""})
            rep["left"].append({"wp": pid, "site": title_of(p)})

    hub = {
        "club": c.title, "wp_club": wp_club, "source": "Thai League", "no_assists": True,
        "league": league, "league_slug": c.region, "match_names": c.names,
        "season": SEASON_LABEL, "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d %H:%M"),
        "squad": hub_squad, "team": {"by_comp": {}, "matches": []},
        "venue": (team or {}).get("stadium_en"), "logo": (team or {}).get("logo"),
        "stadium_th": (team or {}).get("stadium"),
    }
    if wp_club:
        wp.update_club(wp_club, meta={"fs_club_hub_json": json.dumps(hub, ensure_ascii=False)})
    rep["squad_size"] = len(hub_squad)
    out = Path("out"); out.mkdir(exist_ok=True)
    (out / f"t3hub_{re.sub(r'[^a-z0-9]+', '-', c.title.lower()).strip('-')}.json").write_text(
        json.dumps(hub, ensure_ascii=False, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--club", default="all", help="registry club id, club title, or 'all'")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    clubs = T3_CLUBS if args.club == "all" else [c for c in T3_CLUBS if str(c.reg) == args.club or c.title == args.club]
    if not clubs:
        log.error("unknown club %s", args.club)
        return 2
    wp = WPClient(dry_run=not args.live)
    reg = Registry()
    log.info("Mode: %s, %d clubs", "LIVE" if args.live else "DRY RUN", len(clubs))
    teams = reg.teams(T3_TOURNAMENT)
    wp_players = list(wp.iter_all_players(
        fields="id,title,acf.current_club,acf.nationality,acf.player_photo_url,meta.birthday,"
               "meta.fotmob_id,meta.tl_player_id,meta.name_th,featured_media"))
    log.info("WP players: %d", len(wp_players))
    by_tl: dict[str, dict[str, Any]] = {}
    for p in wp_players:
        t = str((p.get("meta") or {}).get("tl_player_id") or "").strip()
        if t:
            by_tl[t] = p
    idx: dict[str, Any] = {"dob": {}, "name": {}}
    for p in wp_players:
        d = unix_to_date((p.get("meta") or {}).get("birthday"))
        if d:
            idx["dob"].setdefault(d, []).append(p)
        idx["name"].setdefault(name_key(title_of(p)), []).append(p)
    # Pages the T1/T2 sync owns are never claimed here.
    claimed: set[int] = set()

    report: dict[str, Any] = {}
    try:
        for c in clubs:
            try:
                run_club(c, wp, reg, teams.get(c.reg), wp_players, by_tl, claimed, report, idx)
            except WPHostChallengeError:
                raise
            except Exception as exc:  # noqa: BLE001 - one club must not stop the rest
                log.error("%s FAILED: %s", c.title, exc)
                report.setdefault(c.title, {"club": c.title, "matched": [], "created": [], "review": [], "left": []})["error"] = str(exc)
    except WPHostChallengeError:
        log.error("SiteGround challenge - nothing more written")
        return 3

    Path("out").mkdir(exist_ok=True)
    Path("out/t3_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    tot = {"squad": 0, "matched": 0, "created": 0, "review": 0, "left": 0}
    for r in report.values():
        tot["squad"] += r.get("squad_size") or 0
        for k in ("matched", "created", "review", "left"):
            tot[k] += len(r[k])
        log.info("%s: squad %s | matched %d | created %d | review %d | left %d%s", r["club"], r.get("squad_size"),
                 len(r["matched"]), len(r["created"]), len(r["review"]), len(r["left"]),
                 f" | ERROR {r['error']}" if r.get("error") else "")
        for x in r["review"]:
            log.warning("  REVIEW %s", x)
    log.info("TOTAL %s", tot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
