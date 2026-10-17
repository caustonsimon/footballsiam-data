#!/usr/bin/env python3
"""
Football Siam — Team of the Week graphic generator.

Usage:
    python3 totw_graphic.py <selection.json> <output.png>

<selection.json> is the JSON block from claude/totw-current-selection.md
(the Claude Project doc), e.g.:
{
  "matchweek": 3,
  "formation": "4-3-3",
  "player_of_the_week": "Méndez",
  "players": [
    {"name": "Mendoza", "club": "Chonburi", "position": "GK", "rating": 7.6, "slot": "GK"},
    ...
  ]
}

Requires (relative to this script's location, i.e. the footballsiam-data repo root):
    assets/shirts/<club-slug>.png   — one per club, transparent background
    assets/branding/thai-league-1.png
    assets/branding/elephant-gold.png (house mark; football-siam.png is retired)

Requires Playwright + a Chromium install. On this project's machines, Chromium
lives at /opt/pw-browsers/chromium-*/chrome-linux/chrome (cloud sandbox) —
adjust CHROME_PATH below if running somewhere else (e.g. a normal local
Playwright install, where p.chromium.launch() with no executable_path works
fine).

Naming convention (per Simon, 2026-09-22): for Thai players, use their FIRST
name in the "name" field (Thai naming convention — e.g. "Ekanit", not
"Panya"), not the surname. For any player who has a well-known preferred/
common name that differs from what FotMob shows, use that name instead (e.g.
"Mendoza" for a goalkeeper FotMob lists as "Hansen"). This is a judgement
call made when the selection doc is written each week, not something this
script can infer — the "name" field in the JSON should already be correct by
the time it reaches this script.
"""
import base64
import json
import os
import re
import sys
import unicodedata

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # this script lives in src/, repo root is one level up
SHIRTS_DIR = os.path.join(REPO_ROOT, "assets", "shirts")
BRANDING_DIR = os.path.join(REPO_ROOT, "assets", "branding")

CHROME_PATH = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"  # cloud sandbox path; None to use Playwright's default

CANVAS_W, CANVAS_H = 1080, 1350

ROWS = [
    ["GK"],
    ["LB", "CB1", "CB2", "RB"],
    ["CM1", "CM2", "CM3"],
    ["LW", "ST", "RW"],
]


