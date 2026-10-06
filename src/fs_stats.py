"""fs_stats.py — Football Siam player-stats toolkit (FootyStats API).

Shared by the fs-scout-report, fs-player-leaderboards and fs-chart-ideas skills.
Needs: requests, pandas, matplotlib, mplsoccer.  Key in env FOOTYSTATS_KEY.
Caches every API response under ./fs_cache so reruns cost no requests.
"""
import json, os, time, hashlib
import requests
import pandas as pd

BASE = "https://api.football-data-api.com/"
CACHE = os.path.join(os.getcwd(), "fs_cache")
os.makedirs(CACHE, exist_ok=True)

SEASONS = {  # FootyStats season_id
    "T1 2026/27": 17367, "T1 2025/26": 15140,
    "T2 2026/27": 17896,
}

# House style: no sponsor names. Extend as needed (FootyStats name -> Football Siam name).
NAME_FIXES = {
    "BG Pathum United": "Pathum United", "Bangkok Glass": "Pathum United",
    "PTT Rayong": "Rayong", "PT Prachuap": "Prachuap", "PTT Prachuap": "Prachuap",
    "Port FC": "Port", "Bangkok FC": "Bangkok", "True Bangkok United": "Bangkok United",
    "SCG Muangthong United": "Muangthong United", "Muang Thong United": "Muangthong United",
    "Kanchanaburi": "Kanchanaburi Power", "Rasi Salai United": "Rasisalai United",
}

# Current-club overrides where FootyStats has no current row (e.g. moved abroad) or is wrong.
# FootyStats player id -> club name (house style: no sponsor names). Verify before adding.
CURRENT_CLUB_OVERRIDES = {
    160449: "Omiya Ardija, Japan",   # Suphanat Mueanta — confirmed by Simon 23 Sep 2026
}

# Player name corrections (FootyStats id -> name as Football Siam spells it). Confirmed by Simon.
PLAYER_NAME_FIXES = {
    44432: "Peeradol Chamratsamee",   # FootyStats has "Peeradon" — Simon 23 Sep 2026
}


def position_group(raw):
    """FootyStats position -> 'Goalkeeper' / 'Defender' / 'Midfielder' / 'Forward' ('' if unknown).
    FootyStats mostly uses those four, but some rows carry finer labels ('Centre Back',
    'Central Midfield', 'Centre Forward'). Same keyword rule as map_position() in the
    footballsiam-data sync pipeline (enrich.py), so the site and the charts agree."""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if "keeper" in s or s == "gk":
        return "Goalkeeper"
    if any(w in s for w in ("back", "defender", "defence", "defense", "cb", "rb", "lb", "wb")):
        return "Defender"
    if "midfield" in s or s in ("dm", "cm", "am", "mf"):
        return "Midfielder"
    if any(w in s for w in ("forward", "striker", "winger", "wing", "attacker", "fw", "cf")):
        return "Forward"
    return ""


def unmapped_positions(season_id):
    """Position labels in a season that position_group() can't place (those players are
    left out of every cohort). Should be empty; check it when a new season starts."""
    lp = league_players(season_id)
    return sorted(set(lp.loc[lp["position_group"] == "", "position"].astype(str)))


def _fix_names(df):
    if len(df) and "id" in df:
        df["name"] = [PLAYER_NAME_FIXES.get(int(i), n) for i, n in zip(df["id"], df["name"])]
    return df


GREEN, GOLD, INK, PAPER = "#0A3B25", "#e3c567", "#111111", "#ffffff"


