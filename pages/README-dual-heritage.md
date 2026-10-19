# Dual Heritage / Thais Abroad map pages

- `build_map.py` builds `dual.html` (page 34128) and `abroad.html` (page 17057) from `data/`.
- `data/verified_all.json`: dual heritage players (one object per player; `include` true to show).
- `data/full_thai_abroad.json`: Thai players at foreign clubs.
- `data/ig_seen_posts.json`: Instagram post codes from @half_thailand.123 already processed.
- `data/under15_watchlist.json`: players left out for being under 15; add them once they turn 15.
- Hand-added players (Duchownys, Siam Yapp) and photos (IMGS) are in `build_map.py`.
- Run: `OUT=/tmp python3 pages/build_map.py`. Publish per the Projects doc `claude/tasks/dual-heritage-weekly.md`.
- English/Thai tabs (19 Oct 2026): Thai text for every row lives in `data/th_rows.json` (keyed by row key: n, tl, pos, lg, her, th, oi, bio). Page UI strings, Thai country names and level labels are in `build_map.py` (UI_COMMON, CN_TH, LV_TH and the `th` dicts in `build()`). New players without a Thai entry show in English on the Thai tab; the build prints them, so add their translation to `th_rows.json` before publishing. `?lang=th` opens a page in Thai; the choice is remembered per browser.
