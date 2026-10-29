#!/usr/bin/env python3
"""
FOOTBALL SIAM - SOCIAL CAROUSEL CARDS (v3, Instagram-first, 17 Oct 2026)

Renders fixtures, results and league tables as 1080x1350 (4:5) JPEG slides
for a carousel post. Simon, 17 Oct 2026: the old 1080-wide cards were as
tall as their content, so a short results card came out landscape and the
Instagram grid cut both ends off. Every slide is now 4:5, built for
Instagram, and the same slides go to X, LinkedIn and LINE.

Layout rules come from src/fs_social.py (the shared toolkit): 80px side
margins, at least 34px for team names and numbers, a gold FOOTBALLSIAM.COM
band at the foot, the gold elephant on every slide, and a hard stop (not a
silent overflow) when something does not fit. Every slide is written with a
*_grid.png preview beside it: the 3:4 slice the profile grid shows, at 360px
wide. Look at those before posting.

Slides:
  * cover: the slide the grid shows. Kicker, big scoreline (results),
    headline, sub-line. No table.
  * fixtures/results: at most 8 matches a slide, home right-aligned, score or
    kick-off in a green box, away left-aligned, a header per date (and per
    group on continental cards).
  * table: 8 rows a slide (9 when that saves a slide, e.g. 18 teams), with
    Pos, Club, P, GD and Pts only. The full table stays on the website.

Data comes from the same JSON as before (fetched by the calling session, this
script makes no network calls): fs/v1/social-fixtures-data,
fs/v1/social-table-data and fs/v1/matchday-card-data.

USAGE
    # The weekly carousel: cover, fixtures or results, then the table.
    python3 src/social_carousel_cards.py carousel \
        --fixtures-file fixtures.json --table-file table.json \
        --out-dir outputs --prefix mw5_sat [--final]

    # Results card for a completed day (Matchday Watcher).
    python3 src/social_carousel_cards.py matchday \
        --data-file card.json --out-dir outputs --prefix thai-league-1_20261017

    # Pieces on their own (no cover):
    python3 src/social_carousel_cards.py fixtures --data-file fixtures.json --out-dir outputs --prefix fx
    python3 src/social_carousel_cards.py table --data-file table.json --out-dir outputs --prefix tb

Cover text is written from the data; override any of it with --kicker,
--headline, --sub and --big. --final marks a results carousel as the whole
round (\"Matchweek 5 results\" rather than \"results so far\"); matchday mode
reads that from round_complete.

Every command prints the slide paths in carousel order and writes them to
<prefix>_order.txt. X takes four images at most: send slides 1 to 4 there.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fs_social as fs  # noqa: E402
from fs_social import MARGIN_X, CONTENT_R, CONTENT_W, W, FOOTER_Y  # noqa: E402

LEAGUE_LABELS = {
    "thai-league-1": "Thai League 1",
    "thai-league-2": "Thai League 2",
    "asean-club-championship": "ASEAN Club Championship",
    "afc-champions-league-elite": "AFC Champions League Elite",
    "afc-champions-league-two": "AFC Champions League Two",
}

MAX_MATCHES = 8
ROW_H = 96
DATE_H = 58
BADGE_W, BADGE_H = 150, 64
NAME_GAP = 24
NAME_W = W / 2 - BADGE_W / 2 - NAME_GAP - MARGIN_X   # 361px each side


# ---- small helpers -------------------------------------------------------------
def round_name(label: str | None, mw=None) -> str:
    """'Match Week 5' -> 'Matchweek 5'; keeps 'Matchday 2', 'Quarter-final'."""
    if label:
        return re.sub(r"(?i)match\s*week", "Matchweek", str(label)).strip()
    return f"Matchweek {mw}" if mw not in (None, "") else ""


def short_date(s: str) -> str:
    """'Friday 16 October' -> 'Fri 16 Oct'."""
    parts = str(s or "").split()
    if len(parts) == 3:
        return f"{parts[0][:3]} {parts[1]} {parts[2][:3]}"
    return str(s or "")


WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
FULL_DAY = {d[:3]: d for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def num(n: int, cap=False) -> str:
    """House style: one to nine in words, 10 and over in figures."""
    w = WORDS[n] if 0 <= n < 10 else str(n)
    return w[:1].upper() + w[1:] if cap else w


def centre_text(m: dict) -> str:
    if m.get("is_scored"):
        return f"{m.get('home_score')}-{m.get('away_score')}"
    if m.get("status") == "postponed":
        return "P-P"
    return m.get("time") or "TBC"


def name_lines(card, name, font):
    if card.width(name, font) <= NAME_W:
        return [name]
    lines = fs.wrap(card, name, font, NAME_W)
    if len(lines) > 2:
        raise fs.LayoutError(f"club name {name!r} needs more than two lines")
    return lines


# ---- slide builders --------------------------------------------------------------
def _match_slides(matches, kicker, title, sub_for_page):
    """Split matches into pages that fit the body, at most 8 a page."""
    show_groups = len({m.get("group") for m in matches if m.get("group")}) > 1
    key = (lambda m: (m.get("date"), m.get("group"))) if show_groups else (lambda m: m.get("date"))
    probe = fs.Card()
    body_top = fs.data_header(probe, kicker, title, sub_for_page(1, 1))
    budget = FOOTER_Y - 20 - body_top
    pages, cur, used, last = [], [], 0, object()
    for m in matches:
        need = ROW_H + (DATE_H if key(m) != last else 0)
        if cur and (len(cur) >= MAX_MATCHES or used + need > budget):
            pages.append(cur)
            cur, used, last = [], 0, object()
            need = ROW_H + DATE_H
        cur.append(m)
        used += need
        last = key(m)
    if cur or not pages:
        pages.append(cur)
    return pages, show_groups


def draw_matches(card, matches, body_top, show_groups):
    f_name = fs.lato_bold(fs.MIN_DATA)
    f_badge = fs.lato_bold(36)
    f_date = fs.lato_bold(30)
    y = body_top + 14
    last, i = object(), 0
    if not matches:
        card.text((W / 2, body_top + 200), "No games to show.", fs.lato(fs.MIN_DATA), fs.MUTED, anchor="mm")
        return
    for m in matches:
        k = (m.get("date"), m.get("group")) if show_groups else m.get("date")
        if k != last:
            label = short_date(m.get("date"))
            if show_groups and m.get("group"):
                label = f"{m['group']} · {label}"
            card.text((MARGIN_X, y + DATE_H / 2 + 4), label.upper(), f_date, fs.GREEN, anchor="lm")
            card.rect([MARGIN_X, y + DATE_H - 6, CONTENT_R, y + DATE_H - 4], fs.GOLD)
            y += DATE_H
            last = k
        if i % 2:
            card.rect([0, y, W, y + ROW_H], fs.ROW_ALT)
        mid = y + ROW_H / 2
        cx = W / 2
        for side, name in (("home", m.get("home", "")), ("away", m.get("away", ""))):
            lines = name_lines(card, name, f_name)
            lh = 38
            y0 = mid - lh * (len(lines) - 1) / 2
            for j, ln in enumerate(lines):
                if side == "home":
                    card.text((cx - BADGE_W / 2 - NAME_GAP, y0 + j * lh), ln, f_name, fs.INK, anchor="rm")
                else:
                    card.text((cx + BADGE_W / 2 + NAME_GAP, y0 + j * lh), ln, f_name, fs.INK, anchor="lm")
        card.rrect([cx - BADGE_W / 2, mid - BADGE_H / 2, cx + BADGE_W / 2, mid + BADGE_H / 2], fs.GREEN, r=10)
        card.text((cx, mid), centre_text(m), f_badge, fs.WHITE, anchor="mm")
        y += ROW_H
        i += 1


def table_pages(standings):
    n = len(standings)
    if n <= 9:
        return [standings]
    per = 9 if n % 9 == 0 and n % 8 != 0 else 8
    return [standings[i:i + per] for i in range(0, n, per)]


def draw_table(card, rows, body_top, qualify=0, note=None, total=0):
    f_head = fs.lato_bold(26)
    f_row = fs.lato_bold(fs.MIN_DATA)
    f_num = fs.lato(fs.MIN_DATA)
    x_pos, x_club = MARGIN_X + 30, MARGIN_X + 82
    cols = [("P", 700), ("GD", 830), ("Pts", CONTENT_R - 30)]
    y = body_top + 16
    hh = 54
    card.text((x_pos, y + hh / 2), "#", f_head, fs.MUTED, anchor="mm")
    card.text((x_club, y + hh / 2), "CLUB", f_head, fs.MUTED, anchor="lm")
    for lbl, x in cols:
        card.text((x, y + hh / 2), lbl.upper(), f_head, fs.MUTED, anchor="mm")
    y += hh
    card.rect([MARGIN_X, y - 2, CONTENT_R, y], fs.HAIRLINE)
    avail = FOOTER_Y - 30 - y - (50 if note else 0)
    rh = min(100, avail // max(len(rows), 1))
    if rh < 80:
        raise fs.LayoutError("too many table rows for one slide")
    for i, t in enumerate(rows):
        pos = int(t.get("pos") or 0)
        if pos == 1:
            card.rect([0, y, W, y + rh], fs.GOLD_TINT)
        elif i % 2:
            card.rect([0, y, W, y + rh], fs.ROW_ALT)
        mid = y + rh / 2
        if pos == 1:
            card.d.ellipse([x_pos - 28, mid - 28, x_pos + 28, mid + 28], fill=fs.GOLD)
        card.text((x_pos, mid), str(pos or i + 1), fs.lato_bold(32), fs.GREEN, anchor="mm")
        name = t.get("name", "")
        nf = f_row if card.width(name, f_row) <= 700 - 50 - x_club else fs.fit_one(card, name, fs.lato_bold, 34, 30, 700 - 50 - x_club)
        card.text((x_club, mid), name, nf, fs.INK, anchor="lm")
        gd = int(t.get("gd") or 0)
        vals = [str(t.get("p", 0)), f"+{gd}" if gd > 0 else str(gd), str(t.get("pts", 0))]
        for (lbl, x), v in zip(cols, vals):
            card.text((x, mid), v, f_row if lbl == "Pts" else f_num, fs.INK, anchor="mm")
        y += rh
        if qualify and pos == qualify and pos < total:
            for xs in range(MARGIN_X, CONTENT_R, 22):
                card.rect([xs, y - 2, min(xs + 12, CONTENT_R), y + 1], fs.GREEN)
    if note:
        card.text((MARGIN_X, y + 32), note, fs.fit_one(card, note, fs.lato, 26, fs.MIN_SMALL, CONTENT_W), fs.MUTED, anchor="lm")


# ---- whole sets ---------------------------------------------------------------------
class Deck:
    """Collects slide builders so page numbers (n/N) can be filled in at the end."""

    def __init__(self):
        self.builders = []

    def add(self, fn):
        self.builders.append(fn)

    def render(self, out_dir, prefix, ext=".jpg"):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        n = len(self.builders)
        paths = []
        for i, fn in enumerate(self.builders, 1):
            card = fs.Card()
            fn(card, f"{i}/{n}" if n > 1 else None)
            paths.append(card.save(out_dir / f"{prefix}_{i:02d}{ext}"))
        (out_dir / f"{prefix}_order.txt").write_text("\n".join(paths) + "\n")
        return paths


def add_cover(deck, kicker, headline, sub=None, big=None):
    deck.add(lambda c, p: fs.cover(c, kicker, headline, sub, big, p))


def add_matches(deck, data, kicker, title, sub=None):
    matches = data.get("matches", [])
    pages, groups = _match_slides(matches, kicker, title, lambda i, n: sub)
    for i, page in enumerate(pages, 1):
        t = title if len(pages) == 1 else f"{title} ({i} of {len(pages)})"

        def build(card, pg, page=page, t=t):
            top = fs.data_header(card, kicker, t, sub)
            draw_matches(card, page, top, groups)
            fs.footer(card, pg)
        deck.add(build)


def add_table(deck, data, kicker, sub=None):
    standings = data.get("standings", [])
    title = data.get("title")
    if not title or title.lower() == "table":
        title = "The table"
    pages = table_pages(standings) or [[]]
    qualify = int(data.get("qualify") or 0)
    for i, rows in enumerate(pages, 1):
        last = i == len(pages)

        def build(card, pg, rows=rows, last=last):
            top = fs.data_header(card, kicker, title, sub)
            draw_table(card, rows, top, qualify, data.get("note") if last else None, len(standings))
            fs.footer(card, pg)
        deck.add(build)


def auto_cover(data, final, label):
    """Cover words written only from the data: round, games, the biggest win."""
    league = data.get("label") or label or LEAGUE_LABELS.get(data.get("league"), "")
    rnd = round_name(data.get("sub") or data.get("round_label"), data.get("mw"))
    matches = data.get("matches", [])
    kicker = league
    if data.get("mode") != "results":
        days = [FULL_DAY.get(short_date(m.get("date")).split()[0], "") for m in matches if m.get("date")]
        span = f"{days[0]} to {days[-1]}" if days and days[0] != days[-1] else (days[0] if days else "")
        n = len(matches)
        return kicker, f"{rnd} fixtures".strip(), f"{num(n, True)} game{'s' if n != 1 else ''}{', ' + span if span else ''}. The table going in is inside.", None
    scored = [m for m in matches if m.get("is_scored")]
    head = f"{rnd} results" if final else f"{rnd} results so far"
    if not scored:
        return kicker, head.strip(), None, None
    best = max(scored, key=lambda m: (abs(int(m["home_score"]) - int(m["away_score"])),
                                     int(m["home_score"]) + int(m["away_score"])))
    big = f"{best['home_score']}-{best['away_score']}"
    lead = f"{best['home']} {big} {best['away']}"
    n = len(scored)
    sub = f"{lead}. {'All ' + num(n) + ' results' if final else num(n, True) + ' played so far'}, and the table."
    return kicker, head.strip(), sub, big


def story_line(m):
    """A short, factual headline for one result. Uses only the score and the
    two names, so it can never claim anything the data does not show."""
    h, a = m["home"], m["away"]
    hs, as_ = int(m["home_score"]), int(m["away_score"])
    win, lose, ws, ls = (h, a, hs, as_) if hs > as_ else (a, h, as_, hs)
    if hs == as_:
        return f"{h} and {a} share {num(hs)} each" if hs >= 2 else f"Honours even at {h}"
    if ws >= 4 and ws - ls >= 3:
        return f"{win} hit {num(ws)}"
    if ws - ls >= 3:
        return f"{win} thrash {lose}" if win == h else f"{win} run riot at {h}"
    if win == a:
        return f"{win} win at {h}"
    return f"{win} beat {lose}"


COVER_SHORT = {
    "AFC Champions League Two": "ACL Two",
    "AFC Champions League Elite": "ACL Elite",
    "ASEAN Club Championship": "ASEAN Club Champ.",
}


def day_cover(data, card_date_label, final, label=None):
    """Matchday cover: built ONLY from the games played on the card's own
    date, so Friday, Saturday and Sunday each get a different headline and
    big scoreline (Simon, 19 Oct 2026). The kicker names the day."""
    league = data.get("label") or label or LEAGUE_LABELS.get(data.get("league"), "")
    rnd = round_name(data.get("sub") or data.get("round_label"), data.get("mw"))
    day_name = (card_date_label or "").split()[0] if card_date_label else ""
    today = [m for m in data.get("matches", []) if m.get("date") == card_date_label and m.get("is_scored")]
    # Cover kickers sit on one 30px line: long continental names overflow
    # (LAYOUT ERROR on ACL Two, 29 Oct 2026), so the cover uses short forms.
    league = COVER_SHORT.get(league, league)
    kicker = " · ".join(x for x in (league, rnd, day_name) if x)
    if not today:
        return kicker, f"{rnd} results" if final else f"{rnd} so far", None, None
    best = max(today, key=lambda m: (abs(int(m["home_score"]) - int(m["away_score"])),
                                    int(m["home_score"]) + int(m["away_score"]), -int(m.get("ts") or 0)))
    big = f"{best['home_score']}-{best['away_score']}"
    head = story_line(best)
    n = len(today)
    when = {"Friday": "Friday night", "Saturday": "Saturday", "Sunday": "Sunday"}.get(day_name, day_name)
    others = n - 1
    lead = f"{best['home']} {big} {best['away']}"
    if final:
        sub = f"{lead}. Every result and the table inside."
    elif others:
        sub = f"{lead}, plus {num(others)} more from {when}. Every result so far, and the table."
    else:
        sub = f"{lead}, {when}'s only game. Every result so far, and the table."
    return kicker, head, sub, big


def build_carousel(fx, tb, final=False, over=None):
    over = over or {}
    deck = Deck()
    kicker, head, sub, big = auto_cover(fx, final, None)
    add_cover(deck, over.get("kicker") or kicker, over.get("headline") or head,
              over.get("sub") or sub, over.get("big") if over.get("big") is not None else big)
    rnd = round_name(fx.get("sub"), fx.get("mw"))
    mode = "Results" if fx.get("mode") == "results" else "Fixtures"
    add_matches(deck, fx, f"{kicker} · {rnd}" if rnd else kicker, mode)
    if tb:
        when = (f"After {rnd}" if final else "As it stands") if fx.get("mode") == "results" else (f"Going into {rnd}" if rnd else None)
        add_table(deck, tb, f"{tb.get('label') or kicker} · {tb.get('season', '')}".strip(" ·"), when)
    return deck


def _is_last_day(data):
    """True when the card's results (the round up to the card's date) hold
    every game of the round: in a single-table league that is half the
    teams. For group stages, trust round_complete."""
    tables = data.get("tables") or []
    res = (data.get("results") or {}).get("matches", [])
    if len(tables) == 1:
        teams = len(tables[0].get("standings") or [])
        if teams:
            done = [m for m in res if m.get("is_scored") or m.get("status") in ("postponed", "cancelled", "abandoned", "suspended")]
            return len(done) >= teams // 2
    return True


def build_matchday(data, over=None):
    over = over or {}
    res = dict(data["results"])
    res.setdefault("label", data.get("label"))
    res.setdefault("mode", "results")
    if data.get("round_label") and not res.get("sub"):
        res["sub"] = data["round_label"]
    # round_complete reflects the round NOW, not on the card's date, so only
    # treat the card as the round's last day when no later date has games.
    final = bool(data.get("round_complete")) and _is_last_day(data)
    deck = Deck()
    kicker, head, sub, big = day_cover(res, data.get("date_label"), final, data.get("label"))
    add_cover(deck, over.get("kicker") or kicker, over.get("headline") or head,
              over.get("sub") or sub, over.get("big") if over.get("big") is not None else big)
    rnd = round_name(res.get("sub") or data.get("round_label"))
    league = res.get("label") or data.get("label") or ""
    add_matches(deck, res, f"{league} · {rnd}" if rnd else league, "Results")
    for t in data.get("tables") or []:
        add_table(deck, t, f"{t.get('label') or league}", "After " + rnd if final else "As it stands")
    return deck


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kind", choices=["carousel", "matchday", "fixtures", "table"])
    p.add_argument("--data-file", help="fixtures, table or matchday JSON")
    p.add_argument("--fixtures-file")
    p.add_argument("--table-file")
    p.add_argument("--out-dir", default="outputs")
    p.add_argument("--prefix", default="card")
    p.add_argument("--out", help="legacy: a path; its folder and name become --out-dir and --prefix")
    p.add_argument("--final", action="store_true", help="results carousel covers the whole round")
    p.add_argument("--png", action="store_true", help="write PNG instead of JPEG (posting needs JPEG)")
    for k in ("kicker", "headline", "sub", "big"):
        p.add_argument(f"--{k}")
    a = p.parse_args(argv)
    if a.out:
        a.out_dir, a.prefix = str(Path(a.out).parent), Path(a.out).stem
    load = lambda f: json.loads(Path(f).read_text(encoding="utf-8"))  # noqa: E731
    over = {k: getattr(a, k) for k in ("kicker", "headline", "sub", "big") if getattr(a, k)}
    try:
        if a.kind == "carousel":
            deck = build_carousel(load(a.fixtures_file), load(a.table_file) if a.table_file else None, a.final, over)
        elif a.kind == "matchday":
            deck = build_matchday(load(a.data_file), over)
        elif a.kind == "fixtures":
            d = load(a.data_file)
            deck = Deck()
            k = d.get("label") or LEAGUE_LABELS.get(d.get("league"), "")
            rnd = round_name(d.get("sub"), d.get("mw"))
            add_matches(deck, d, f"{k} · {rnd}" if rnd else k, "Results" if d.get("mode") == "results" else "Fixtures")
        else:
            d = load(a.data_file)
            deck = Deck()
            add_table(deck, d, f"{d.get('label') or LEAGUE_LABELS.get(d.get('league'), '')} · {d.get('season', '')}".strip(" ·"))
        paths = deck.render(a.out_dir, a.prefix, ".png" if a.png else ".jpg")
    except fs.LayoutError as e:
        print("LAYOUT ERROR:", e, file=sys.stderr)
        return 2
    for path in paths:
        print("Wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
