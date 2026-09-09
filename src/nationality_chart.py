#!/usr/bin/env python3
"""Pie chart of starting-XI nationalities for one match week.

    python src/nationality_chart.py thai-league-1 1
    python src/nationality_chart.py thai-league-1 20 --season 2025/2026
    python src/nationality_chart.py thai-league-2 3 --min-count 2

Data quality (checked against the 240 enriched Thai League 1 2025/26 matches):
the `nationality` ACF field is a consistently formatted full country name -
always "Thailand", never "Thai"/"THA"; "South Korea" never "Korea"/"KOR"; etc.
So the tally groups on the raw string directly - no normalization map needed.
The only handling required is a fallback bucket ("Unknown") for the handful of
players with a blank nationality and for the rare starter row that has no
`wp_id` (player was not in the lookup when the match was enriched).

Manual / on-demand only - not wired into any workflow.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
from sync_matches import _load_dotenv  # noqa: E402
_load_dotenv(_ROOT / ".env")
from wp_client import WPClient, WPError  # noqa: E402

# competition slug -> (competition taxonomy term id, display name)
COMPETITIONS = {
    "thai-league-1": (116, "Thai League 1"),
    "thai-league-2": (117, "Thai League 2"),
}

# site palette: dark green accent first (pinned to Thailand), then a green-led
# sequence with a few muted accents; greys reserved for Other / Unknown.
PALETTE = [
    "#0A3B25", "#1C6B45", "#2E9B67", "#4FB587", "#7FCBA6", "#A8DCC3",
    "#C9A227", "#E0B84D", "#8C6D3F", "#B08968", "#5B7C99", "#8AA9BD",
    "#6D5B7B", "#9B8AA6", "#A03E3E", "#C56B6B", "#3F6F52", "#68A98C",
]
GREY_OTHER = "#9AA0A0"
GREY_UNKNOWN = "#C7CCCC"


def _wp_get(wp: WPClient, path: str, params: dict) -> list:
    resp = wp._request("GET", path, params=params)
    if resp.status_code == 400 and "rest_post_invalid_page_number" in resp.text:
        return []
    return wp._json_or_raise(resp, f"GET {path}")


def fetch_match_week(wp: WPClient, comp_term: int, season: str, week: int) -> list[dict]:
    """Every published `match` post for competition + season string + match_week."""
    out: list[dict] = []
    page = 1
    while True:
        batch = _wp_get(wp, "wp/v2/match", {
            "competition": comp_term, "per_page": 100, "page": page,
            "status": "publish",
            "_fields": "id,title,meta.match_week,meta.season,meta.raw_match_data",
            "orderby": "id", "order": "asc",
        })
        if not batch:
            break
        for m in batch:
            meta = m.get("meta") or {}
            if str(meta.get("season")) == season and str(meta.get("match_week")) == str(week):
                out.append(m)
        page += 1
    return out


def collect_starters(matches: list[dict]) -> tuple[list[int | None], int]:
    """Flat list of every starter's wp_id (None where the row has no wp_id),
    plus a count of matches that had no raw_match_data (skipped).
    """
    starter_wp_ids: list[int | None] = []
    unenriched = 0
    for m in matches:
        rmd = (m.get("meta") or {}).get("raw_match_data")
        if not rmd:
            unenriched += 1
            print(f"  ! post {m['id']} "
                  f"({(m.get('title') or {}).get('rendered','')}) has no "
                  f"raw_match_data - not yet enriched, skipped")
            continue
        data = json.loads(base64.b64decode(rmd))
        for ps in data.get("player_stats", []):
            if ps.get("is_starter") is True:
                w = ps.get("wp_id")
                starter_wp_ids.append(int(w) if w else None)
    return starter_wp_ids, unenriched


def fetch_nationalities(wp: WPClient, wp_ids: set[int]) -> dict[int, str]:
    """wp_id -> nationality string ('' where blank/absent). Batched by `include`,
    with a per-id fallback for anything the batch call omits.
    """
    nat: dict[int, str] = {}
    ids = sorted(wp_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        rows = _wp_get(wp, "wp/v2/player", {
            "include": ",".join(map(str, chunk)), "per_page": 100,
            "_fields": "id,acf.nationality", "status": "publish",
        })
        for p in rows:
            nat[p["id"]] = ((p.get("acf") or {}).get("nationality") or "").strip()
    for pid in ids:
        if pid in nat:
            continue
        try:
            p = wp.get_player(pid, fields="id,acf.nationality")
            nat[pid] = ((p.get("acf") or {}).get("nationality") or "").strip()
        except WPError:
            nat[pid] = ""
    return nat


def tally(starter_wp_ids: list[int | None], nat: dict[int, str]) -> Counter:
    c: Counter = Counter()
    for w in starter_wp_ids:
        if w is None:
            c["Unknown"] += 1
        else:
            v = nat.get(w, "")
            c[v if v else "Unknown"] += 1
    return c


def group_small(counts: Counter, min_count: int) -> list[tuple[str, int]]:
    """Sorted [(label, count)]; nationalities below min_count folded into
    'Other (N countries)'. 'Unknown' is kept separate and always last.
    """
    unknown = counts.pop("Unknown", 0)
    named = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    keep = [(k, v) for k, v in named if v >= min_count]
    small = [(k, v) for k, v in named if v < min_count]
    rows = list(keep)
    if small:
        rows.append((f"Other ({len(small)} countries)", sum(v for _, v in small)))
    if unknown:
        rows.append(("Unknown", unknown))
    return rows


def colour_for(rows: list[tuple[str, int]]) -> list[str]:
    cols: list[str] = []
    pi = 0
    for label, _ in rows:
        if label == "Thailand":
            cols.append(PALETTE[0])
        elif label.startswith("Other ("):
            cols.append(GREY_OTHER)
        elif label == "Unknown":
            cols.append(GREY_UNKNOWN)
        else:
            pi += 1
            cols.append(PALETTE[pi % len(PALETTE)] if pi < len(PALETTE)
                        else PALETTE[1 + (pi % (len(PALETTE) - 1))])
    return cols


def render(rows: list[tuple[str, int]], comp_name: str, week: int,
           season: str, out_path: Path) -> None:
    labels = [k for k, _ in rows]
    sizes = [v for _, v in rows]
    total = sum(sizes)
    colours = colour_for(rows)

    fig, ax = plt.subplots(figsize=(11, 7.5), dpi=150)
    wedges, _texts, autotexts = ax.pie(
        sizes, colors=colours, startangle=90, counterclock=False,
        autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
        pctdistance=0.78, wedgeprops={"edgecolor": "white", "linewidth": 1.2},
    )
    for t in autotexts:
        t.set_color("white")
        t.set_fontsize(9)
        t.set_fontweight("bold")
    ax.set_aspect("equal")

    ax.set_title(
        f"{comp_name} — Match Week {week} — Starting XI Nationalities",
        fontsize=15, fontweight="bold", color=PALETTE[0], pad=18,
    )
    ax.text(0.5, -0.06,
            f"{season} season · {total} starters · {len(rows)} groups",
            transform=ax.transAxes, ha="center", fontsize=9, color="#555555")

    ax.legend(
        wedges,
        [f"{k}  —  {v}  ({v / total * 100:.1f}%)" for k, v in rows],
        title="Nationality",
        loc="center left", bbox_to_anchor=(1.0, 0.5),
        frameon=False, fontsize=9, title_fontsize=10,
    )
    fig.subplots_adjust(left=0.02, right=0.62)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("competition", choices=sorted(COMPETITIONS))
    ap.add_argument("match_week", type=int)
    ap.add_argument("--season", default="2026/2027",
                    help="season meta value to match (default: 2026/2027)")
    ap.add_argument("--min-count", type=int, default=3,
                    help="fold nationalities with fewer than this many starters "
                         "into 'Other' (default: 3; use --no-group to disable)")
    ap.add_argument("--no-group", action="store_true",
                    help="give every nationality its own slice")
    ap.add_argument("--outputs", default=str(_ROOT / "outputs"),
                    help="output directory (default: ./outputs)")
    args = ap.parse_args(argv)

    comp_term, comp_name = COMPETITIONS[args.competition]
    wp = WPClient(dry_run=True)  # reads only

    print(f"{comp_name} — {args.season} — Match Week {args.match_week}")
    matches = fetch_match_week(wp, comp_term, args.season, args.match_week)
    print(f"  matches found: {len(matches)}")
    if not matches:
        print("  nothing to chart - check the competition / week / --season")
        return 1

    starter_wp_ids, unenriched = collect_starters(matches)
    if not starter_wp_ids:
        print("  no starters (none of these matches are enriched yet)")
        return 1
    unique = {w for w in starter_wp_ids if w}
    no_wp = sum(1 for w in starter_wp_ids if w is None)
    print(f"  starters: {len(starter_wp_ids)}  "
          f"(unique players {len(unique)}, rows without wp_id {no_wp}, "
          f"un-enriched matches skipped {unenriched})")

    nat = fetch_nationalities(wp, unique)
    counts = tally(starter_wp_ids, nat)
    blank = counts.get("Unknown", 0)
    if blank:
        print(f"  Unknown bucket: {blank} "
              f"(blank nationality or missing wp_id)")

    rows = group_small(Counter(counts),
                       min_count=1 if args.no_group else args.min_count)

    print("\n  nationality tally:")
    for k, v in rows:
        print(f"    {v:>3}  {k}")

    out = Path(args.outputs) / f"nationality_mw{args.match_week}_{args.competition}.png"
    render(rows, comp_name, args.match_week, args.season, out)
    print(f"\n  saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
