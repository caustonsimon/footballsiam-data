#!/usr/bin/env python3
"""Starting-XI nationalities pie chart, styled for the Canva "Lineup Breakdown"
inset (transparent canvas, rounded solid-green card, no title/header — the
title lives on the Canva page itself, same pattern as the attendance chart).

    python src/nationality_chart_inset.py thai-league-1 1
    python src/nationality_chart_inset.py thai-league-1 20 --season 2025/2026
    python src/nationality_chart_inset.py thai-league-2 3 --min-count 3

Data source (2026-09-10 update, two changes):

1. For Thai League 1, this script first tries the site's own JSON endpoint
   (`GET /wp-json/fs/v1/nationality-breakdown`), which returns the exact same
   starting-XI nationality counts the homepage's Nationality Breakdown widget
   renders (same function, same season pin, same starter-only filter — single
   source of truth, so future tweaks to that widget apply here automatically
   with zero extra work). This only satisfies a request for the *current*
   latest match week of the *current* season — if the requested
   `match_week`/`--season` don't match what the site currently reports as
   latest (e.g. a historical backfill, or Thai League 2, which the widget
   doesn't cover), it falls back to the original direct computation below
   unchanged. Pass --no-site-api to always skip the site and compute directly
   (useful for comparing the two paths, or if the endpoint is ever down).

2. IMPORTANT — --data-file, for the weekly automated run specifically: when
   this script runs inside a Claude-driven shell (the weekly scheduled task,
   via device_bash), NEITHER of the network paths above can actually reach
   footballsiam.com — Claude's sandboxed shells go through an org-level
   egress proxy that blocks raw HTTP to it entirely (confirmed while building
   this: even a plain `curl footballsiam.com` from that shell gets rejected).
   Only Claude's own WebFetch tool and the WordPress MCP connector can reach
   the site from within a Claude session. So for the automated weekly run,
   the calling routine must fetch
   https://footballsiam.com/wp-json/fs/v1/nationality-breakdown itself via
   WebFetch, save that response verbatim to a local JSON file, and pass
   --data-file pointing at it — this script then reads counts straight from
   that file and never touches the network at all. If you're running this
   manually from a normal terminal with real internet access (not through a
   Claude-driven shell), you don't need --data-file — the network paths above
   work fine on their own, exactly as before.

The direct-computation path (fetch_match_week / collect_starters /
fetch_nationalities / tally) is copied verbatim from nationality_chart.py —
same WPClient-based WordPress lookups, same "Unknown" fallback bucket for
blank nationality / missing wp_id, same grouping logic (group_small). Only
`render()` differs: this produces the polished Canva-inset visual, not the
plain white-background chart nationality_chart.py makes.

Style notes (locked in after a lot of back-and-forth on Matchweek 1 — see the
Football Siam content pipeline chat if you need the reasoning):
  - Card background is SOLID GREEN (not white) — the attendance chart's card
    is white, this one is deliberately different per explicit feedback.
  - Slice colours deliberately avoid ALL green hues (Thailand/Brazil are NOT
    green here) because the card itself is green — green-on-green was
    unreadable. Thailand is pinned to gold; everything else cycles through
    NO_GREEN_PALETTE below.
  - Legend text size: matplotlib silently ignores the `fontsize` kwarg on
    ax.legend(...) whenever `prop={...}` is also passed — it falls back to
    the rcParams default instead. That bug ate several iterations before it
    was caught. This script sets legend text size/weight directly on the
    rendered Text objects after legend() returns (see render()) — never
    reintroduce a `prop=` dict on the legend call without also verifying the
    rendered font size actually changed.
  - min_count default is 5, not 3 (nationality_chart.py's default) — with
    Thailand/Brazil dominant, a min_count of 3 produces ~13 legend rows,
    which is too many to render at a legible size. 5 gave 6 rows for MW1
    (Thailand, Brazil, Japan, Australia, Other, Unknown) — that's the target
    row count this layout is tuned for. If a given week's spread is very
    different (e.g. much more even), sanity-check the rendered legend still
    fits without overlap before shipping, and adjust LEGEND_FONTSIZE /
    axes fractions in render() if not — don't just ship an overflowing
    legend. (Applies whichever data path filled `rows` — group_small() is
    shared by both.)
  - Output aspect ratio (9.6x7.6in, i.e. 1440x1140px @ dpi150) does NOT
    exactly match the Canva placeholder box (980.51189337526 x
    862.4674084300458px, ratio ~1.1369). After inserting this PNG into the
    Canva design (via Canva MCP insert_fill/update_fill), you MUST follow up
    with a crop_media operation on that same element setting
    top=0, left=0, width=980.51189337526, height=862.4674084300458 —
    otherwise Canva's automatic cover-fit crops the right edge of the
    legend text off. This is a Canva-side step, not something fixable here.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent

# 2026-09-20: WPClient/wp_client/sync_matches are repo-internal and only
# needed by the two network fallback paths below (no --data-file given).
# The automated weekly run always passes --data-file (see docstring point
# 2) and never touches either path, so these are imported lazily inside
# _wp_client_or_raise() instead of at module level -- that's what makes
# this script runnable in a fresh environment with zero access to the
# footballsiam-data repo (e.g. a cloud sandbox with no git credentials for
# it), same pattern as attendance_chart_inset.py v2.
WPClient = None  # type: ignore[assignment]  # populated by _wp_client_or_raise()
WPError = Exception  # placeholder so `except WPError:` below stays valid
                     # syntax before _wp_client_or_raise() ever runs


def _wp_client_or_raise():
    """Lazily import sync_matches/wp_client and return a fresh WPClient
    class + WPError, raising a clear error if the repo-internal modules
    aren't importable (e.g. running with --data-file was skipped by
    mistake, or this script was copied out of the repo without them).
    """
    global WPClient, WPError
    sys.path.insert(0, str(_ROOT / "src"))
    try:
        from sync_matches import _load_dotenv
        from wp_client import WPClient as _WPClient, WPError as _WPError
    except ImportError as exc:
        raise RuntimeError(
            "This script needs sync_matches.py/wp_client.py (repo-internal) "
            "for network access, but they're not importable. Pass --data-file "
            "instead (fetch the site's JSON via WebFetch first) if you're "
            "running outside the footballsiam-data repo."
        ) from exc
    _load_dotenv(_ROOT / ".env")
    WPClient, WPError = _WPClient, _WPError
    return WPClient, WPError

# competition slug -> (competition taxonomy term id, display name)
COMPETITIONS = {
    "thai-league-1": (116, "Thai League 1"),
    "thai-league-2": (117, "Thai League 2"),
}

GREEN = "#004d25"
GOLD = "#ffde59"
WHITE = "#ffffff"
CARD_TOP = "#0f6b3d"     # solid green card, subtle gradient top->bottom
CARD_BOTTOM = "#013a1f"

# no-green palette — card is solid green, so slice colours stay clear of
# green hues entirely for contrast. Gold is reserved for Thailand.
NO_GREEN_PALETTE = [
    "#3E6FA8",   # blue
    "#C0392B",   # brick red
    "#E08E2B",   # orange
    "#8E44AD",   # purple
    "#C2547A",   # rose
    "#6FA8D9",   # sky blue
    "#8C5A3C",   # brown
    "#B5348C",   # magenta
    "#7B2D3B",   # maroon
    "#2F4A6B",   # navy
]
GREY_OTHER = "#9AA0A0"
GREY_UNKNOWN = "#C7CCCC"

for _f in [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]:
    try:
        fm.fontManager.addfont(_f)
    except Exception:
        pass
plt.rcParams["font.family"] = "Liberation Sans"


def _wp_get(wp: WPClient, path: str, params: dict) -> list:
    resp = wp._request("GET", path, params=params)
    if resp.status_code == 400 and "rest_post_invalid_page_number" in resp.text:
        return []
    return wp._json_or_raise(resp, f"GET {path}")


def fetch_latest_from_site(wp: WPClient, comp_name: str) -> dict | None:
    """Try the site's own nationality-breakdown endpoint first — it's the
    exact same data (and same computation) the homepage widget renders, so
    using it here means this chart can never silently drift from the
    website. Returns the endpoint's JSON dict on success, or None on any
    failure (network error, non-2xx, snippet not active yet, competition
    not Thai League 1, or the endpoint reporting no data yet) — callers
    should fall back to the direct WPClient computation below when this
    returns None.

    IMPORTANT: this goes through WPClient's own `_request` (the same call
    fetch_match_week/fetch_nationalities use for wp/v2/*), not a bare
    `requests.get()`. The site's SiteGround anti-bot layer has been observed
    to 404 plain/generic HTTP clients on API-shaped URLs (confirmed while
    building this — a browser session got real data on the exact same URL a
    bare requests.get() 404'd on) — WPClient's session already carries
    whatever headers/retry logic make the wp/v2/* calls below work reliably
    against that anti-bot layer, so route this through it too rather than
    reinventing that handling here.
    """
    if comp_name != "Thai League 1":
        return None  # the widget/endpoint is Thai League 1 only
    try:
        data = _wp_get(wp, "fs/v1/nationality-breakdown", {})
    except Exception as exc:
        print(f"  (site API unavailable, falling back to direct computation: {exc})")
        return None
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return data


def load_data_file(path: Path) -> dict:
    """Load a JSON file saved verbatim from the site's nationality-breakdown
    endpoint (see --data-file in the module docstring — this is how the
    weekly automated run gets data in, since it can't reach the network
    itself).

    2026-09-12: the site switched to the v6 route (Snippet 103, "FS
    Nationality Breakdown v6 (FIX)"), which fixes an undercount bug in the
    old route but no longer includes a top-level "available" key in its
    shape: {"season": str, "week": int|str|None, "latest": [...], "overall": [...]}.
    So "available" is treated as absent-by-default now: data counts as
    available when "week" is non-null and at least one of "latest"/"overall"
    is non-empty. The old {"available": bool, ...} shape (if ever seen again)
    is still honoured for backward compatibility.
    Raises on missing file / malformed JSON / not-yet-available data so the
    caller fails loudly rather than silently charting nothing.
    """
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain available nationality-breakdown data")
    if "available" in data:
        is_available = bool(data.get("available"))
    else:
        is_available = bool(data.get("week")) and bool(data.get("latest") or data.get("overall"))
    if not is_available:
        raise ValueError(f"{path} does not contain available nationality-breakdown data")
    return data


def rows_from_site_slices(slices: list[dict], min_count: int) -> list[tuple[str, int]]:
    """Convert the site API's [{nat, cnt, pct, color}, ...] into this script's
    own (label, count) rows via the same group_small() grouping used by the
    direct-computation path, so the rendered chart is identical either way.
    """
    counts: Counter = Counter()
    for s in slices:
        counts[s["nat"]] = int(s["cnt"])
    return group_small(counts, min_count=min_count)


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
            cols.append(GOLD)
        elif label.startswith("Other ("):
            cols.append(GREY_OTHER)
        elif label == "Unknown":
            cols.append(GREY_UNKNOWN)
        else:
            cols.append(NO_GREEN_PALETTE[pi % len(NO_GREEN_PALETTE)])
            pi += 1
    return cols


def render(rows: list[tuple[str, int]], out_path: Path) -> None:
    total = sum(v for _, v in rows)
    sizes = [v for _, v in rows]
    colours = colour_for(rows)

    fig = plt.figure(figsize=(9.6, 7.6), dpi=150)
    fig.patch.set_alpha(0.0)

    CARD_X, CARD_Y, CARD_W, CARD_H = 0.012, 0.012, 0.976, 0.976
    ROUND = 0.035

    # soft drop shadow: several stacked, offset, low-alpha rounded rects
    n_shadow = 7
    for i in range(n_shadow, 0, -1):
        off = i * 0.0016
        alpha = 0.035 * (1 - (i - 1) / n_shadow)
        shadow = FancyBboxPatch(
            (CARD_X + off, CARD_Y - off), CARD_W, CARD_H,
            transform=fig.transFigure, figure=fig,
            boxstyle=f"round,pad=0,rounding_size={ROUND}",
            linewidth=0, facecolor="#0a1f14", alpha=alpha, zorder=-20 - i,
        )
        fig.patches.append(shadow)

    # card with a subtle vertical green gradient fill, clipped to rounded shape
    card = FancyBboxPatch(
        (CARD_X, CARD_Y), CARD_W, CARD_H,
        transform=fig.transFigure, figure=fig,
        boxstyle=f"round,pad=0,rounding_size={ROUND}",
        linewidth=0, facecolor=CARD_TOP, zorder=-10,
    )
    fig.patches.append(card)

    grad_ax = fig.add_axes([CARD_X, CARD_Y, CARD_W, CARD_H], zorder=-9)
    grad_ax.set_xlim(0, 1)
    grad_ax.set_ylim(0, 1)
    grad_ax.axis("off")
    gradient = np.linspace(0, 1, 256).reshape(-1, 1)
    cmap = LinearSegmentedColormap.from_list("card_grad", [CARD_TOP, CARD_BOTTOM])
    im = grad_ax.imshow(
        gradient, aspect="auto", cmap=cmap, origin="upper",
        extent=[0, 1, 0, 1], zorder=-9,
    )
    im.set_clip_path(card)

    ax = fig.add_axes([0.005, 0.02, 0.535, 0.96], zorder=1)
    wedges, _texts, autotexts = ax.pie(
        sizes, colors=colours, startangle=90, counterclock=False,
        # only wide slices get a number stamped on them — a big label can't
        # fit inside a thin sliver without overlapping its neighbours; the
        # legend states every slice's percentage clearly regardless
        autopct=lambda p: f"{p:.0f}%" if p >= 15 else "",
        pctdistance=0.66, wedgeprops={"edgecolor": WHITE, "linewidth": 2.6},
    )
    for t in autotexts:
        t.set_color("white")
        t.set_fontsize(50)
        t.set_fontweight("bold")
        t.set_path_effects([pe.withStroke(linewidth=5, foreground="#00000066")])
    ax.set_aspect("equal")
    # nudge the pie slightly left within its axes box
    _xlim = ax.get_xlim()
    ax.set_xlim(_xlim[0] + 0.16, _xlim[1] + 0.16)

    ax_legend = fig.add_axes([0.545, 0.02, 0.445, 0.96], zorder=1)
    ax_legend.axis("off")
    disp = lambda k: "Other" if k.startswith("Other (") else k  # noqa: E731
    legend_labels = [f"{disp(k)} — {v / total * 100:.0f}%" for k, v in rows]
    LEGEND_FONTSIZE = 34
    leg = ax_legend.legend(
        wedges, legend_labels,
        loc="center", frameon=False, fontsize=LEGEND_FONTSIZE,
        labelcolor=WHITE, handlelength=1.3, handleheight=1.9,
        borderaxespad=0, bbox_to_anchor=(0.44, 0.5), labelspacing=1.3,
        handletextpad=0.5,
    )
    # IMPORTANT: do not add `prop={...}` to the legend() call above — see the
    # module docstring. Set size/weight on the rendered Text objects instead.
    for t in leg.get_texts():
        t.set_fontsize(LEGEND_FONTSIZE)
        t.set_fontweight("bold")
    for handle in leg.legend_handles:
        handle.set_edgecolor(WHITE)
        handle.set_linewidth(2.0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, transparent=True)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("competition", choices=sorted(COMPETITIONS))
    ap.add_argument("match_week", type=int)
    ap.add_argument("--season", default="2026/2027",
                    help="season meta value to match (default: 2026/2027)")
    ap.add_argument("--min-count", type=int, default=5,
                    help="fold nationalities with fewer than this many starters "
                         "into 'Other' (default: 5 — tuned for a ~6-row legend "
                         "at this layout's font size; see module docstring)")
    ap.add_argument("--outputs", default=str(_ROOT / "outputs"),
                    help="output directory (default: ./outputs)")
    ap.add_argument("--no-site-api", action="store_true",
                    help="skip the site's JSON endpoint and always compute "
                         "directly via WPClient (for debugging/comparison)")
    ap.add_argument("--data-file", type=Path, default=None,
                    help="path to a JSON file saved verbatim from the site's "
                         "nationality-breakdown endpoint (fetched via WebFetch, "
                         "not this script) — use this when running inside a "
                         "Claude-driven shell that can't reach the network "
                         "itself; see the module docstring. Skips both network "
                         "paths below entirely when given.")
    args = ap.parse_args(argv)

    comp_term, comp_name = COMPETITIONS[args.competition]

    rows: list[tuple[str, int]] | None = None

    if args.data_file is not None:
        print(f"{comp_name} — {args.season} — Match Week {args.match_week}  [from --data-file]")
        site_data = load_data_file(args.data_file)
        site_season = str(site_data.get("season"))
        site_week = int(site_data.get("week", -1))
        if site_season != str(args.season) or site_week != args.match_week:
            print(f"  ! WARNING: {args.data_file} contains MW{site_week} of {site_season}, "
                  f"but you asked for MW{args.match_week} of {args.season} — using the "
                  f"file's data anyway (it's the only source available in this mode), "
                  f"but double-check this is the week you meant to chart.")
        rows = rows_from_site_slices(site_data["latest"], min_count=args.min_count)
        starters_count = sum(v for _, v in rows)
        print(f"  starters (from data file): {starters_count}")

    if rows is None and not args.no_site_api:
        WPClient, WPError = _wp_client_or_raise()
        wp = WPClient(dry_run=True)  # reads only — used for both the site-API call and the fallback
        site_data = fetch_latest_from_site(wp, comp_name)
        if site_data is not None:
            site_season = str(site_data.get("season"))
            site_week = int(site_data.get("week", -1))
            if site_season == str(args.season) and site_week == args.match_week:
                print(f"{comp_name} — {args.season} — Match Week {args.match_week}  [via site API]")
                rows = rows_from_site_slices(site_data["latest"], min_count=args.min_count)
                starters_count = sum(v for _, v in rows)
                print(f"  starters (from site): {starters_count}")
            else:
                print(f"  (site API reports MW{site_week} of {site_season} — "
                      f"requested MW{args.match_week} of {args.season} differs, "
                      f"computing directly instead)")

    if rows is None:
        WPClient, WPError = _wp_client_or_raise()
        wp = WPClient(dry_run=True)  # reads only — direct computation, requires real network
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

        rows = group_small(Counter(counts), min_count=args.min_count)

    print("\n  nationality tally:")
    for k, v in rows:
        print(f"    {v:>3}  {k}")
    if len(rows) > 8:
        print(f"\n  ! {len(rows)} rows is more than this layout is tuned for "
              f"(target ~6) — consider a higher --min-count, or check the "
              f"rendered legend for overlap before using it.")

    out = Path(args.outputs) / f"nationality_mw{args.match_week}_{args.competition}_inset.png"
    render(rows, out)
    print(f"\n  saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