def _get(endpoint, **params):
    key = os.environ["FOOTYSTATS_KEY"]
    h = hashlib.md5(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
    path = os.path.join(CACHE, f"{endpoint}_{h}.json")
    if os.path.exists(path):
        return json.load(open(path))
    for attempt in range(3):
        r = requests.get(BASE + endpoint, params={"key": key, **params}, timeout=30)
        if r.status_code == 429:
            raise RuntimeError("FootyStats hourly limit (1800/h) hit — stop and resume later")
        if r.ok:
            d = r.json()
            json.dump(d, open(path, "w"))
            return d
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()


def clean_name(n):
    n = (n or "").strip()
    return NAME_FIXES.get(n, n)


def team_names(season_id):
    d = _get("league-teams", season_id=season_id)
    return {t["id"]: clean_name(t.get("cleanName") or t.get("name")) for t in d.get("data", [])}


def club_label(player_id, stats_season_id, current_season_id=17367):
    """Club to print next to a player when charting `stats_season_id` data.
    FootyStats' club_team_id is per competition-season, so last season's row gives
    LAST season's club. If he has since moved, return 'Now (2025/26: Then)'."""
    d = _get("player-stats", player_id=player_id).get("data", [])
    by = {r.get("competition_id"): r.get("club_team_id") for r in d}
    names = {**team_names(stats_season_id), **team_names(current_season_id)}
    then, now = by.get(stats_season_id), by.get(current_season_id)
    t = names.get(then, "?")
    if player_id in CURRENT_CLUB_OVERRIDES:
        now_name = CURRENT_CLUB_OVERRIDES[player_id]
        season = next((r.get("season") for r in d if r.get("competition_id") == stats_season_id), "")
        season = season[:5] + season[7:] if len(season) == 9 and season[4] == "/" else season
        return now_name if now_name == t else f"{now_name} ({season}: {t})"
    if stats_season_id == current_season_id or now == then:
        return t
    season = next((r.get("season") for r in d if r.get("competition_id") == stats_season_id), "")
    if len(season) == 9 and season[4] == "/":
        season = season[:5] + season[7:]
    if now in (None, -1):  # no current-season row: we can't confirm where he is now
        return f"{t} ({season})"
    return f"{names.get(now, '?')} ({season}: {t})"


def league_players(season_id):
    rows, page = [], 1
    while True:
        d = _get("league-players", season_id=season_id, page=page)
        rows += d.get("data", [])
        if page >= d.get("pager", {}).get("max_page", 1):
            break
        page += 1
    df = pd.DataFrame(rows).drop_duplicates("id")
    df["position_group"] = df["position"].map(position_group)
    df["name"] = df["known_as"].where(df["known_as"].astype(str).str.len() > 0, df["full_name"])
    return _fix_names(df[df["name"].astype(str).str.len() > 0].copy())


def player_season(player_id, season_id):
    """The player's row for one competition-season, with 'detailed' flattened.
    FootyStats repeats identical rows 2-3x — dedupe by taking the first."""
    d = _get("player-stats", player_id=player_id)
    for r in d.get("data", []):
        if r.get("competition_id") == season_id:
            flat = {k: v for k, v in r.items() if k != "detailed"}
            flat.update(r.get("detailed") or {})
            return flat
    return None


def player_history(player_id):
    d = _get("player-stats", player_id=player_id)
    seen, out = set(), []
    for r in d.get("data", []):
        k = (r.get("competition_id"), r.get("minutes_played_overall"))
        if k in seen:
            continue
        seen.add(k)
        out.append({"season": r.get("season"), "competition": r.get("league"),
                    "competition_id": r.get("competition_id"),
                    "apps": r.get("appearances_overall"), "mins": r.get("minutes_played_overall"),
                    "goals": r.get("goals_overall"), "assists": r.get("assists_overall")})
    return pd.DataFrame(out)


# Metric sets per position: (field, label, lower_is_better)
METRICS = {
    "Forward": [
        ("goals_per_90_overall", "Goals", False), ("npxg_per_90_overall", "npxG", False),
        ("shots_per_90_overall", "Shots", False), ("shot_accuraccy_percentage_overall", "Shot\naccuracy %", False),
        ("shot_conversion_rate_overall", "Conversion %", False), ("xa_per_90_overall", "xA", False),
        ("key_passes_per_90_overall", "Key passes", False), ("dribbles_successful_per_90_overall", "Dribbles\ncompleted", False),
        ("duels_won_percentage_overall", "Duels won %", False), ("aerial_duels_won_per_90_overall", "Aerials won", False),
    ],
    "Midfielder": [
        ("npxg_per_90_overall", "npxG", False), ("xa_per_90_overall", "xA", False),
        ("key_passes_per_90_overall", "Key passes", False), ("passes_per_90_overall", "Passes", False),
        ("pass_completion_rate_overall", "Pass\ncompletion %", False), ("dribbles_successful_per_90_overall", "Dribbles\ncompleted", False),
        ("tackles_per_90_overall", "Tackles", False), ("interceptions_per_90_overall", "Interceptions", False),
        ("duels_won_percentage_overall", "Duels won %", False), ("dispossesed_per_90_overall", "Dispossessed", True),
    ],
    "Defender": [
        ("tackles_per_90_overall", "Tackles", False), ("interceptions_per_90_overall", "Interceptions", False),
        ("clearances_per_90_overall", "Clearances", False), ("blocks_per_90_overall", "Blocks", False),
        ("aerial_duels_won_per_90_overall", "Aerials won", False), ("duels_won_percentage_overall", "Duels won %", False),
        ("passes_per_90_overall", "Passes", False), ("pass_completion_rate_overall", "Pass\ncompletion %", False),
        ("accurate_crosses_per_90_overall", "Accurate\ncrosses", False), ("dribbled_past_per_90_overall", "Dribbled past", True),
    ],
    "Goalkeeper": [
        ("save_percentage_overall", "Save %", False), ("saves_per_90_overall", "Saves", False),
        ("conceded_per_90_overall", "Conceded", True), ("clean_sheets_per_overall", "Clean\nsheet %", False),
        ("punches_per_90_overall", "Punches", False), ("pass_completion_rate_overall", "Pass\ncompletion %", False),
        ("passes_per_90_overall", "Passes", False), ("average_rating_overall", "Match\nrating", False),
    ],
}


def cohort(season_id, position, min_minutes=900):
    """All players at `position` with >= min_minutes in the season, with detailed stats.
    Costs one request per player on first run (cached after)."""
    lp = league_players(season_id)
    lp = lp[(lp["position_group"] == position) & (lp["minutes_played_overall"] >= min_minutes)]
    rows = []
    for pid in lp["id"]:
        r = player_season(int(pid), season_id)
        if r and (r.get("detailed_minutes_played_recorded_overall") or 0) > 0:
            rows.append(r)
    df = pd.DataFrame(rows)
    if not len(df):
        return df
    df["position_detail"] = df.get("position")  # FootyStats' own label, e.g. 'Centre Back'
    df["position"] = position                   # the group, so downstream code sees one label
    df["name"] = df["known_as"].where(df["known_as"].astype(str).str.len() > 0, df["full_name"])
    return _fix_names(df)


def percentiles(df, position):
    """Our own percentiles, computed against the named cohort (not FootyStats' opaque ones)."""
    out = df[["id", "name", "club_team_id", "age", "nationality", "minutes_played_overall"]].copy()
    for field, label, low in METRICS[position]:
        s = pd.to_numeric(df.get(field), errors="coerce")
        s = s.where(s >= 0)  # FootyStats uses -1 for "no data"
        pct = s.rank(pct=True, ascending=not low) * 100
        out[field] = s
        out[field + "_pct"] = pct.round().astype("Int64")
    return out


def sanity_warnings(row):
    w = []
    xg, xa = row.get("xg_total_overall"), row.get("xa_total_overall")
    if xg not in (None, 0) and xg == xa:
        w.append(f"xG and xA are identical ({xg}) — likely a FootyStats data error; don't quote xA.")
    if (row.get("detailed_minutes_played_recorded_overall") or 0) < (row.get("minutes_played_overall") or 0) * 0.8:
        w.append("Detailed stats cover <80% of minutes played — per-90s are on a partial sample.")
    return w


def _fonts():
    from matplotlib import font_manager as fm
    here = os.path.dirname(os.path.abspath(__file__))
    fams = {}
    for f, key in [("PlayfairDisplay%5Bwght%5D.ttf", "head"), ("Lato-Regular.ttf", "body"), ("Lato-Bold.ttf", "bold")]:
        p = os.path.join(here, f)
        if not os.path.exists(p):
            url = {"head": "playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf", "body": "lato/Lato-Regular.ttf",
                   "bold": "lato/Lato-Bold.ttf"}[key]
            open(p, "wb").write(requests.get("https://raw.githubusercontent.com/google/fonts/main/ofl/" + url).content)
        fm.fontManager.addfont(p)
        fams[key] = fm.FontProperties(fname=p)
    return fams


def ordinal(n):
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


BANDS = [(80, GREEN, "Top 20% (80th+)"), (50, "#5b8a6f", "Above average (50th-79th)"), (0, "#b9cfc2", "Below average (under 50th)")]


def band_colour(pct):
    for lo, col, _ in BANDS:
        if pct >= lo:
            return col
    return BANDS[-1][1]


def pizza(prow, position, title, subtitle, footnote, out_png, cohort_desc=""):
    """Percentile pizza in Football Siam house style. prow = one row of percentiles().
    Reads clearly as percentiles: ordinal boxes (38th), colour bands, a key, and the
    real value printed under every label. cohort_desc e.g. '52 Thai League 1 forwards (900+ mins)'."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from mplsoccer import PyPizza
    F = _fonts()
    mets = METRICS[position]

    def _raw(field, label):
        v = prow.get(field)
        if v is None or pd.isna(v):
            return "no data"
        return f"{v:.0f}%" if "%" in label else f"{v:.2f} per 90"
    params = [f"{m[1].replace(chr(10), ' ').replace(' %', '')}\n{_raw(m[0], m[1])}" for m in mets]
    vals = [int(prow[m[0] + "_pct"]) if pd.notna(prow[m[0] + "_pct"]) else 0 for m in mets]
    cols = [band_colour(v) for v in vals]
    baker = PyPizza(params=params, background_color=PAPER, straight_line_color="#dddddd",
                    straight_line_lw=1, last_circle_color="#bbbbbb", last_circle_lw=1.2,
                    other_circle_color="#e2e2e2", other_circle_lw=0.8, other_circle_ls="--",
                    inner_circle_size=16)
    fig, ax = baker.make_pizza(
        vals, figsize=(9, 11), color_blank_space="same", blank_alpha=0.10, param_location=113,
        slice_colors=cols, value_colors=[INK] * len(vals), value_bck_colors=[GOLD] * len(vals),
        kwargs_slices=dict(edgecolor=PAPER, zorder=1, linewidth=1.5),
        kwargs_params=dict(color=INK, fontsize=10.5, fontproperties=F["bold"], va="center"),
        kwargs_values=dict(color=INK, fontsize=11, fontproperties=F["bold"], zorder=3,
                           bbox=dict(edgecolor=GREEN, facecolor=GOLD, boxstyle="round,pad=0.25", lw=1)),
    )
    for t, v in zip(baker.get_value_texts(), vals):
        t.set_text(ordinal(v))
    fig.subplots_adjust(top=0.735, bottom=0.145)
    H = fig.get_figheight()
    # header
    fig.patches.append(Rectangle((0, 0.895), 1, 0.105, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.05, 0.952, title.upper(), fontproperties=F["head"], fontsize=24, color=PAPER, va="center", weight="bold")
    fig.text(0.05, 0.913, subtitle, fontproperties=F["body"], fontsize=11.5, color=GOLD, va="center")
    # key
    fig.text(0.05, 0.868, "PERCENTILE RANKS" + (f"  \u00b7  compared with {cohort_desc}" if cohort_desc else ""),
             fontproperties=F["bold"], fontsize=11, color=GREEN, va="center")
    fig.text(0.05, 0.843, "80th means better than 80% of them. The figure under each label is his actual number.",
             fontproperties=F["body"], fontsize=10, color="#333333", va="center")
    x = 0.05
    for _, col, lab in BANDS:
        fig.patches.append(Rectangle((x, 0.811), 0.022, 0.014, transform=fig.transFigure, color=col))
        fig.text(x + 0.03, 0.818, lab, fontproperties=F["body"], fontsize=9.5, color=INK, va="center")
        x += 0.03 + len(lab) * 0.0105 + 0.03
    # footer
    fig.patches.append(Rectangle((0, 0), 1, 0.042, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.05, 0.021, "FOOTBALLSIAM.COM", fontproperties=F["bold"], fontsize=11, color=GOLD, va="center")
    fig.text(0.95, 0.021, "Data: FootyStats", fontproperties=F["body"], fontsize=10, color=PAPER, va="center", ha="right")
    fig.text(0.5, 0.068, footnote, fontproperties=F["body"], fontsize=9.5, color="#444444", ha="center", va="center", wrap=True)
    fig.savefig(out_png, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return out_png


def all_positions(season_id, min_minutes=900):
    """Cohorts for every outfield position + GK, concatenated, with a 'position' column."""
    frames = []
    for pos in METRICS:
        c = cohort(season_id, pos, min_minutes)
        if len(c):
            frames.append(c)
    return pd.concat(frames, ignore_index=True).copy()


def leaderboard(df, field, n=10, ascending=False):
    s = pd.to_numeric(df[field], errors="coerce")
    d = df.copy().assign(_v=s).dropna(subset=["_v"]).query("_v >= 0")
    return d.sort_values("_v", ascending=ascending).head(n)


def bar_chart(names, values, title, subtitle, footnote, out_png, fmt="{:.2f}", highlight=None, ranked=True,
              baseline=0, ref=None, ref_label=""):
    """baseline>0 starts the axis above zero (for ratings); the axis is then drawn with ticks so it is visible."""
    """Horizontal ranked bar chart in house style. highlight = set of names to colour gold."""
    import matplotlib.pyplot as plt
    F = _fonts()
    n = len(names)
    fig, ax = plt.subplots(figsize=(9, 1.6 + 0.55 * n))
    fig.subplots_adjust(top=1 - 1.15 / (1.6 + 0.55 * n), bottom=0.9 / (1.6 + 0.55 * n), left=0.42, right=0.92)
    y = list(range(n))[::-1]
    cols = [GOLD if highlight and nm in highlight else GREEN for nm in names]
    ax.barh(y, [v - baseline for v in values], left=baseline, color=cols, height=0.62)
    ax.set_yticks(y, [""] * n)
    ax.tick_params(left=False)
    for yi, nm in zip(y, names):  # "Name\nClub" -> bold name, lighter club line
        top, _, sub = nm.partition("\n")
        ax.text(-0.01, yi + (0.13 if sub else 0), top, transform=ax.get_yaxis_transform(), ha="right", va="center",
                fontproperties=F["bold"], fontsize=11, color=INK)
        if sub:
            ax.text(-0.01, yi - 0.2, sub, transform=ax.get_yaxis_transform(), ha="right", va="center",
                    fontproperties=F["body"], fontsize=9, color="#555555")
    for i, (yi, v) in enumerate(zip(y, values)):
        ax.text(v, yi, "  " + fmt.format(v), va="center", fontproperties=F["bold"], fontsize=10, color=INK)
        if ranked:
            ax.text(baseline + (max(values) - baseline) * 0.012, yi, str(i + 1), va="center", ha="left", fontproperties=F["bold"],
                    fontsize=11, color=GOLD if cols[i] == GREEN else GREEN)
    for s in ["top", "right", "bottom"]:
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color("#cccccc")
    if baseline:
        fig.subplots_adjust(bottom=1.3 / fig.get_figheight())
        ax.set_xlim(baseline, max(values) + (max(values) - baseline) * 0.15)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#cccccc")
        ax.tick_params(axis="x", colors="#555555", labelsize=9)
    else:
        ax.set_xticks([])
        ax.set_xlim(0, max(values) * 1.15)
    if ref is not None:
        ax.axvline(ref, color=GOLD, lw=1.5, ls="--", zorder=3)
        ax.text(ref, n - 0.45, ref_label, ha="center", va="bottom", fontproperties=F["bold"], fontsize=9, color="#8a6d1a")
    H = fig.get_figheight()
    fig.patches.append(plt.Rectangle((0, 1 - 0.95 / H), 1, 0.95 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 1 - 0.38 / H, title.upper(), fontproperties=F["head"], fontsize=20, color=PAPER, va="center", weight="bold")
    fig.text(0.04, 1 - 0.72 / H, subtitle, fontproperties=F["body"], fontsize=11, color=GOLD, va="center")
    fig.patches.append(plt.Rectangle((0, 0), 1, 0.4 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 0.2 / H, "FOOTBALLSIAM.COM", fontproperties=F["bold"], fontsize=10, color=GOLD, va="center")
    fig.text(0.96, 0.2 / H, "Data: FootyStats", fontproperties=F["body"], fontsize=9, color=PAPER, va="center", ha="right")
    fig.text(0.04, 0.62 / H, footnote, fontproperties=F["body"], fontsize=9, color="#555555", va="center")
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    widest = max(t.get_window_extent(r).width for t in ax.texts if t.get_position()[0] == -0.01)
    fig.subplots_adjust(left=min(0.6, (widest + 40) / fig.bbox.width + 0.02))
    fig.savefig(out_png, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return out_png



def dot_chart(names, values, title, subtitle, footnote, out_png, xlim, ref=None, ref_label="",
              notes=None, fmt="{:.2f}"):
    """Ranked lollipop chart for metrics where a zero baseline hides the story (e.g. match ratings).
    xlim=(lo, hi) must be stated honestly in the subtitle/axis. ref = dashed reference line (e.g. league average).
    notes = optional right-hand text per row (e.g. '3 goals · 236 mins')."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    F = _fonts()
    n = len(names)
    H = 1.9 + 0.62 * n
    fig, ax = plt.subplots(figsize=(9, H))
    fig.subplots_adjust(top=1 - 1.25 / H, bottom=1.05 / H, left=0.36, right=0.76 if notes else 0.92)
    y = list(range(n))[::-1]
    lo, hi = xlim
    for i, (yi, v) in enumerate(zip(y, values)):
        ax.plot([lo, v], [yi, yi], color="#cfd8d3", lw=3, zorder=1, solid_capstyle="round")
        ax.scatter([v], [yi], s=520, color=GREEN, zorder=3, edgecolor=GOLD, linewidth=2)
        ax.text(v, yi, fmt.format(v), ha="center", va="center", fontproperties=F["bold"], fontsize=8.5, color=PAPER, zorder=4)
        ax.text(lo + (hi - lo) * 0.004, yi + 0.33, str(i + 1), ha="left", va="center", fontproperties=F["bold"], fontsize=9.5, color=GREEN)
        top, _, sub = names[i].partition("\n")
        ax.text(-0.02, yi + (0.13 if sub else 0), top, transform=ax.get_yaxis_transform(), ha="right", va="center",
                fontproperties=F["bold"], fontsize=11, color=INK)
        if sub:
            ax.text(-0.02, yi - 0.2, sub, transform=ax.get_yaxis_transform(), ha="right", va="center",
                    fontproperties=F["body"], fontsize=9, color="#555555")
        if notes:
            ax.text(1.03, yi, notes[i], transform=ax.get_yaxis_transform(), ha="left", va="center",
                    fontproperties=F["body"], fontsize=9.5, color=INK)
    if ref is not None:
        ax.axvline(ref, color=GOLD, lw=1.5, ls="--", zorder=0)
        ax.text(ref, n - 0.35, ref_label, ha="center", va="bottom", fontproperties=F["bold"], fontsize=9, color="#8a6d1a")
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.7, n - 0.1)
    ax.set_yticks([])
    for sp in ["top", "right", "left"]:
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(axis="x", colors="#555555", labelsize=9)
    for lab in ax.get_xticklabels():
        lab.set_fontproperties(F["body"])
    fig.patches.append(Rectangle((0, 1 - 1.0 / H), 1, 1.0 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 1 - 0.4 / H, title.upper(), fontproperties=F["head"], fontsize=20, color=PAPER, va="center", weight="bold")
    fig.text(0.04, 1 - 0.76 / H, subtitle, fontproperties=F["body"], fontsize=11, color=GOLD, va="center")
    fig.patches.append(Rectangle((0, 0), 1, 0.4 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 0.2 / H, "FOOTBALLSIAM.COM", fontproperties=F["bold"], fontsize=10, color=GOLD, va="center")
    fig.text(0.96, 0.2 / H, "Data: FootyStats", fontproperties=F["body"], fontsize=9, color=PAPER, va="center", ha="right")
    fig.text(0.04, 0.66 / H, footnote, fontproperties=F["body"], fontsize=9, color="#555555", va="center")
    fig.savefig(out_png, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return out_png


# ---------------------------------------------------------------- league bump chart
def league_positions(season_id):
    """League position of every team after each completed matchweek.
    Sort = points, goal difference, goals for (matches footballsiam.com's table).
    Returns (positions DataFrame index=team name, columns=matchweek), last complete MW."""
    d = _get_fresh("league-matches", season_id=season_id)
    names = team_names(season_id)
    rows = [m for m in d.get("data", []) if m.get("status") == "complete"]
    if not rows:
        return pd.DataFrame(), 0
    weeks = sorted({m["game_week"] for m in rows})
    # only matchweeks where every match is complete
    allm = d.get("data", [])
    full = [w for w in weeks if all(m.get("status") == "complete" for m in allm if m["game_week"] == w)]
    teams = {m["homeID"] for m in allm} | {m["awayID"] for m in allm}
    stats = {t: dict(pts=0, gd=0, gf=0) for t in teams}
    out = {}
    for w in full:
        for m in [x for x in rows if x["game_week"] == w]:
            h, a, hg, ag = m["homeID"], m["awayID"], m["homeGoalCount"], m["awayGoalCount"]
            stats[h]["gf"] += hg; stats[a]["gf"] += ag
            stats[h]["gd"] += hg - ag; stats[a]["gd"] += ag - hg
            if hg > ag: stats[h]["pts"] += 3
            elif ag > hg: stats[a]["pts"] += 3
            else: stats[h]["pts"] += 1; stats[a]["pts"] += 1
        order = sorted(teams, key=lambda t: (-stats[t]["pts"], -stats[t]["gd"], -stats[t]["gf"], names.get(t, "")))
        out[w] = {names.get(t, str(t)): i + 1 for i, t in enumerate(order)}
    pos = pd.DataFrame(out)
    return pos, (full[-1] if full else 0)


def _get_fresh(endpoint, **params):
    """Like _get but always refetches (results change week to week)."""
    h = hashlib.md5(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
    path = os.path.join(CACHE, f"{endpoint}_{h}.json")
    if os.path.exists(path):
        os.remove(path)
    return _get(endpoint, **params)


def bump_chart(pos, title, subtitle, footnote, out_png, highlight=None):
    """Bump chart: league position (1 at top) by matchweek. `highlight` = team names drawn
    in colour (default: current top 3 + the week's biggest riser and faller); the rest grey."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    F = _fonts()
    weeks = list(pos.columns)
    n = len(pos)
    last = weeks[-1]
    if highlight is None:
        highlight = list(pos[last].sort_values().index[:3])
        if len(weeks) > 1:
            delta = pos[weeks[-2]] - pos[last]
            highlight += [delta.idxmax(), delta.idxmin()]
    highlight = list(dict.fromkeys(highlight))
    palette = [GREEN, "#c9a227", "#2f7d55", "#8a1c1c", "#1f4e79", "#7a4f9a"]
    col = {t: palette[i % len(palette)] for i, t in enumerate(highlight)}
    H = 12.5
    fig, ax = plt.subplots(figsize=(10.8, H))
    fig.subplots_adjust(top=1 - 1.35 / H, bottom=1.05 / H, left=0.2, right=0.72)
    for t in pos.index:
        ys = pos.loc[t, weeks].tolist()
        hl = t in col
        ax.plot(weeks, ys, color=col.get(t, "#d3d9d6"), lw=3.2 if hl else 1.8, zorder=3 if hl else 1,
                marker="o", ms=9 if hl else 6, mfc=col.get(t, "#d3d9d6"), mec=PAPER, mew=1.5)
        ax.text(weeks[-1] + 0.12 * max(1, len(weeks) - 1), ys[-1], f"{int(ys[-1])}  {t}", va="center", ha="left",
                fontproperties=F["bold"] if hl else F["body"], fontsize=11 if hl else 10,
                color=col.get(t, "#777777"))
        ax.text(weeks[0] - 0.12 * max(1, len(weeks) - 1), ys[0], f"{t}  {int(ys[0])}", va="center", ha="right",
                fontproperties=F["body"], fontsize=9, color="#888888")
    ax.set_ylim(n + 0.6, 0.4)
    ax.set_xlim(weeks[0] - 0.05, weeks[-1] + 0.05)
    ax.set_xticks(weeks, [f"MW{w}" for w in weeks])
    for lab in ax.get_xticklabels():
        lab.set_fontproperties(F["bold"]); lab.set_fontsize(10); lab.set_color("#555555")
    ax.set_yticks([])
    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#cccccc")
    for w in weeks:
        ax.axvline(w, color="#eeeeee", lw=1, zorder=0)
    fig.patches.append(Rectangle((0, 1 - 1.05 / H), 1, 1.05 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 1 - 0.42 / H, title.upper(), fontproperties=F["head"], fontsize=22, color=PAPER, va="center", weight="bold")
    fig.text(0.04, 1 - 0.8 / H, subtitle, fontproperties=F["body"], fontsize=11.5, color=GOLD, va="center")
    fig.patches.append(Rectangle((0, 0), 1, 0.42 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 0.21 / H, "FOOTBALLSIAM.COM", fontproperties=F["bold"], fontsize=10, color=GOLD, va="center")
    fig.text(0.96, 0.21 / H, "Data: FootyStats", fontproperties=F["body"], fontsize=9, color=PAPER, va="center", ha="right")
    fig.text(0.04, 0.68 / H, footnote, fontproperties=F["body"], fontsize=9, color="#555555", va="center")
    fig.savefig(out_png, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return out_png


# ---------------------------------------------------------------- comet chart
def comet_chart(names, before, after, title, subtitle, footnote, out_png, before_label="2025/26",
                after_label="2026/27", fmt="{:.2f}", xlim=None):
    """One row per player: a comet from last season's value (small grey dot) to this season's
    (big dot; green if up, dark red if down). Rows with before=None show 'new' (no comparable season)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    F = _fonts()
    n = len(names)
    H = 1.9 + 0.62 * n
    fig, ax = plt.subplots(figsize=(9, H))
    fig.subplots_adjust(top=1 - 1.25 / H, bottom=1.3 / H, left=0.4, right=0.9)
    y = list(range(n))[::-1]
    vals = [v for v in list(before) + list(after) if v is not None and not pd.isna(v)]
    lo, hi = xlim or (min(vals) - 0.25, max(vals) + 0.25)
    for yi, nm, b, a in zip(y, names, before, after):
        top, _, sub = nm.partition("\n")
        ax.text(-0.02, yi + (0.13 if sub else 0), top, transform=ax.get_yaxis_transform(), ha="right", va="center",
                fontproperties=F["bold"], fontsize=11, color=INK)
        if sub:
            ax.text(-0.02, yi - 0.2, sub, transform=ax.get_yaxis_transform(), ha="right", va="center",
                    fontproperties=F["body"], fontsize=9, color="#555555")
        if b is None or pd.isna(b):
            ax.scatter([a], [yi], s=170, color=GREEN, zorder=3, edgecolor=GOLD, linewidth=1.5)
            ax.text(a + (hi - lo) * 0.03, yi, f"{fmt.format(a)}  (new)", ha="left", va="center", fontproperties=F["bold"], fontsize=9.5, color=GREEN)
            continue
        up = a >= b
        c = GREEN if up else "#8a1c1c"
        ax.annotate("", xy=(a, yi), xytext=(b, yi),
                    arrowprops=dict(arrowstyle="-|>", color=c, lw=2.2, shrinkA=4, shrinkB=9, mutation_scale=14))
        ax.scatter([b], [yi], s=45, color="#b5bdb9", zorder=3)
        ax.scatter([a], [yi], s=170, color=c, zorder=4, edgecolor=GOLD if up else PAPER, linewidth=1.5)
        pad = (hi - lo) * 0.022
        # labels sit outside the comet: old value on its far side, new value beyond the head
        ax.text(b - pad if up else b + pad, yi, fmt.format(b), ha="right" if up else "left", va="center",
                fontproperties=F["body"], fontsize=8.5, color="#777777")
        ax.text(a + pad * 1.4 if up else a - pad * 1.4, yi, fmt.format(a), ha="left" if up else "right", va="center",
                fontproperties=F["bold"], fontsize=9.5, color=c)
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.7, n - 0.2)
    ax.set_yticks([])
    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(axis="x", colors="#555555", labelsize=9)
    # legend
    fig.text(0.40, 0.95 / H, f"● {before_label}", fontproperties=F["body"], fontsize=9.5, color="#8f9894", va="center")
    fig.text(0.53, 0.95 / H, f"● {after_label} (up)", fontproperties=F["bold"], fontsize=9.5, color=GREEN, va="center")
    fig.text(0.70, 0.95 / H, f"● {after_label} (down)", fontproperties=F["bold"], fontsize=9.5, color="#8a1c1c", va="center")
    fig.patches.append(Rectangle((0, 1 - 1.0 / H), 1, 1.0 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 1 - 0.4 / H, title.upper(), fontproperties=F["head"], fontsize=20, color=PAPER, va="center", weight="bold")
    fig.text(0.04, 1 - 0.76 / H, subtitle, fontproperties=F["body"], fontsize=11, color=GOLD, va="center")
    fig.patches.append(Rectangle((0, 0), 1, 0.4 / H, transform=fig.transFigure, color=GREEN, zorder=-1))
    fig.text(0.04, 0.2 / H, "FOOTBALLSIAM.COM", fontproperties=F["bold"], fontsize=10, color=GOLD, va="center")
    fig.text(0.96, 0.2 / H, "Data: FootyStats", fontproperties=F["body"], fontsize=9, color=PAPER, va="center", ha="right")
    fig.text(0.04, 0.62 / H, footnote, fontproperties=F["body"], fontsize=9, color="#555555", va="center")
    fig.savefig(out_png, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return out_png
