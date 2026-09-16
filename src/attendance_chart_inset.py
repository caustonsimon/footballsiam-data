#!/usr/bin/env python3
"""Weekly attendance line-chart + season-to-date table, styled for the Canva
"Attendance" post inset (white rounded card, no title/header — the title
lives on the Canva page itself: "Attendances" / "thai League 1" / "Match
week NN  2026/27"). Same overall pattern as src/nationality_chart_inset.py
(transparent figure, house colours, matplotlib, output to outputs/), but
unlike that script this one's card is SOLID WHITE, not green — see the
nationality script's docstring for why (locked in on Matchweek 1 feedback).

    python src/attendance_chart_inset.py thai-league-1 2
    python src/attendance_chart_inset.py thai-league-1 2 --season 2026/2027

Data source
-----------
Attendance is NOT synced to WordPress (checked 2026-09-16: `wp/v2/match`
meta has a `stadium` field but no `attendance` field, and the base64
`raw_match_data` blob only carries `player_stats`/`match_events` — no
attendance either). The only source for it is the FootyStats API directly
(`league-matches`, field `attendance`, -1 when not yet reported), the same
API sync_matches.py pulls from for the initial WP import.

IMPORTANT — this is the opposite situation from nationality_chart_inset.py's
--data-file requirement: footballsiam.com itself is unreachable from a
Claude-driven device_bash shell (SiteGround's anti-bot layer), but
api.football-data-api.com is NOT behind that block — a plain
FootyStatsClient() call works fine from device_bash (confirmed 2026-09-16:
curl to api.football-data-api.com and google.com both returned real HTTP
200s from that shell, only footballsiam.com itself is challenged). So this
script talks to FootyStats directly over the network by default, no
--data-file dance needed. --data-file is still offered below as an escape
hatch (a JSON list of raw FootyStats match objects, i.e. the `data` array
of a `league-matches` response for the relevant season_id) in case that
ever changes or you want to test against a saved snapshot.

Attendance data lag: FootyStats does not always have every match's
attendance in within a day or two of kickoff — on 2026-09-16, Matchweek 2
(played 2026-09-11 to 09-13) still had only 4 of 8 matches reporting
attendance (the other 4 showed -1), even though Matchweek 1 (played five
days earlier, one week prior) had gone on to fill in cleanly by the time it
was charted. This script does NOT wait or retry for that — it charts
whatever has a non-negative attendance value *right now* and prints a loud
warning listing which fixtures are still missing, so the calling routine
can decide whether to post a partial chart or hold off. Never silently
publish a chart with fewer than the full slate of fixtures without knowing
why — check the printed warning first.

Team abbreviations (TEAM_ABBR below) are a hand-maintained lookup, not
derived from any WordPress/FootyStats field (neither exposes one). Built
2026-09-16 from the 16 Thai League 1 2026/27 club names FootyStats reports
and cross-checked against the abbreviations visible in the Matchweek 1
Canva design (DAHUrrqKYMY) — e.g. "Bangkok Glass" shows as BGPU because
Football Siam refers to the club as "BG Pathum United" elsewhere (see the
Matchweek 2 Team of the Week Buffer post). If a new club is promoted into
the league, add it to TEAM_ABBR — unmapped names fall back to an
auto-generated code (first letters of each word, uppercased, max 5 chars)
so the script never crashes, but check the output for anything that looks
wrong before shipping.

Canva placeholder box (read from DAHUrrqKYMY 2026-09-16): the image rect
element is 980.51189337526 x 862.4674084300458px (locator id
LBckcBGqS43Hhs5C on the only page) — same numbers as the nationality
placeholder's docstring cites, i.e. both chart types share one page
template. render() below targets that exact aspect ratio (9.8 x 8.62in @
dpi150 -> 1470x1293px, ratio 1.1370, vs. the placeholder's 1.1369) so the
crop needed after inserting into Canva is minimal — but Canva's automatic
cover-fit can still nibble an edge, so after insert_fill/update_fill you
MUST still follow up with a crop_media operation on that element setting
top=0, left=0, width=980.51189337526, height=862.4674084300458 (re-read
the design first in case a future placeholder box size differs slightly).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
from sync_matches import _load_dotenv  # noqa: E402
_load_dotenv(_ROOT / ".env")
from footystats_client import FootyStatsClient, FootyStatsError  # noqa: E402

# competition slug -> (FootyStats season_id, display name) — season_ids
# copied from sync_matches.py's LEAGUES table (Thai League 1 / 2 rows).
COMPETITIONS = {
    "thai-league-1": (17367, "Thai League 1"),
    "thai-league-2": (17896, "Thai League 2"),
}

GREEN = "#004d25"
GOLD = "#ffde59"
WHITE = "#ffffff"
DARK_TEXT = "#12261b"
ROW_ALT = "#f2f6f3"     # faint green-grey banding for alternating table rows
GRID_GREY = "#d9dede"

# hand-maintained team-name -> short code lookup (see module docstring).
TEAM_ABBR = {
    "Ayutthaya United": "AYUTD",
    "Bangkok Glass": "BGPU",       # a.k.a. BG Pathum United
    "Bangkok United": "BUFC",
    "Buriram United": "BRUTD",
    "Chiangrai United": "CRUTD",
    "Chonburi": "CHON",
    "Lamphun Warrior": "LNWR",
    "Lamphun Warriors": "LNWR",
    "Pattani": "PTFC",
    "Port FC": "PORT",
    "Prachuap": "PRFC",
    "Rasi Salai United": "RSU",
    "Rasisalai United": "RSU",
    "Ratchaburi": "RFC",
    "Rayong": "RYFC",
    "Sisaket United": "SISU",
    "Sukhothai": "SKTFC",
    "Uthai Thani": "UTFC",
}


def abbr(name: str) -> str:
    if name in TEAM_ABBR:
        return TEAM_ABBR[name]
    code = "".join(w[0] for w in name.split() if w)[:5].upper()
    print(f"  ! no TEAM_ABBR entry for {name!r} — auto-generated code {code!r}, "
          f"add it to TEAM_ABBR if this looks wrong")
    return code


for _f in [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]:
    try:
        fm.fontManager.addfont(_f)
    except Exception:
        pass
plt.rcParams["font.family"] = "Liberation Sans"


def load_matches(data_file: Path | None, season_id: int) -> list[dict]:
    """Every raw match object for a FootyStats season_id — either from a
    saved JSON file (a `league-matches` response's `data` array) or, by
    default, straight from the FootyStats API (works fine from a
    device_bash shell — see module docstring).
    """
    if data_file is not None:
        data = json.loads(data_file.read_text())
        if isinstance(data, dict):
            data = data.get("data", data)
        if not isinstance(data, list):
            raise ValueError(f"{data_file} does not contain a list of match objects")
        return data
    fs = FootyStatsClient()
    return fs.get_league_matches(season_id)


def week_fixtures(matches: list[dict], week: int) -> tuple[list[dict], list[dict], list[dict]]:
    """Split a match week's matches into (with_attendance, missing_attendance,
    not_yet_played) — each a list of raw FootyStats match dicts.
    """
    wk = [m for m in matches if m.get("game_week") == week]
    played = [m for m in wk if m.get("status") == "complete"]
    not_played = [m for m in wk if m.get("status") != "complete"]
    have = [m for m in played if isinstance(m.get("attendance"), int) and m["attendance"] >= 0]
    missing = [m for m in played if m not in have]
    have.sort(key=lambda m: m.get("date_unix", 0))
    return have, missing, not_played


def season_to_date(matches: list[dict], upto_week: int) -> list[tuple[str, int, int]]:
    """[(club, season_total, games_played)] for every club that has hosted
    at least one match (game_week <= upto_week) with a recorded attendance,
    sorted descending by season total. This is HOME attendance only, same
    as the Matchweek 1 design ("SEASON-TO-DATE — HOME ATTENDANCE").
    """
    totals: dict[str, int] = {}
    games: dict[str, int] = {}
    for m in matches:
        wk = m.get("game_week")
        if not isinstance(wk, int) or wk > upto_week:
            continue
        if m.get("status") != "complete":
            continue
        att = m.get("attendance")
        if not isinstance(att, int) or att < 0:
            continue
        home = m.get("home_name") or "Unknown"
        totals[home] = totals.get(home, 0) + att
        games[home] = games.get(home, 0) + 1
    rows = [(club, totals[club], games[club]) for club in totals]
    rows.sort(key=lambda r: -r[1])
    return rows


def _nice_step(max_val: float) -> int:
    """A round gridline step (1000/2000/2500/5000/...) giving ~4-6 gridlines
    up to max_val.
    """
    if max_val <= 0:
        return 1000
    raw = max_val / 5
    magnitude = 10 ** (len(str(int(raw))) - 1)
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * magnitude
        if step >= raw:
            return int(step)
    return int(10 * magnitude)


def render(
    fixtures: list[dict],
    table_rows: list[tuple[str, int, int]],
    out_path: Path,
) -> None:
    fig = plt.figure(figsize=(9.8, 8.62), dpi=150)
    fig.patch.set_alpha(0.0)

    CARD_X, CARD_Y, CARD_W, CARD_H = 0.012, 0.012, 0.976, 0.976
    ROUND = 0.03

    n_shadow = 7
    for i in range(n_shadow, 0, -1):
        off = i * 0.0016
        alpha = 0.05 * (1 - (i - 1) / n_shadow)
        shadow = FancyBboxPatch(
            (CARD_X + off, CARD_Y - off), CARD_W, CARD_H,
            transform=fig.transFigure, figure=fig,
            boxstyle=f"round,pad=0,rounding_size={ROUND}",
            linewidth=0, facecolor="#0a1f14", alpha=alpha, zorder=-20 - i,
        )
        fig.patches.append(shadow)

    card = FancyBboxPatch(
        (CARD_X, CARD_Y), CARD_W, CARD_H,
        transform=fig.transFigure, figure=fig,
        boxstyle=f"round,pad=0,rounding_size={ROUND}",
        linewidth=0, facecolor=WHITE, zorder=-10,
    )
    fig.patches.append(card)

    # ---- top: line chart -------------------------------------------------
    ax = fig.add_axes([0.105, 0.485, 0.85, 0.44])
    xs = list(range(len(fixtures)))
    ys = [f["attendance"] for f in fixtures]
    labels = [f"{abbr(f['home_name'])} v {abbr(f['away_name'])}" for f in fixtures]

    step = _nice_step(max(ys) if ys else 5000)
    y_max = step * (max(ys) // step + 2) if ys else step * 4

    ax.set_ylim(0, y_max)
    ax.set_xlim(-0.5, len(xs) - 0.5 if xs else 0.5)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(step))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.grid(axis="y", color=GRID_GREY, linewidth=1.1, zorder=0)
    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=15, colors="#4a5a52")
    ax.tick_params(axis="x", length=0)

    ax.plot(xs, ys, color=GREEN, linewidth=3.2, zorder=2, solid_capstyle="round")
    ax.scatter(xs, ys, s=230, facecolor=GOLD, edgecolor=WHITE, linewidth=2.6, zorder=3)

    for x, y in zip(xs, ys):
        # flip labels below the marker if they'd otherwise clip the top
        va, dy = ("bottom", y_max * 0.035) if y < y_max * 0.9 else ("top", -y_max * 0.035)
        ax.text(
            x, y + dy, f"{y:,}", ha="center", va=va, fontsize=16.5, fontweight="bold",
            color=DARK_TEXT, zorder=4,
            path_effects=[pe.withStroke(linewidth=3.5, foreground=WHITE)],
        )

    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=15, ha="right", fontsize=12.5,
                        fontweight="bold", color="#2c3b33",
                        rotation_mode="anchor")
    ax.set_ylabel("Attendance", fontsize=14, fontweight="bold", color="#4a5a52",
                   labelpad=10)

    # ---- bottom: season-to-date table -------------------------------------
    ax_t = fig.add_axes([0.0, 0.0, 1.0, 0.435])
    ax_t.set_xlim(0, 1)
    ax_t.set_ylim(0, 1)
    ax_t.axis("off")

    HEADER_H = 0.155
    SUBHEAD_H = 0.10
    top = 1.0
    ax_t.add_patch(Rectangle((0, top - HEADER_H), 1, HEADER_H, transform=ax_t.transAxes,
                              facecolor=GREEN, linewidth=0, zorder=1))
    ax_t.text(0.035, top - HEADER_H / 2, "SEASON-TO-DATE — HOME ATTENDANCE",
              transform=ax_t.transAxes, ha="left", va="center",
              fontsize=19, fontweight="bold", color=WHITE, zorder=2)

    subhead_top = top - HEADER_H
    ax_t.add_patch(Rectangle((0, subhead_top - SUBHEAD_H), 1, SUBHEAD_H, transform=ax_t.transAxes,
                              facecolor="#0f6b3d", linewidth=0, zorder=1))
    COL_CLUB, COL_TOTAL, COL_AVG = 0.035, 0.68, 0.90
    ax_t.text(COL_CLUB, subhead_top - SUBHEAD_H / 2, "Club", transform=ax_t.transAxes,
              ha="left", va="center", fontsize=14.5, fontweight="bold", color=GOLD, zorder=2)
    ax_t.text(COL_TOTAL, subhead_top - SUBHEAD_H / 2, "Season Total", transform=ax_t.transAxes,
              ha="center", va="center", fontsize=14.5, fontweight="bold", color=GOLD, zorder=2)
    ax_t.text(COL_AVG, subhead_top - SUBHEAD_H / 2, "Season Average", transform=ax_t.transAxes,
              ha="center", va="center", fontsize=14.5, fontweight="bold", color=GOLD, zorder=2)

    rows_top = subhead_top - SUBHEAD_H
    n_rows = len(table_rows)
    row_h = rows_top / max(n_rows, 1)
    for i, (club, total, games) in enumerate(table_rows):
        y0 = rows_top - (i + 1) * row_h
        if i % 2 == 1:
            ax_t.add_patch(Rectangle((0, y0), 1, row_h, transform=ax_t.transAxes,
                                      facecolor=ROW_ALT, linewidth=0, zorder=1))
        y_mid = y0 + row_h / 2
        avg = round(total / games) if games else 0
        ax_t.text(COL_CLUB, y_mid, club, transform=ax_t.transAxes, ha="left", va="center",
                  fontsize=14.5, fontweight="bold", color=GREEN, zorder=2)
        ax_t.text(COL_TOTAL, y_mid, f"{total:,}", transform=ax_t.transAxes, ha="center",
                  va="center", fontsize=14.5, color=DARK_TEXT, zorder=2)
        ax_t.text(COL_AVG, y_mid, f"{avg:,}", transform=ax_t.transAxes, ha="center",
                  va="center", fontsize=14.5, color=DARK_TEXT, zorder=2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, transparent=True)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("competition", choices=sorted(COMPETITIONS))
    ap.add_argument("match_week", type=int)
    ap.add_argument("--season", default="2026/2027",
                    help="season label, for display/logging only (default: 2026/2027)")
    ap.add_argument("--outputs", default=str(_ROOT / "outputs"),
                    help="output directory (default: ./outputs)")
    ap.add_argument("--data-file", type=Path, default=None,
                    help="path to a JSON file holding the `data` array of a saved "
                         "FootyStats league-matches response, to skip the network "
                         "call (see module docstring — normally not needed).")
    ap.add_argument("--allow-partial", action="store_true",
                    help="render even if some of this match week's fixtures are "
                         "missing attendance data (by default the script refuses "
                         "and exits 1, printing what's missing, so a partial chart "
                         "is never shipped by accident).")
    args = ap.parse_args(argv)

    season_id, comp_name = COMPETITIONS[args.competition]
    print(f"{comp_name} — {args.season} — Match Week {args.match_week}")

    try:
        matches = load_matches(args.data_file, season_id)
    except FootyStatsError as exc:
        print(f"  ! FootyStats API error: {exc}")
        return 1
    print(f"  matches loaded for season: {len(matches)}")

    have, missing, not_played = week_fixtures(matches, args.match_week)

    if not_played:
        print(f"  ! {len(not_played)} fixture(s) in week {args.match_week} not yet "
              f"played/complete:")
        for m in not_played:
            print(f"      {m.get('home_name')} v {m.get('away_name')} "
                  f"(status={m.get('status')})")

    if missing:
        print(f" ! {len(missing)} played fixture(s) in week {args.match_week} still "
              f"missing attendance from FootyStats:")
        for m in missing:
            print(f"      {m.get('home_name')} v {m.get('away_name')} "
                  f"(id {m.get('id')})")
        if not args.allow_partial:
            print(f"\n  Refusing to render a partial week ({len(have)}/"
                  f"{len(have) + len(missing)} fixtures have attendance). "
                  f"Re-run with --allow-partial to chart what's available anyway, "
                  f"or wait for FootyStats to fill the rest in.")
            return 1
        print(f"  --allow-partial set: rendering with {len(have)}/"
              f"{len(have) + len(missing)} fixtures anyway.")

    if not have:
        print("  no fixtures with attendance data - nothing to chart")
        return 1

    print(f"\n  fixtures charted ({len(have)}):")
    for m in have:
        print(f"    {m['attendance']:>7,}  {m['home_name']} v {m['away_name']}")

    table_rows = season_to_date(matches, args.match_week)
    print(f"\n  season-to-date home attendance ({len(table_rows)} clubs):")
    for club, total, games in table_rows:
        print(f"    {total:>7,}  ({games} gm)  {club}")

    out = Path(args.outputs) / f"attendance_mw{args.match_week}_{args.competition}_inset.png"
    render(have, table_rows, out)
    print(f"\n  saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
