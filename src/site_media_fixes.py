#!/usr/bin/env python3
"""Site media fixes (added 2026-11-05). Two re-runnable parts:

1. Crests: every club whose `club_logo_url` points off the site (the data
   provider media.api-sports.io, FotMob, the Thai League registry) gets the
   crest copied into the WordPress media library as
   `club-crest-<slug>.<ext>`, with alt text "<Club> crest", and
   `club_logo_url` repointed at the copy. A crest already copied is found
   by filename and reused, so a re-run uploads nothing twice. Clubs with no
   crest, or a crest already on footballsiam.com, are left alone.
   Featured images are NOT set: the older Divi club template may show a
   featured image at the top of the page.

2. Alt text: every media-library image with empty alt text gets one, by
   these rules, first match wins:
     a. it is a player's featured image          -> player name
     b. it is a club crest copied by part 1      -> "<Club> crest"
     c. it is attached to a post/page/club/player/match -> that item's title
     d. its own title is real words (2+ words, no long digit runs or hashes)
                                                 -> the title, tidied
   Anything else is left blank and counted, never guessed.

    python site_media_fixes.py                 # dry run, both parts
    python site_media_fixes.py --live          # write
    python site_media_fixes.py --only crests   # or --only alt
"""
from __future__ import annotations

import argparse
import html
import logging
import mimetypes
import os
import re
import sys
import tempfile
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wp_client import WPClient  # noqa: E402

log = logging.getLogger("site_media_fixes")
SITE_HOST = "footballsiam.com"
DL_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FootballSiamMediaCopy/1.0)"}


def title_of(post: dict[str, Any]) -> str:
    t = post.get("title")
    if isinstance(t, dict):
        t = t.get("raw") or t.get("rendered") or ""
    return html.unescape(str(t or "")).strip()


def paged(wp: WPClient, route: str, params: dict[str, Any]):
    page = 1
    while True:
        resp = wp._request("GET", route, params={**params, "per_page": 100, "page": page})
        if resp.status_code == 400 and "invalid_page_number" in resp.text:
            return
        batch = wp._json_or_raise(resp, f"GET {route} page {page}")
        if not batch:
            return
        yield from batch
        if page >= int(resp.headers.get("X-WP-TotalPages", "1") or "1"):
            return
        page += 1


def is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == SITE_HOST or host.endswith("." + SITE_HOST)


# ---------------------------------------------------------------- crests
def copy_crests(wp: WPClient, stats: Counter) -> dict[int, str]:
    """Returns {attachment id: club name} for every crest in the library."""
    crest_ids: dict[int, str] = {}
    clubs = list(paged(wp, "wp/v2/club", {
        "status": "publish", "context": "edit", "orderby": "id", "order": "asc",
        "_fields": "id,title,slug,acf.club_logo_url",
    }))
    log.info("%d clubs", len(clubs))
    for c in clubs:
        cid, name, slug = int(c["id"]), title_of(c), c.get("slug") or str(c["id"])
        url = str(((c.get("acf") or {}).get("club_logo_url")) or "").strip()
        if not url:
            stats["crest: none"] += 1
            continue
        if is_local(url):
            stats["crest: already on site"] += 1
            continue
        ext = os.path.splitext(urlparse(url).path)[1].lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
            ext = ".png"
        fname = f"club-crest-{slug}{ext}"
        alt = f"{name} crest"

        # Reuse a copy made on an earlier run (matched by exact filename).
        existing = None
        resp = wp._request("GET", "wp/v2/media", params={
            "search": f"club-crest-{slug}", "per_page": 20, "_fields": "id,source_url,alt_text"})
        if resp.ok:
            for m in resp.json():
                if os.path.basename(urlparse(m["source_url"]).path).startswith(f"club-crest-{slug}."):
                    existing = m
                    break
        if existing:
            new_url, mid = existing["source_url"], int(existing["id"])
            stats["crest: reused earlier copy"] += 1
        else:
            try:
                r = requests.get(url, headers=DL_HEADERS, timeout=30)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if not ctype.startswith("image/") or len(r.content) < 200:
                    raise ValueError(f"not an image ({ctype}, {len(r.content)} bytes)")
            except Exception as exc:  # noqa: BLE001
                log.warning("  SKIP %s (%d): download failed %s: %s", name, cid, url, exc)
                stats["crest: download failed"] += 1
                continue
            real_ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ext
            if real_ext == ".jpe":
                real_ext = ".jpg"
            fname = f"club-crest-{slug}{real_ext}"
            if wp.dry_run:
                log.info("  WOULD COPY %-32s %s", name, url)
                stats["crest: would copy"] += 1
                continue
            with tempfile.NamedTemporaryFile(suffix=real_ext, delete=False) as fh:
                fh.write(r.content)
                tmp = fh.name
            try:
                media = wp.upload_media(tmp, filename=fname, title=alt, alt_text=alt)
            finally:
                os.unlink(tmp)
            new_url, mid = media["source_url"], int(media["id"])
            stats["crest: copied"] += 1
            log.info("  COPIED %-32s -> %s", name, new_url)
        crest_ids[mid] = name
        if not wp.dry_run:
            wp.update_club(cid, acf={"club_logo_url": new_url})
            stats["crest: club repointed"] += 1
    return crest_ids


