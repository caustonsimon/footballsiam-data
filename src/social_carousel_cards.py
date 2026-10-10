#!/usr/bin/env python3
"""
FOOTBALL SIAM - SOCIAL CAROUSEL CARDS (v2, 2026-09-17)

Renders the Thu-Sun social carousel's two images directly in Python:
  1. A "Fixtures/Results" card (kickoff times on Thursday, scores from
     Friday onward, matches grouped under a date header).
  2. A league table card (current standings).

WHY THIS EXISTS INSTEAD OF SCREENSHOTTING THE SITE'S OWN CARDS
----------------------------------------------------------------
footballsiam.com already has WordPress shortcodes that render these
exact cards as HTML ([fs_social_fixtures], [fs_social_table], with club
logos) -- the natural plan was to screenshot those pages for the social
post. That doesn't work: footballsiam.com sits behind SiteGround's
anti-bot challenge (sgcaptcha), which blocks plain HTTP AND a real
headless-browser (Playwright/Chromium) request alike, from any
cloud/datacenter IP -- confirmed by direct test 2026-09-16 (both got
served the "Robot Challenge Screen" instead of real content). Only
WebFetch (a different fetch path) or device_bash (the user's own
network) can reach the site reliably, and neither can take a
screenshot.

So this script takes the same approach that already works for the
Attendance/Nationality Buffer automations: fetch plain JSON (via
WebFetch, from the Football Siam Social Data APIs -- Snippet 134,
fs/v1/social-table-data / fs/v1/social-fixtures-data / fs/v1/current-
matchweek) and render the finished card directly here, matching the
site's own visual language (dark green #0A3B25 header band, gold
#e3c567 accent text, footballsiam.com footer strip) as closely as
practical. ONE DELIBERATE DIFFERENCE: no club badge logos -- fetching
those images at runtime hits the exact same anti-bot wall as fetching
HTML (confirmed), so team names render as text only here. This matches
how the existing Attendance chart's season-to-date table already shows
club names as plain text with no logos.

FONTS (added v2, 2026-09-17)
-----------------------------
footballsiam.com's real stylesheet (confirmed by curling the live
homepage HTML and grepping font-family declarations, 2026-09-17) uses
Playfair Display (serif) for display headings and Lato (sans-serif)
for everything else -- NOT matplotlib's default DejaVu fonts, and not
the Liberation Sans used by the sibling attendance/nationality charts
(that was a generic substitute, never actually matched). Both font
families are bundled as TTFs in assets/fonts/ alongside this script so
rendering doesn't depend on whatever happens to be installed on the
machine running it:
  - PlayfairDisplay-Bold.ttf / -Regular.ttf: statically instantiated
    (via fonttools varLib.instancer) from Google Fonts' variable
    PlayfairDisplay[wght].ttf at wght=700/400, since matplotlib doesn't
    reliably pick weights out of a variable font.
  - Lato-Bold.ttf / -Regular.ttf: copied from the Lato family already
    present on the rendering machine (/usr/share/fonts/truetype/lato/).
If assets/fonts/ is ever missing these files, re-fetch Playfair Display
from https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/
and instantiate as above; grab Lato from the same google/fonts repo
(ofl/lato/) if it's not already on the machine.

BADGE SIZING (fixed v2, 2026-09-17)
-------------------------------------
The v1 badge (76x30px) was measured too tight for its text -- kickoff
times like "19:00"/"18:30" and scores like "10 - 0" run ~67px wide at
this font/size, leaving only ~4px padding each side (confirmed by
Simon: looked cramped against the box edges in the posted cards). The
badge is now sized from an actual text-extent measurement rather than
guessed, with comfortable padding baked in -- see BADGE_W/BADGE_H
below. If future kickoff-time or score formats turn out wider than
"XX - XX", bump BADGE_W rather than shrinking the font.

USAGE
-----
This script does NOT fetch anything itself -- the calling Claude
session fetches the three JSON endpoints via WebFetch and saves them
to local files first (same pattern as nationality_chart_inset.py's
--data-file), because a raw HTTP call to footballsiam.com from this
machine (or from device_bash) hits the anti-bot wall / org egress
block respectively.

    python src/social_carousel_cards.py fixtures \\
        --data-file outputs/fixtures_data.json \\
        --out outputs/fixtures_card.png

    python src/social_carousel_cards.py table \\
        --data-file outputs/table_data.json \\
        --out outputs/table_card.png

`fixtures_data.json` is the raw response of fs/v1/social-fixtures-data
(has "mw", "mode", "matches": [...]).  `table_data.json` is the raw
response of fs/v1/social-table-data (has "standings": [...]).

Both cards are 1080px wide, opaque white background, saved via
savefig(..., transparent=False) -- these are finished social images,
not compositing insets.
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm

# House colours -- matched to the WP shortcode cards (Snippet 130/132),
# which Simon has already seen and approved, rather than the slightly
# different green already used inside attendance_chart_inset.py /
# nationality_chart_inset.py (#004d25). Both greens are "Football Siam
# green" in practice; this script standardises on the shortcode's exact
# hex so the carousel matches what Simon signed off on.
DARK_GREEN = "#0A3B25"
GOLD = "#e3c567"
WHITE = "#ffffff"
DARK_TEXT = "#1a1a1a"
ROW_ALT = "#f7f8f6"
FOOTER_BG = "#f5f5f3"

CARD_WIDTH_PX = 1080
DPI = 150

LEAGUE_LABELS = {
    "thai-league-1": "Thai League 1",
    "thai-league-2": "Thai League 2",
    "asean-club-championship": "ASEAN Club Championship",
    "afc-champions-league-elite": "AFC Champions League Elite",
    "afc-champions-league-two": "AFC Champions League Two",
}
GREY = "#888888"

# -- Fonts: matched to footballsiam.com's real stylesheet (see module
# docstring). Bundled TTFs beside this script, loaded by explicit path
# so this doesn't depend on what's installed system-wide.
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_HEADLINE = fm.FontProperties(fname=str(_FONT_DIR / "PlayfairDisplay-Bold.ttf"))
FONT_BOLD = fm.FontProperties(fname=str(_FONT_DIR / "Lato-Bold.ttf"))
FONT_REGULAR = fm.FontProperties(fname=str(_FONT_DIR / "Lato-Regular.ttf"))

# -- Kickoff-time / score badge sizing: measured (not guessed) from the
# actual rendered width of "19:00"-style and "10 - 0"-style strings at
# BADGE_FONTSIZE in FONT_BOLD (~67px wide, ~24px tall at 150 DPI) with
# generous padding added. See BADGE SIZING note in the module docstring.
BADGE_FONTSIZE = 12.5
BADGE_W = 92
BADGE_H = 36


def _fig_for_height(height_px):
    width_in = CARD_WIDTH_PX / DPI
    height_in = height_px / DPI
    fig = plt.figure(figsize=(width_in, height_in), dpi=DPI)
    fig.patch.set_facecolor(WHITE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, CARD_WIDTH_PX)
    ax.set_ylim(0, height_px)
    ax.invert_yaxis()  # y=0 at the top, growing downward, like pixel coords
    ax.axis("off")
    return fig, ax


def _header(ax, height_px, league_name, badge_label, sub_label=None, header_h=100):
    ax.add_patch(patches.Rectangle((0, 0), CARD_WIDTH_PX, header_h, facecolor=DARK_GREEN, edgecolor="none"))
    ax.text(36, header_h / 2 - (8 if sub_label else 0), league_name,
             fontsize=24, color=WHITE, ha="left", va="center",
             fontproperties=FONT_HEADLINE)
    ax.text(CARD_WIDTH_PX - 36, header_h / 2 - (10 if sub_label else 0), badge_label,
             fontsize=11, color=GOLD, ha="right", va="center", fontproperties=FONT_BOLD)
    if sub_label:
        ax.text(CARD_WIDTH_PX - 36, header_h / 2 + 16, sub_label,
                 fontsize=12, color="#c9d9c9", ha="right", va="center", fontproperties=FONT_BOLD)
    return header_h


def _footer(ax, height_px, footer_h=50):
    y0 = height_px - footer_h
    ax.add_patch(patches.Rectangle((0, y0), CARD_WIDTH_PX, footer_h, facecolor=FOOTER_BG, edgecolor="none"))
    ax.text(CARD_WIDTH_PX - 36, y0 + footer_h / 2, "footballsiam.com",
             fontsize=12, color=DARK_GREEN, ha="right", va="center", fontproperties=FONT_BOLD)


def render_fixtures_card(data, out_path):
    league = data.get("league", "thai-league-1")
    league_name = data.get("label") or LEAGUE_LABELS.get(league, league)
    mode = data.get("mode", "fixtures")
    mw = data.get("mw", "")
    matches = data.get("matches", [])

    badge_label = "RESULTS" if mode == "results" else "FIXTURES"
    # "sub" (added 2026-10-10, Matchday Watcher card data) overrides the
    # Thai League "Match Week N" label, e.g. "Matchday 2" for continental games.
    if "sub" in data:
        sub_label = data.get("sub") or None
    else:
        sub_label = f"Match Week {mw}" if mw else None
    show_groups = len({m.get("group") for m in matches if m.get("group")}) > 1

    header_h = 100
    footer_h = 50
    date_header_h = 34
    row_h = 46
    top_pad = 8
    bottom_pad = 8

    if not matches:
        body_h = 80
    else:
        body_h = top_pad + bottom_pad
        last_date = None
        for m in matches:
            if m.get("date") != last_date:
                body_h += date_header_h
                last_date = m.get("date")
            body_h += row_h

    total_h = header_h + body_h + footer_h
    fig, ax = _fig_for_height(total_h)
    _header(ax, total_h, league_name, badge_label, sub_label, header_h=header_h)
    _footer(ax, total_h, footer_h=footer_h)

    y = header_h + top_pad
    if not matches:
        ax.text(CARD_WIDTH_PX / 2, header_h + body_h / 2, f"No {badge_label.lower()} to show.",
                 fontsize=13, color="#888888", ha="center", va="center", fontproperties=FONT_REGULAR)
    else:
        last_date = None
        row_i = 0
        for m in matches:
            if m.get("date") != last_date:
                last_date = m.get("date")
                ax.text(32, y + date_header_h / 2, last_date,
                         fontsize=12, color=DARK_GREEN, ha="left", va="center", fontproperties=FONT_BOLD)
                y += date_header_h

            if row_i % 2 == 0:
                ax.add_patch(patches.Rectangle((0, y), CARD_WIDTH_PX, row_h, facecolor=WHITE, edgecolor="none"))
            else:
                ax.add_patch(patches.Rectangle((0, y), CARD_WIDTH_PX, row_h, facecolor=ROW_ALT, edgecolor="none"))

            mid = y + row_h / 2
            is_scored = m.get("is_scored")
            if is_scored:
                center_text = f"{m.get('home_score')} - {m.get('away_score')}"
            elif m.get("status") == "postponed":
                center_text = "P - P"
            else:
                center_text = m.get("time", "TBC")
            if show_groups and m.get("group"):
                # Small group tag at the far left, only when the card mixes groups.
                ax.text(32, mid, str(m["group"]).replace("Group ", ""), fontsize=10.5,
                         color=GREY, ha="left", va="center", fontproperties=FONT_BOLD)

            # Team-name anchors sit just outside the (now wider) centre
            # badge -- BADGE_W/2 + a fixed clearance, not a hand-picked
            # constant, so the gap stays correct if BADGE_W changes.
            name_offset = BADGE_W / 2 + 20
            ax.text(CARD_WIDTH_PX / 2 - name_offset, mid, m.get("home", ""), fontsize=13.5,
                     color=DARK_TEXT, ha="right", va="center", fontproperties=FONT_BOLD)
            ax.text(CARD_WIDTH_PX / 2 + name_offset, mid, m.get("away", ""), fontsize=13.5,
                     color=DARK_TEXT, ha="left", va="center", fontproperties=FONT_BOLD)

            ax.add_patch(patches.FancyBboxPatch(
                (CARD_WIDTH_PX / 2 - BADGE_W / 2, mid - BADGE_H / 2), BADGE_W, BADGE_H,
                boxstyle="round,pad=0,rounding_size=6", facecolor=DARK_GREEN, edgecolor="none"))
            ax.text(CARD_WIDTH_PX / 2, mid, center_text, fontsize=BADGE_FONTSIZE,
                     color=WHITE, ha="center", va="center", fontproperties=FONT_BOLD)

            y += row_h
            row_i += 1

    fig.savefig(out_path, dpi=DPI, facecolor=WHITE, transparent=False)
    plt.close(fig)


def render_table_card(data, out_path):
    league = data.get("league", "thai-league-1")
    league_name = data.get("label") or LEAGUE_LABELS.get(league, league)
    season = data.get("season", "")
    standings = data.get("standings", [])
    # Added 2026-10-10 for group tables: "title" (e.g. "Group A", "League stage: East")
    # goes in the header badge, "qualify" draws a dashed line under that position,
    # "note" is a one-line explanation under the table.
    title = data.get("title")
    if title and title.lower() == "table":
        title = None
    qualify = int(data.get("qualify") or 0)
    note = data.get("note") or ""

    header_h = 100
    footer_h = 50
    col_header_h = 34
    row_h = 40
    top_pad = 6
    bottom_pad = 6
    note_h = 40 if note else 0

    body_h = top_pad + bottom_pad + col_header_h + row_h * max(len(standings), 1) + note_h
    total_h = header_h + body_h + footer_h

    fig, ax = _fig_for_height(total_h)
    if title:
        _header(ax, total_h, league_name, title.upper(),
                sub_label=f"{season} Season" if season else None, header_h=header_h)
    else:
        _header(ax, total_h, league_name, f"{season} Season".upper() if season else "", header_h=header_h)
    _footer(ax, total_h, footer_h=footer_h)

    # column x-positions (pixels from left)
    col_pos = 40      # Pos
    col_club = 80     # Club (left aligned)
    col_stats_start = 640
    col_labels = ["P", "W", "D", "L", "F", "A", "GD", "Pts"]
    n_cols = len(col_labels)
    col_w = (CARD_WIDTH_PX - 36 - col_stats_start) / n_cols

    y = header_h + top_pad
    # column header row
    ax.text(col_pos, y + col_header_h / 2, "#", fontsize=11, color="#888888", ha="center", va="center", fontproperties=FONT_BOLD)
    ax.text(col_club, y + col_header_h / 2, "Club", fontsize=11, color="#888888", ha="left", va="center", fontproperties=FONT_BOLD)
    for i, lbl in enumerate(col_labels):
        cx = col_stats_start + col_w * i + col_w / 2
        ax.text(cx, y + col_header_h / 2, lbl, fontsize=11, color="#888888", ha="center", va="center", fontproperties=FONT_BOLD)
    y += col_header_h

    if not standings:
        ax.text(CARD_WIDTH_PX / 2, y + row_h / 2, "No matches scheduled for this division.",
                 fontsize=13, color="#888888", ha="center", va="center", fontproperties=FONT_REGULAR)
    else:
        for i, t in enumerate(standings):
            row_bg = WHITE if i % 2 == 0 else ROW_ALT
            ax.add_patch(patches.Rectangle((0, y), CARD_WIDTH_PX, row_h, facecolor=row_bg, edgecolor="none"))
            mid = y + row_h / 2

            ax.text(col_pos, mid, str(t.get("pos", i + 1)), fontsize=12.5,
                     color=DARK_GREEN, ha="center", va="center", fontproperties=FONT_BOLD)
            ax.text(col_club, mid, t.get("name", ""), fontsize=13,
                     color=DARK_TEXT, ha="left", va="center", fontproperties=FONT_BOLD)

            values = [t.get("p", 0), t.get("w", 0), t.get("d", 0), t.get("l", 0),
                      t.get("gf", 0), t.get("ga", 0), t.get("gd", 0), t.get("pts", 0)]
            for j, val in enumerate(values):
                cx = col_stats_start + col_w * j + col_w / 2
                font = FONT_BOLD if col_labels[j] == "Pts" else FONT_REGULAR
                ax.text(cx, mid, str(val), fontsize=12.5,
                         color=DARK_TEXT, ha="center", va="center", fontproperties=font)

            y += row_h
            if qualify and i + 1 == qualify and i + 1 < len(standings):
                ax.plot([24, CARD_WIDTH_PX - 24], [y, y], color=DARK_GREEN, linewidth=1.6,
                        linestyle=(0, (5, 4)), solid_capstyle="butt")

    if note:
        ax.text(36, y + bottom_pad + note_h / 2, note, fontsize=11, color=GREY,
                 ha="left", va="center", fontproperties=FONT_REGULAR)

    fig.savefig(out_path, dpi=DPI, facecolor=WHITE, transparent=False)
    plt.close(fig)


def render_matchday_cards(data, out_dir, prefix):
    """One results card plus one table card per table, from the raw response of
    fs/v1/matchday-card-data (Snippet 192, added 2026-10-10). Returns the paths in
    carousel order: results first, then the tables."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    res = dict(data["results"])
    res.setdefault("label", data.get("label"))
    p = out_dir / f"{prefix}_results.png"
    render_fixtures_card(res, str(p))
    paths.append(str(p))
    for i, t in enumerate(data.get("tables") or [], 1):
        p = out_dir / f"{prefix}_table_{i}.png"
        render_table_card(t, str(p))
        paths.append(str(p))
    return paths


def main():
    parser = argparse.ArgumentParser(description="Render Football Siam social carousel cards (fixtures/results or table).")
    parser.add_argument("kind", choices=["fixtures", "table", "matchday"],
                        help="Which card to render. 'matchday' renders the results card plus every table from fs/v1/matchday-card-data.")
    parser.add_argument("--out-dir", help="matchday only: folder for the PNGs.")
    parser.add_argument("--prefix", default="card", help="matchday only: file name prefix.")
    parser.add_argument("--data-file", required=True, help="Path to the raw JSON response from the corresponding fs/v1/social-*-data endpoint.")
    parser.add_argument("--out", help="Output PNG path (fixtures/table).")
    args = parser.parse_args()

    data_path = Path(args.data_file)
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if args.kind == "matchday":
        if not args.out_dir:
            print("ERROR: matchday needs --out-dir", file=sys.stderr)
            sys.exit(1)
        for path in render_matchday_cards(data, args.out_dir, args.prefix):
            print(f"Wrote {path}")
        return

    if not args.out:
        print("ERROR: --out is required", file=sys.stderr)
        sys.exit(1)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.kind == "fixtures":
        render_fixtures_card(data, str(out_path))
    else:
        render_table_card(data, str(out_path))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
