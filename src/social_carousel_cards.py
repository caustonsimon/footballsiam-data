#!/usr/bin/env python3
"""
FOOTBALL SIAM - SOCIAL CAROUSEL CARDS (v1, 2026-09-17)

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
WebFetch (a different fetch path) or the device-linked WordPress MCP
connector can reach the site reliably, and neither can take a
screenshot.

So this script takes the same approach that already works for the
Attendance/Nationality Buffer automations: fetch plain JSON (via
WebFetch, from the Football Siam Social Data APIs -- Snippet 134,
fs/v1/social-table-data / fs/v1/social-fixtures-data / fs/v1/current-
matchweek) and render the finished card directly here, matching the
WP shortcodes' visual language (dark green #0A3B25 header band, gold
#e3c567 accent text, footballsiam.com footer strip) as closely as
practical. ONE DELIBERATE DIFFERENCE: no club badge logos -- fetching
those images at runtime hits the exact same anti-bot wall as fetching
HTML (confirmed), so team names render as text only here. This matches
how the existing Attendance chart's season-to-date table already shows
club names as plain text with no logos.

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
}


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
             fontsize=22, fontweight="bold", color=WHITE, ha="left", va="center",
             fontstyle="normal", family="serif")
    ax.text(CARD_WIDTH_PX - 36, header_h / 2 - (10 if sub_label else 0), badge_label,
             fontsize=11, fontweight="bold", color=GOLD, ha="right", va="center")
    if sub_label:
        ax.text(CARD_WIDTH_PX - 36, header_h / 2 + 16, sub_label,
                 fontsize=12, fontweight="bold", color="#c9d9c9", ha="right", va="center")
    return header_h


def _footer(ax, height_px, footer_h=50):
    y0 = height_px - footer_h
    ax.add_patch(patches.Rectangle((0, y0), CARD_WIDTH_PX, footer_h, facecolor=FOOTER_BG, edgecolor="none"))
    ax.text(CARD_WIDTH_PX - 36, y0 + footer_h / 2, "footballsiam.com",
             fontsize=12, fontweight="bold", color=DARK_GREEN, ha="right", va="center")


def render_fixtures_card(data, out_path):
    league = data.get("league", "thai-league-1")
    league_name = LEAGUE_LABELS.get(league, league)
    mode = data.get("mode", "fixtures")
    mw = data.get("mw", "")
    matches = data.get("matches", [])

    badge_label = "RESULTS" if mode == "results" else "FIXTURES"
    sub_label = f"Match Week {mw}" if mw else None

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
                 fontsize=13, color="#888888", ha="center", va="center")
    else:
        last_date = None
        row_i = 0
        for m in matches:
            if m.get("date") != last_date:
                last_date = m.get("date")
                ax.text(32, y + date_header_h / 2, last_date,
                         fontsize=12, fontweight="bold", color=DARK_GREEN, ha="left", va="center")
                y += date_header_h

            if row_i % 2 == 0:
                ax.add_patch(patches.Rectangle((0, y), CARD_WIDTH_PX, row_h, facecolor=WHITE, edgecolor="none"))
            else:
                ax.add_patch(patches.Rectangle((0, y), CARD_WIDTH_PX, row_h, facecolor=ROW_ALT, edgecolor="none"))

            mid = y + row_h / 2
            is_scored = m.get("is_scored")
            if is_scored:
                center_text = f"{m.get('home_score')} - {m.get('away_score')}"
            else:
                center_text = m.get("time", "TBC")

            ax.text(CARD_WIDTH_PX / 2 - 55, mid, m.get("home", ""), fontsize=13.5, fontweight="bold",
                     color=DARK_TEXT, ha="right", va="center")
            ax.text(CARD_WIDTH_PX / 2 + 55, mid, m.get("away", ""), fontsize=13.5, fontweight="bold",
                     color=DARK_TEXT, ha="left", va="center")

            badge_w, badge_h = 76, 30
            ax.add_patch(patches.FancyBboxPatch(
                (CARD_WIDTH_PX / 2 - badge_w / 2, mid - badge_h / 2), badge_w, badge_h,
                boxstyle="round,pad=0,rounding_size=4", facecolor=DARK_GREEN, edgecolor="none"))
            ax.text(CARD_WIDTH_PX / 2, mid, center_text, fontsize=12.5, fontweight="bold",
                     color=WHITE, ha="center", va="center")

            y += row_h
            row_i += 1

    fig.savefig(out_path, dpi=DPI, facecolor=WHITE, transparent=False)
    plt.close(fig)


def render_table_card(data, out_path):
    league = data.get("league", "thai-league-1")
    league_name = LEAGUE_LABELS.get(league, league)
    season = data.get("season", "")
    standings = data.get("standings", [])

    header_h = 100
    footer_h = 50
    col_header_h = 34
    row_h = 40
    top_pad = 6
    bottom_pad = 6

    body_h = top_pad + bottom_pad + col_header_h + row_h * max(len(standings), 1)
    total_h = header_h + body_h + footer_h

    fig, ax = _fig_for_height(total_h)
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
    ax.text(col_pos, y + col_header_h / 2, "#", fontsize=11, fontweight="bold", color="#888888", ha="center", va="center")
    ax.text(col_club, y + col_header_h / 2, "Club", fontsize=11, fontweight="bold", color="#888888", ha="left", va="center")
    for i, lbl in enumerate(col_labels):
        cx = col_stats_start + col_w * i + col_w / 2
        ax.text(cx, y + col_header_h / 2, lbl, fontsize=11, fontweight="bold", color="#888888", ha="center", va="center")
    y += col_header_h

    if not standings:
        ax.text(CARD_WIDTH_PX / 2, y + row_h / 2, "No matches scheduled for this division.",
                 fontsize=13, color="#888888", ha="center", va="center")
    else:
        for i, t in enumerate(standings):
            row_bg = WHITE if i % 2 == 0 else ROW_ALT
            ax.add_patch(patches.Rectangle((0, y), CARD_WIDTH_PX, row_h, facecolor=row_bg, edgecolor="none"))
            mid = y + row_h / 2

            ax.text(col_pos, mid, str(t.get("pos", i + 1)), fontsize=12.5, fontweight="bold",
                     color=DARK_GREEN, ha="center", va="center")
            ax.text(col_club, mid, t.get("name", ""), fontsize=13, fontweight="bold",
                     color=DARK_TEXT, ha="left", va="center")

            values = [t.get("p", 0), t.get("w", 0), t.get("d", 0), t.get("l", 0),
                      t.get("gf", 0), t.get("ga", 0), t.get("gd", 0), t.get("pts", 0)]
            for j, val in enumerate(values):
                cx = col_stats_start + col_w * j + col_w / 2
                weight = "bold" if col_labels[j] == "Pts" else "normal"
                ax.text(cx, mid, str(val), fontsize=12.5, fontweight=weight,
                         color=DARK_TEXT, ha="center", va="center")

            y += row_h

    fig.savefig(out_path, dpi=DPI, facecolor=WHITE, transparent=False)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Render Football Siam social carousel cards (fixtures/results or table).")
    parser.add_argument("kind", choices=["fixtures", "table"], help="Which card to render.")
    parser.add_argument("--data-file", required=True, help="Path to the raw JSON response from the corresponding fs/v1/social-*-data endpoint.")
    parser.add_argument("--out", required=True, help="Output PNG path.")
    args = parser.parse_args()

    data_path = Path(args.data_file)
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.kind == "fixtures":
        render_fixtures_card(data, str(out_path))
    else:
        render_table_card(data, str(out_path))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
