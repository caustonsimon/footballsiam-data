"""Player photos -> transparent cut-outs (Simon, 23 Oct 2026: "all photos of
players need background removed").

Most Thai League 1 registry photos are already transparent cut-outs, but the
T2 and T3 clubs uploaded theirs with studio or pitch backgrounds. For every
player page with a current club and a photo:

  1. skip if the photo is already one of ours (file name contains "-cutout");
  2. download it; skip if the corners are already transparent;
  3. remove the background (rembg, isnet-general-use), trim to a square
     400 x 400 WebP with alpha;
  4. upload it to the media library and point the page at it (featured image
     + player_photo_url). tl_photo_src is left alone, so the squad syncs do
     not re-upload the original.

Dry run by default (writes the cut-outs to out/cutouts/ for review);
--live uploads. --shard i/n splits the players across parallel jobs.
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image

import sync_matches  # noqa: F401  (loads .env)
from wp_client import WPClient, WPHostChallengeError

log = logging.getLogger("photo_cutout")
UA = {"User-Agent": "Mozilla/5.0 (footballsiam.com photo cut-out)"}
SIZE = 400


def has_background(im: Image.Image) -> bool:
    im = im.convert("RGBA")
    w, h = im.size
    pts = [(2, 2), (w - 3, 2), (2, h // 2), (w - 3, h // 2)]
    return sum(1 for p in pts if im.getpixel(p)[3] > 200) >= 3


def cutout(im: Image.Image, session: Any) -> Image.Image:
    from rembg import remove
    out = remove(im.convert("RGBA"), session=session, post_process_mask=True)
    # square canvas, figure centred horizontally, bottom kept (shirt to edge)
    w, h = out.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(out, ((side - w) // 2, side - h), out)
    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


def current_photo(p: dict[str, Any], media_url: dict[int, str]) -> str:
    fm = int(p.get("featured_media") or 0)
    if fm and media_url.get(fm):
        return media_url[fm]
    return str((p.get("acf") or {}).get("player_photo_url") or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    si, sn = (int(x) for x in args.shard.split("/"))

    wp = WPClient(dry_run=not args.live)
    players = [p for p in wp.iter_all_players(fields="id,title,slug,featured_media,acf.current_club,acf.player_photo_url")
               if (p.get("acf") or {}).get("current_club") and int(p["id"]) % sn == si]
    log.info("players with a current club in shard %s: %d", args.shard, len(players))

    # featured image URLs, fetched in batches of 100
    ids = sorted({int(p["featured_media"]) for p in players if p.get("featured_media")})
    media_url: dict[int, str] = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        r = wp._request("GET", "wp/v2/media", params={"include": ",".join(map(str, chunk)), "per_page": 100,
                                                       "_fields": "id,source_url"})
        for m in wp._json_or_raise(r, "media batch"):
            media_url[int(m["id"])] = m["source_url"]

    from rembg import new_session
    session = new_session("isnet-general-use")
    out = Path("out/cutouts"); out.mkdir(parents=True, exist_ok=True)
    s = requests.Session(); s.headers.update(UA)
    rep = {"done": 0, "already_cutout": 0, "transparent": 0, "no_photo": 0, "failed": 0}
    n = 0
    for p in players:
        url = current_photo(p, media_url)
        if not url:
            rep["no_photo"] += 1
            continue
        if "-cutout" in url:
            rep["already_cutout"] += 1
            continue
        try:
            r = s.get(url, timeout=40)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content))
            if not has_background(im):
                rep["transparent"] += 1
                continue
            cut = cutout(im, session)
            slug = re.sub(r"[^a-z0-9-]+", "-", str(p.get("slug") or p["id"]).lower()).strip("-") or str(p["id"])
            fn = out / f"{slug}-cutout.webp"
            cut.save(fn, "WEBP", quality=86, method=6)
            if args.live:
                title = (p.get("title") or {}).get("rendered", "") or slug
                media = wp.upload_media(str(fn), title=f"{title} (cut-out)", alt_text=title)
                wp._json_or_raise(wp._request("POST", f"wp/v2/player/{p['id']}", json={
                    "featured_media": int(media["id"]),
                    "acf": {"player_photo_url": media.get("source_url", "")}}), f"photo {p['id']}")
                fn.unlink(missing_ok=True)  # keep the artifact small on live runs
            rep["done"] += 1
            n += 1
            if n % 50 == 0:
                log.info("  %d cut-outs (%s)", n, rep)
            if args.limit and n >= args.limit:
                break
        except WPHostChallengeError:
            log.error("SiteGround challenge - stopping")
            return 3
        except Exception as exc:  # noqa: BLE001 - one photo must not stop the run
            rep["failed"] += 1
            log.warning("  %s (%s): %s", p["id"], url[-60:], exc)
            time.sleep(1)
    log.info("DONE %s", rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