def slugify(club_name: str) -> str:
    """Same slugify rule documented in the weekly scheduled task: lowercase,
    spaces to hyphens. Kept as a single source of truth here."""
    s = unicodedata.normalize("NFKD", club_name).encode("ascii", "ignore").decode()
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def data_uri(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def load_selection(path: str) -> dict:
    with open(path) as f:
        raw = f.read()
    # tolerate being handed either a bare JSON file or the full markdown doc
    # containing a ```json ... ``` fenced block
    if raw.strip().startswith("{"):
        sel = json.loads(raw)
    else:
        m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if not m:
            raise ValueError("No fenced ```json``` block found in selection doc")
        sel = json.loads(m.group(1))
    return sel


def check_assets(selection: dict) -> list:
    """Returns a list of missing club slugs, if any. Caller should treat a
    non-empty list as a hard stop — never substitute a placeholder shirt."""
    missing = []
    for p in selection["players"]:
        slug = slugify(p["club"])
        p["_slug"] = slug  # stash for build()
        if not os.path.isfile(os.path.join(SHIRTS_DIR, f"{slug}.png")):
            missing.append((p["club"], slug))
    return missing


def build_html(selection: dict) -> str:
    players = {p["slot"]: p for p in selection["players"]}
    potw = selection["player_of_the_week"]

    # House mark: the gold Erawan elephant (Simon, 13 Oct 2026). football-siam.png is retired.
    fs_logo = data_uri(os.path.join(BRANDING_DIR, "elephant-gold.png"))
    tl_logo = data_uri(os.path.join(BRANDING_DIR, "thai-league-1.png"))

    def render_card(slot):
        p = players[slot]
        img = data_uri(os.path.join(SHIRTS_DIR, f"{p['_slug']}.png"))
        is_potw = (p["name"] == potw)
        cls = "card potw" if is_potw else "card"
        ribbon = '<div class="ribbon">★ PLAYER OF THE WEEK</div>' if is_potw else ""
        return f'''
    <div class="{cls}">
      {ribbon}
      <img class="shirt" src="{img}" alt="{p['club']}">
      <div class="name">{p['name']}</div>
      <div class="club">{p['club']}</div>
    </div>'''

    rows_html = ""
    for row in ROWS:
        cards = "".join(render_card(slot) for slot in row)
        rows_html += f'<div class="row row-{len(row)}">{cards}</div>\n'

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Lato:wght@400;700;900&display=swap');

* {{ margin:0; padding:0; box-sizing:border-box; }}

body {{
  width:{CANVAS_W}px; height:{CANVAS_H}px;
  font-family:'Lato', sans-serif;
  background:#004d25;
  position:relative;
  overflow:hidden;
}}

.pitch {{
  position:absolute; inset:0;
  background:
    repeating-linear-gradient(
      180deg,
      #0a6b3a 0px, #0a6b3a 90px,
      #0d7a42 90px, #0d7a42 180px
    );
}}

.pitch::before {{
  content:"";
  position:absolute;
  left:48px; right:48px; top:230px; bottom:40px;
  border:3px solid rgba(255,255,255,0.55);
  border-radius:6px;
}}

.pitch::after {{
  content:"";
  position:absolute;
  left:48px; right:48px; top:50%;
  border-top:3px solid rgba(255,255,255,0.55);
}}

.center-circle {{
  position:absolute;
  left:50%; top:50%;
  width:220px; height:220px;
  margin-left:-110px; margin-top:-110px;
  border:3px solid rgba(255,255,255,0.55);
  border-radius:50%;
}}
.center-spot {{
  position:absolute; left:50%; top:50%;
  width:10px; height:10px; margin-left:-5px; margin-top:-5px;
  background:rgba(255,255,255,0.55); border-radius:50%;
}}

.box-top {{
  position:absolute; left:50%; top:230px;
  width:520px; height:150px; margin-left:-260px;
  border:3px solid rgba(255,255,255,0.55); border-top:none;
}}
.box-bottom {{
  position:absolute; left:50%; bottom:40px;
  width:520px; height:150px; margin-left:-260px;
  border:3px solid rgba(255,255,255,0.55); border-bottom:none;
}}

.corner-logo {{
  position:absolute;
  top:26px;
  z-index:6;
  height:108px;
  width:auto;
  filter:drop-shadow(0 3px 6px rgba(0,0,0,0.35));
}}
.corner-logo.left {{ left:80px; }}
.corner-logo.right {{ right:80px; }}

.header {{
  position:relative; z-index:5;
  text-align:center;
  padding:38px 0 10px;
}}
.brand {{
  font-family:'Lato', sans-serif;
  font-weight:900;
  letter-spacing:3px;
  color:#e3c567;
  font-size:30px;
  text-transform:uppercase;
}}
.title {{
  font-family:'Playfair Display', serif;
  font-weight:900;
  color:#ffffff;
  font-size:64px;
  line-height:1.05;
  text-shadow:0 3px 10px rgba(0,0,0,0.35);
  margin-top:4px;
}}
.subtitle {{
  font-family:'Lato', sans-serif;
  font-weight:700;
  letter-spacing:2px;
  color:#e3c567;
  font-size:30px;
  margin-top:6px;
  text-transform:uppercase;
}}

.formation {{
  position:relative; z-index:5;
  height:1030px;
  display:flex;
  flex-direction:column;
  justify-content:space-evenly;
  padding:10px 80px 20px;
}}

.row {{
  display:flex;
  justify-content:space-evenly;
  align-items:flex-start;
}}

.card {{
  width:184px;
  background:rgba(255,255,255,0.96);
  border-radius:14px;
  padding:11px 8px 10px;
  text-align:center;
  box-shadow:0 8px 18px rgba(0,0,0,0.25);
  position:relative;
  border:3px solid rgba(0,77,37,0.15);
}}

.card.potw {{
  border:8px solid #e3c567;
  box-shadow:0 0 0 4px rgba(255,222,89,0.35), 0 10px 22px rgba(0,0,0,0.35);
}}

.ribbon {{
  position:absolute;
  top:-16px; left:50%;
  transform:translateX(-50%);
  background:#e3c567;
  color:#004d25;
  font-family:'Lato', sans-serif;
  font-weight:900;
  font-size:12.5px;
  letter-spacing:0.5px;
  padding:5px 10px;
  border-radius:20px;
  white-space:nowrap;
  box-shadow:0 3px 8px rgba(0,0,0,0.3);
}}

.shirt {{
  width:104px; height:104px;
  object-fit:contain;
  margin:6px auto 4px;
  display:block;
}}

.name {{
  font-family:'Lato', sans-serif;
  font-weight:900;
  font-size:19px;
  color:#0a1f14;
  text-transform:uppercase;
  letter-spacing:0.3px;
  margin-top:2px;
}}

.club {{
  font-family:'Lato', sans-serif;
  font-weight:400;
  font-size:14px;
  color:#4a5a52;
  margin-top:2px;
}}

.footer {{
  position:absolute;
  bottom:14px; left:0; right:0;
  text-align:center;
  z-index:5;
  color:rgba(255,255,255,0.85);
  font-size:14px;
  letter-spacing:2px;
  font-weight:700;
  text-transform:uppercase;
}}
</style>
</head>
<body>
  <div class="pitch"></div>
  <div class="center-circle"></div>
  <div class="center-spot"></div>
  <div class="box-top"></div>
  <div class="box-bottom"></div>

  <img class="corner-logo left" src="{tl_logo}" alt="Thai League 1">
  <img class="corner-logo right" src="{fs_logo}" alt="Football Siam">

  <div class="header">
    <div class="brand">Football Siam</div>
    <div class="title">Team of the Week</div>
    <div class="subtitle">Matchweek {selection['matchweek']} &middot; Thai League 1</div>
  </div>

  <div class="formation">
    {rows_html}
  </div>

  <div class="footer">footballsiam.com</div>
</body>
</html>
"""


def render(html: str, out_path: str):
    from playwright.sync_api import sync_playwright

    html_path = out_path + ".tmp.html"
    with open(html_path, "w") as f:
        f.write(html)

    with sync_playwright() as p:
        launch_kwargs = {"args": ["--no-sandbox"]}
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            launch_kwargs["executable_path"] = CHROME_PATH
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": CANVAS_W, "height": CANVAS_H})
        page.goto(f"file://{os.path.abspath(html_path)}")
        page.wait_for_timeout(600)
        page.screenshot(path=out_path)
        browser.close()

    os.remove(html_path)


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 totw_graphic.py <selection.json or totw-current-selection.md> <output.png>", file=sys.stderr)
        sys.exit(2)

    selection_path, out_path = sys.argv[1], sys.argv[2]
    selection = load_selection(selection_path)

    missing = check_assets(selection)
    if missing:
        print("MISSING SHIRT ICONS — stopping, not building with a placeholder:", file=sys.stderr)
        for club, slug in missing:
            print(f"  - {club}  (expected assets/shirts/{slug}.png)", file=sys.stderr)
        sys.exit(1)

    html = build_html(selection)
    render(html, out_path)

    size = os.path.getsize(out_path)
    print(f"OK: wrote {out_path} ({size} bytes)")


if __name__ == "__main__":
    main()