# ---------------------------------------------------------------- alt text
WORDY = re.compile(r"[A-Za-z฀-๿]{2,}")
JUNK = re.compile(r"\d{4,}|[0-9a-f]{8,}|^(img|dsc|screenshot|image|photo|untitled)\b", re.I)


def tidy_title(t: str) -> str | None:
    t = re.sub(r"[-_]+", " ", t).strip()
    t = re.sub(r"\s+(scaled|e\d+|\d+x\d+)$", "", t, flags=re.I)
    words = WORDY.findall(t)
    if len(words) < 2 or JUNK.search(t):
        return None
    return t[:1].upper() + t[1:]


def fill_alt(wp: WPClient, crest_ids: dict[int, str], stats: Counter) -> None:
    titles: dict[int, str] = {}
    player_feat: dict[int, str] = {}
    for route, fields in (("wp/v2/player", "id,title,featured_media"),
                          ("wp/v2/club", "id,title"),
                          ("wp/v2/posts", "id,title"),
                          ("wp/v2/pages", "id,title"),
                          ("wp/v2/match", "id,title")):
        n = 0
        for p in paged(wp, route, {"status": "publish", "context": "edit", "_fields": fields}):
            titles[int(p["id"])] = title_of(p)
            if route.endswith("player") and p.get("featured_media"):
                player_feat[int(p["featured_media"])] = title_of(p)
            n += 1
        log.info("  %s: %d titles", route, n)

    # Crests copied on an earlier run (part 1 skipped or found nothing new).
    for m in paged(wp, "wp/v2/media", {"search": "club-crest-", "_fields": "id,title,source_url"}):
        if os.path.basename(urlparse(m["source_url"]).path).startswith("club-crest-"):
            crest_ids.setdefault(int(m["id"]), re.sub(r"\s+crest$", "", title_of(m)))

    for m in paged(wp, "wp/v2/media", {"media_type": "image", "context": "edit",
                                        "_fields": "id,title,alt_text,post,source_url"}):
        stats["alt: images checked"] += 1
        if str(m.get("alt_text") or "").strip():
            stats["alt: already had one"] += 1
            continue
        mid = int(m["id"])
        parent = int(m.get("post") or 0)
        if mid in player_feat:
            alt, why = player_feat[mid], "player photo"
        elif mid in crest_ids:
            alt, why = f"{crest_ids[mid]} crest", "crest"
        elif parent and titles.get(parent):
            alt, why = titles[parent], "attached item title"
        else:
            t = tidy_title(title_of(m))
            alt, why = (t, "own title") if t else (None, "")
        if not alt:
            stats["alt: left blank (no safe source)"] += 1
            continue
        alt = alt[:250]
        stats[f"alt: set from {why}"] += 1
        if wp.dry_run:
            if stats[f"alt: set from {why}"] <= 5:
                log.info("  WOULD SET %d [%s] %r", mid, why, alt)
            continue
        resp = wp._request("POST", f"wp/v2/media/{mid}", json={"alt_text": alt})
        if not resp.ok:
            stats["alt: write failed"] += 1
            log.warning("  FAIL media %d: HTTP %s", mid, resp.status_code)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--only", choices=["crests", "alt"])
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    wp = WPClient(dry_run=not a.live)
    log.info("Site media fixes - %s", "LIVE" if a.live else "DRY RUN (no writes)")
    stats: Counter = Counter()
    crest_ids: dict[int, str] = {}
    if a.only in (None, "crests"):
        crest_ids = copy_crests(wp, stats)
    if a.only in (None, "alt"):
        fill_alt(wp, crest_ids, stats)
    log.info("Summary")
    for k in sorted(stats):
        log.info("  %-40s %d", k, stats[k])
    return 0


if __name__ == "__main__":
    sys.exit(main())
