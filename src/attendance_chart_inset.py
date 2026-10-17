#!/usr/bin/env python3
"""Weekly attendance line-chart + season-to-date table, styled for the Canva
"Attendance" post inset (white rounded card, no title/header — the title
lives on the Canva page itself: "Attendances" / "thai League 1" / "Match
week NN  2026/27"). Same overall pattern as src/nationality_chart_inset.py
(transparent figure, house colours, matplotlib, output to outputs/).

    python src/attendance_chart_inset.py thai-league-1 2

Data source
-----------
2026-09-16, v2: switched from the FootyStats API to the OFFICIAL Thai
League website's own public API (competition.tl.prod.c0d1um.io — the
backend behind thaileague.co.th's Results pages). Reasoning: FootyStats
had attendance for only 4 of Matchweek 2's 8 fixtures five days after
kickoff (the other 4 stuck at -1 = "not yet reported"), while the official
site had all 8 matches' attendance figures available immediately — it's
the venues reporting straight to the league, not a third-party aggregator
waiting on a feed. Cross-checked: every figure this API gives for the 4
matches FootyStats DID have matched FootyStats exactly, and Matchweek 1's
full set matches the already-published MW1 chart exactly too. So this is
strictly better: faster AND cross-validated as accurate. FootyStats is no
longer used by this script at all.

This API is not documented anywhere (found by opening thaileague.co.th's
Results page in a real browser and reading window.performance's resource
list for XHR calls — there is no public API doc). It needs no API key and
works fine over the network from a device_bash shell (confirmed
2026-09-16, same as api.football-data-api.com — it's specifically
footballsiam.com itself that device_bash can't reach, not FootyStats/Thai
League). Endpoints used, all under
https://competition.tl.prod.c0d1um.io/thaileague/api/ :
  - tournament-public/?latest_activated_season=True&show_in_public_website=True
      -> current season's tournaments incl. Thai League 1/2's id (`tournament`
         below) and name_en (e.g. "BYD SEALION 6 LEAGUE I 2026/27"). This is
         re-resolved every run instead of hardcoding an id, since (like
         FootyStats' season_id) it changes every season — but unlike
         FootyStats there's no need to hunt down and hardcode a new number
         each year, it's just a name-match away.
  - matchday-dropdown-public/?tournament=<id>&stage=&start_date__lte=<today>
      -> maps a human match week number ("name": "1", "2", ...) to the
         internal match_day id these endpoints actually key on. Filtered to
         start_date__lte=today, so a week that hasn't started yet won't
         resolve — that's the signal this script uses for "not played yet".
  - match-day-match-public/?tournament=<id>&match_day=<id>&only_valid_match=true&page=1
      -> every scheduled fixture for one match week, regardless of status
         (omit match_day to page through the WHOLE season instead, used for
         the season-to-date table). Each match has `attendance_number`
         (None until reported — in practice this API has it the moment the
         match is marked played, no lag observed so far), `home_team_name_en`
         / `away_team_name_en` (inconsistent casing/branding straight from
         the source — see TEAM_INFO below for the cleanup), `match_day_name`
         (the human week number as a string), and `match_status` (an int
         code; 2 = finished, observed for every MW1/MW2 match so far — not
         reverse-engineered further than that).
  Paginated DRF-style ({count, next, previous, results}) — fetch_all() below
  follows `next` until exhausted, since a full season won't fit on one page.

TEAM_INFO (below) maps the API's raw home/away team name strings (matched
case-insensitively, stripped) to a short chart-axis code AND a clean
display name for the season-to-date table — the raw strings are a messy
mix of ALL CAPS ("RASISALAI UNITED"), sponsor-prefixed ("PT Prachuap FC",
"True Bangkok United", "Singha Chiangrai United") and plain names
("Sukhothai FC"), not fit to print directly. Built 2026-09-16 from the 16
Thai League 1 2026/27 club names this API returns across Matchweeks 1-2,
codes cross-checked against the abbreviations visible in the Matchweek 1
Canva design (DAHUrrqKYMY). If a new club shows up (promotion, or Thai
League 2), add it here — unmapped names fall back to an auto-generated
code and a title-cased raw name so the script never crashes, but check the
output before shipping.

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
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402
import requests  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent

API_BASE = "https://competition.tl.prod.c0d1um.io/thaileague/api/"
USER_AGENT = "footballsiam-content/1.0 (+https://github.com/caustonsimon/footballsiam-data)"

# competition slug -> a distinctive, non-overlapping substring to find in a
# tournament's name_en from tournament-public (see module docstring). Must
# not be a substring of the OTHER competition's name — "LEAGUE I " (with the
# trailing space) does not match "...LEAGUE II 2026/27" because the next
# character there is a second "I", not a space.
COMPETITIONS = {
    "thai-league-1": ("LEAGUE I ", "Thai League 1"),
    "thai-league-2": ("LEAGUE II ", "Thai League 2"),
}

GREEN = "#004d25"
GOLD = "#ffde59"
WHITE = "#ffffff"
DARK_TEXT = "#12261b"
ROW_ALT = "#f2f6f3"     # faint green-grey banding for alternating table rows
GRID_GREY = "#d9dede"

# raw API team name (stripped, uppercased for lookup) -> (chart code, clean
# display name for the season-to-date table). See module docstring.
TEAM_INFO = {
    # raw API name -> (table display name, chart-axis short name)
    "AYUTTHAYA UNITED": ("Ayutthaya United", "Ayutthaya"),
    "BG PATHUM UNITED": ("Pathum United", "Pathum Utd"),
    "TRUE BANGKOK UNITED": ("Bangkok United", "Bangkok Utd"),
    "BURIRAM UNITED": ("Buriram United", "Buriram"),
    "SINGHA CHIANGRAI UNITED": ("Chiangrai United", "Chiangrai"),
    "CHONBURI FC": ("Chonburi", "Chonburi"),
    "LAMPHUN WARRIORS": ("Lamphun Warrior", "Lamphun"),
    "PATTANI FC": ("Pattani", "Pattani"),
    "PORT FC": ("Port", "Port"),
    "PT PRACHUAP FC": ("Prachuap", "Prachuap"),
    "RASISALAI UNITED": ("Rasi Salai United", "Rasi Salai"),
    "RATCHABURI FC": ("Ratchaburi", "Ratchaburi"),
    "RAYONG FC": ("Rayong", "Rayong"),
    "SISAKET UNITED": ("Sisaket United", "Sisaket"),
    "SUKHOTHAI FC": ("Sukhothai", "Sukhothai"),
    "UTHAI THANI FC": ("Uthai Thani", "Uthai Thani"),
}


def team_info(raw_name: str) -> tuple[str, str]:
    """(table display name, chart-axis short name) for a raw API team name.
    2026-09-16: axis labels used to be 4-5 letter codes (BRUTD v CRUTD) —
    Simon couldn't read them on the chart, so both the table and the axis
    now use real (if shortened) club names instead. See module docstring.
    """
    key = (raw_name or "").strip().upper()
    if key in TEAM_INFO:
        return TEAM_INFO[key]
    display = (raw_name or "Unknown").strip().title()
    print(f"  ! no TEAM_INFO entry for {raw_name!r} — using {display!r} as-is "
          f"for both table and axis, add a shortened form to TEAM_INFO if "
          f"this is too long to read on the chart")
    return display, display


for _f in [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]:
    try:
        fm.fontManager.addfont(_f)
    except Exception:
        pass
plt.rcParams["font.family"] = "Liberation Sans"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def fetch_all(session: requests.Session, url: str, params: dict | None = None) -> list[dict]:
    """Follow a DRF-paginated endpoint's `next` link until exhausted."""
    out: list[dict] = []
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    out.extend(data.get("results", data if isinstance(data, list) else []))
    next_url = data.get("next") if isinstance(data, dict) else None
    while next_url:
        resp = session.get(next_url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        out.extend(data.get("results", []))
        next_url = data.get("next")
    return out


def resolve_tournament(session: requests.Session, competition: str) -> tuple[int, str]:
    needle, comp_name = COMPETITIONS[competition]
    resp = session.get(
        API_BASE + "tournament-public/",
        params={"latest_activated_season": "True", "show_in_public_website": "True"},
        timeout=30,
    )
    resp.raise_for_status()
    tournaments = resp.json()
    tournaments = tournaments.get("results", tournaments) if isinstance(tournaments, dict) else tournaments
    for t in tournaments:
        name_en = (t.get("name_en") or "").upper()
        if needle in name_en:
            return t["id"], t.get("name_en", comp_name)
    raise RuntimeError(
        f"could not find a current tournament matching {needle!r} for {comp_name} "
        f"in tournament-public response — Thai League may have renamed the "
        f"competition; inspect the API and update COMPETITIONS"
    )


def resolve_match_day(session: requests.Session, tournament_id: int, week: int) -> dict | None:
    resp = session.get(
        API_BASE + "matchday-dropdown-public/",
        params={"tournament": tournament_id, "stage": "", "start_date__lte": date.today().isoformat()},
        timeout=30,
    )
    resp.raise_for_status()
    days = resp.json()
    for d in days:
        if str(d.get("name")) == str(week):
            return d
    return None


def week_fixtures(session: requests.Session, tournament_id: int, match_day_id: int) -> tuple[list[dict], list[dict]]:
    """(have, missing) — every scheduled fixture for one match_day, split by
    whether attendance_number is populated. `missing` covers both "not yet
    played" and (should it ever recur) "played but unreported" — this API
    has not shown that gap so far (see module docstring), so the two aren't
    distinguished the way the old FootyStats-based script did.
    """
    matches = fetch_all(
        session, API_BASE + "match-day-match-public/",
        params={"tournament": tournament_id, "tournament_team": "", "stage": "",
                "match_day": match_day_id, "only_valid_match": "true",
                "show_have_photo_field": "true"},
    )
    have = [m for m in matches if m.get("attendance_number") is not None]
    missing = [m for m in matches if m not in have]
    have.sort(key=lambda m: (m.get("start_date") or "", m.get("start_time") or ""))
    return have, missing


def season_to_date(session: requests.Session, tournament_id: int, upto_week: int) -> list[tuple[str, int, int]]:
    """[(display_name, season_total, games_played)] for every club that has
    hosted at least one match (match_day_name <= upto_week) with a recorded
    attendance, sorted descending by season total. HOME attendance only,
    same as the Matchweek 1 design ("SEASON-TO-DATE — HOME ATTENDANCE").
    """
    matches = fetch_all(
        session, API_BASE + "match-day-match-public/",
        params={"tournament": tournament_id, "tournament_team": "", "stage": "",
                "match_status": "results", "only_valid_match": "true",
                "show_have_photo_field": "true"},
    )
    totals: dict[str, int] = {}
    games: dict[str, int] = {}
    for m in matches:
        wk_name = m.get("match_day_name")
        try:
            wk = int(wk_name)
        except (TypeError, ValueError):
            continue
        if wk > upto_week:
            continue
        att = m.get("attendance_number")
        if att is None:
            continue
        display, _ = team_info(m.get("home_team_name_en", ""))
        totals[display] = totals.get(display, 0) + int(att)
        games[display] = games.get(display, 0) + 1
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
    ax = fig.add_axes([0.105, 0.70, 0.85, 0.225])
    xs = list(range(len(fixtures)))
    ys = [f["attendance_number"] for f in fixtures]
    labels = [
        f"{team_info(f['home_team_name_en'])[1]} v {team_info(f['away_team_name_en'])[1]}"
        for f in fixtures
    ]

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
    ax.set_xticklabels([])  # drawn manually below — see comment
    ax.set_ylabel("Attendance", fontsize=14, fontweight="bold", color="#4a5a52",
                   labelpad=10)
    # IMPORTANT — two separate, non-obvious rendering bugs were found here
    # (2026-09-16, matplotlib 3.10.9, Agg backend) while making these axis
    # labels readable (they used to be short codes like "BRUTD v CRUTD").
    # Both silently DROP THE FIRST CHARACTER of a long, steeply-rotated
    # label — not a clip/paint-over issue, verified down to the SVG glyph
    # level (the <use> list for the Text omits the first glyph entirely).
    # Neither is fixed by .set_clip_on(False).
    #   1. rotation_mode="anchor" on this ax.text() call triggers it
    #      whenever the figure has a second Axes anywhere in it (this
    #      script always does — ax_t, the table below). Fix: don't pass
    #      rotation_mode="anchor" here; default rotation mode has no such
    #      bug and still anchors acceptably at ha="right" for this angle.
    #   2. Independently of (1), it also triggers whenever a label's
    #      rotated extent comes close to / overlaps another Axes' patch
    #      elsewhere in the figure — specifically, ax_t's green header
    #      Rectangle (added below) sits right at ax_t's top edge, which
    #      is immediately below where these labels hang. Fix: keep enough
    #      vertical gap between `ax`'s bottom (currently 0.70) and ax_t's
    #      top (0.435) that even the longest label (~24 chars at 68°) does
    #      not reach down into ax_t's territory. Bisected empirically by
    #      re-adding pieces of the figure one at a time until the bug
    #      reproduced/disappeared — don't shrink this gap without
    #      re-testing a long label ("Ratchaburi v Bangkok Utd") at high
    #      zoom; a quick look at normal size will not catch 1-2 missing
    #      characters.
    for _x, _label in zip(xs, labels):
        ax.text(
            _x, -0.02, _label, transform=ax.get_xaxis_transform(),
            rotation=68, ha="right", va="top",
            fontsize=12.5, fontweight="bold", color="#2c3b33", clip_on=False,
        )

    # ---- bottom: season-to-date table -------------------------------------
    ax_t = fig.add_axes([0.0, 0.0, 1.0, 0.435])
    ax_t.set_xlim(0, 1)
    ax_t.set_ylim(0, 1)
    ax_t.axis("off")
    # ax_t's own background patch is opaque by default and gets painted
    # AFTER ax (it's added later) — that silently covered the bottom of any
    # x-axis label in `ax` long/steep enough to reach down into ax_t's
    # figure-space (0 to 0.435), which is exactly what a long rotated
    # fixture label like "Ratchaburi v Bangkok Utd" does. Cost real time to
    # track down (2026-09-16) because .set_clip_on(False) on the label text
    # itself does nothing for this — the label was never being clipped, it
    # was being drawn OVER. Making this patch transparent fixes it; the
    # white card behind everything already provides the background.
    ax_t.patch.set_alpha(0)

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
    ap.add_argument("--outputs", default=str(_ROOT / "outputs"),
                    help="output directory (default: ./outputs)")
    ap.add_argument("--allow-partial", action="store_true",
                    help="render even if some of this match week's fixtures are "
                         "missing attendance data (by default the script refuses "
                         "and exits 1, printing what's missing, so a partial chart "
                         "is never shipped by accident).")
    ap.add_argument("--social-out-dir", default=None,
                    help="also write the Instagram-first 1080x1350 carousel "
                         "(src/social_charts.py) and its data JSON here. This "
                         "carousel is what gets posted; the inset is for the site.")
    args = ap.parse_args(argv)

    session = _session()

    tournament_id, tournament_name = resolve_tournament(session, args.competition)
    print(f"{tournament_name} (tournament id {tournament_id}) — Match Week {args.match_week}")

    match_day = resolve_match_day(session, tournament_id, args.match_week)
    if match_day is None:
        print(f"  ! Match Week {args.match_week} hasn't started yet (or doesn't exist) "
              f"per the Thai League site's own match-day list.")
        return 1

    have, missing = week_fixtures(session, tournament_id, match_day["id"])

    if missing:
        print(f"  ! {len(missing)} fixture(s) in week {args.match_week} have no "
              f"attendance yet (not played, or reported but not yet posted):")
        for m in missing:
            print(f"      {m.get('home_team_name_en')} v {m.get('away_team_name_en')} "
                  f"(status={m.get('match_status')}, id {m.get('id')})")
        if not args.allow_partial:
            print(f"\n  Refusing to render a partial week ({len(have)}/"
                  f"{len(have) + len(missing)} fixtures have attendance). "
                  f"Re-run with --allow-partial to chart what's available anyway, "
                  f"or wait for the rest to be played/reported.")
            return 1
        print(f"  --allow-partial set: rendering with {len(have)}/"
              f"{len(have) + len(missing)} fixtures anyway.")

    if not have:
        print("  no fixtures with attendance data - nothing to chart")
        return 1

    print(f"\n  fixtures charted ({len(have)}):")
    for m in have:
        print(f"    {m['attendance_number']:>7,}  {m['home_team_name_en']} v {m['away_team_name_en']}")

    table_rows = season_to_date(session, tournament_id, args.match_week)
    print(f"\n  season-to-date home attendance ({len(table_rows)} clubs):")
    for club, total, games in table_rows:
        print(f"    {total:>7,}  ({games} gm)  {club}")

    out = Path(args.outputs) / f"attendance_mw{args.match_week}_{args.competition}_inset.png"
    render(have, table_rows, out)
    print(f"\n  saved {out}")

    if args.social_out_dir:
        import json
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import social_charts
        data = {
            "league": COMPETITIONS[args.competition][1], "mw": args.match_week,
            "matches": [{"home": team_info(m["home_team_name_en"])[0],
                         "away": team_info(m["away_team_name_en"])[0],
                         "att": int(m["attendance_number"])} for m in have],
            "season": [{"club": c, "total": t, "games": g} for c, t, g in table_rows],
        }
        sd = Path(args.social_out_dir); sd.mkdir(parents=True, exist_ok=True)
        jp = sd / f"attendance_mw{args.match_week}.json"
        jp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        for path in social_charts.attendance_deck(data).render(sd, f"attendance_mw{args.match_week}"):
            print(f"  social slide {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
