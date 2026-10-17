#!/usr/bin/env python3
"""
Football Siam weekly chart carousels, Instagram-first (17 Oct 2026).

The old attendance and nationality posts were one wide chart card each
(1470x1293 and 1440x1140 insets in a header and footer), which the Instagram
grid cropped and which shrank the labels to nothing on a phone. They are now
carousels of 1080x1350 slides built with src/fs_social.py:

  Attendance:     cover (biggest crowd as the big figure), the week's crowds
                  as labelled bars, then home crowds for the season so far
                  (eight clubs a slide).
  Nationalities:  cover (Thai share as the big figure), then each
                  nationality's share of the starting places as bars.

The inset scripts make the data and call this module when given
--social-out-dir; it can also run on its own from the JSON they write:

    python3 src/social_charts.py attendance outputs/attendance_mw5.json --out-dir outputs --prefix attendance_mw5
    python3 src/social_charts.py nationality outputs/nationality_mw5.json --out-dir outputs --prefix nationality_mw5

attendance JSON: {"league": "Thai League 1", "mw": 5,
                  "matches": [{"home": ..., "away": ..., "att": 18012}, ...],
                  "season": [{"club": ..., "total": ..., "games": ...}, ...]}
nationality JSON: {"league": "Thai League 1", "mw": 5,
                   "rows": [{"label": "Thailand", "count": 67}, ...]}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fs_social as fs  # noqa: E402
from fs_social import MARGIN_X, CONTENT_R, CONTENT_W, W, FOOTER_Y  # noqa: E402
from social_carousel_cards import Deck, num  # noqa: E402


def draw_bars(card, rows, top, colours):
    """rows: [(label, value_text, value)]; labelled horizontal bars, label
    above the bar, value on the right. Fails rather than squeezing."""
    f_lab = fs.lato_bold(fs.MIN_DATA)
    f_val = fs.lato_bold(36)
    vmax = max((v for _, _, v in rows), default=1) or 1
    avail = FOOTER_Y - 30 - top - 20
    rh = min(118, avail // max(len(rows), 1))
    if rh < 92:
        raise fs.LayoutError(f"{len(rows)} bars do not fit one slide; split them or fold small ones together")
    y = top + 24
    for (label, vtext, v), col in zip(rows, colours):
        vw = card.width(vtext, f_val)
        lf = fs.fit_one(card, label, fs.lato_bold, fs.MIN_DATA, 30, CONTENT_W - vw - 24)
        card.text((MARGIN_X, y + 18), label, lf if lf else f_lab, fs.INK, anchor="lm")
        card.text((CONTENT_R, y + 18), vtext, f_val, fs.GREEN, anchor="rm")
        bw = max(8, round(CONTENT_W * v / vmax))
        card.rrect([MARGIN_X, y + 44, MARGIN_X + bw, y + 70], col, r=6)
        y += rh


def attendance_deck(d):
    league, mw = d.get("league", "Thai League 1"), d.get("mw")
    rnd = f"Matchweek {mw}"
    ms = sorted(d["matches"], key=lambda m: -int(m["att"]))
    total = sum(int(m["att"]) for m in ms)
    top = ms[0]
    deck = Deck()
    sub = (f"{top['home']} v {top['away']} drew the biggest crowd. "
           f"{total:,} watched {num(len(ms))} games.")
    deck.add(lambda c, p: fs.cover(c, league, f"{rnd} attendances", sub, f"{int(top['att']):,}", p))

    def week(card, p):
        t = fs.data_header(card, f"{league} · {rnd}", "Crowds this week")
        rows = [(f"{m['home']} v {m['away']}", f"{int(m['att']):,}", int(m["att"])) for m in ms]
        draw_bars(card, rows, t, [fs.GOLD] + [fs.GREEN] * (len(rows) - 1))
        fs.footer(card, p)
    deck.add(week)

    season = sorted(d.get("season") or [], key=lambda r: -int(r["total"]))
    pages = [season[i:i + 8] for i in range(0, len(season), 8)]
    for i, page in enumerate(pages):
        def build(card, p, page=page, start=i * 8):
            t = fs.data_header(card, f"{league} · after {rnd}", "Home crowds", "Season total and average per game")
            _season_table(card, page, t, start)
            fs.footer(card, p)
        deck.add(build)
    return deck


def _season_table(card, rows, top, start):
    fh, fr, fn = fs.lato_bold(26), fs.lato_bold(fs.MIN_DATA), fs.lato(fs.MIN_DATA)
    xc, xt, xa = MARGIN_X + 10, 760, CONTENT_R - 10
    y = top + 16
    card.text((xc, y + 27), "CLUB", fh, fs.MUTED, anchor="lm")
    card.text((xt, y + 27), "TOTAL", fh, fs.MUTED, anchor="rm")
    card.text((xa, y + 27), "AVERAGE", fh, fs.MUTED, anchor="rm")
    y += 54
    rh = min(100, (FOOTER_Y - 30 - y) // max(len(rows), 1))
    for i, r in enumerate(rows):
        if start + i == 0:
            card.rect([0, y, W, y + rh], fs.GOLD_TINT)
        elif i % 2:
            card.rect([0, y, W, y + rh], fs.ROW_ALT)
        mid = y + rh / 2
        g = int(r.get("games") or 0)
        card.text((xc, mid), r["club"], fr, fs.INK, anchor="lm")
        card.text((xt, mid), f"{int(r['total']):,}", fn, fs.INK, anchor="rm")
        card.text((xa, mid), f"{round(int(r['total']) / g):,}" if g else "-", fr, fs.INK, anchor="rm")
        y += rh


def nationality_deck(d):
    league, mw = d.get("league", "Thai League 1"), d.get("mw")
    rows = d["rows"]
    total = sum(int(r["count"]) for r in rows) or 1
    pct = {r["label"]: round(100 * int(r["count"]) / total) for r in rows}
    thai = pct.get("Thailand", 0)
    named = [r for r in rows if r["label"] != "Thailand" and not r["label"].startswith(("Other", "Unknown"))]
    second = f" {named[0]['label']} next, on {pct[named[0]['label']]}%." if named else ""
    deck = Deck()
    sub = f"Thai players filled {thai}% of the {total} starting places in {league}.{second}"
    deck.add(lambda c, p: fs.cover(c, f"{league} · Matchweek {mw}", "Who started this week", sub, f"{thai}%", p))

    def bars(card, p):
        t = fs.data_header(card, f"{league} · Matchweek {mw}", "Starting XI nationalities",
                           f"Share of {total} starting places")
        cols, out = [], []
        for r in rows:
            lab = "Other" if r["label"].startswith("Other") else r["label"]
            out.append((lab, f"{pct[r['label']]}%  ·  {r['count']}", int(r["count"])))
            cols.append(fs.GOLD if lab == "Thailand" else fs.GREY_BAR if lab in ("Other", "Unknown") else fs.GREEN)
        draw_bars(card, out, t, cols)
        fs.footer(card, p)
    deck.add(bars)
    return deck


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kind", choices=["attendance", "nationality"])
    p.add_argument("data")
    p.add_argument("--out-dir", default="outputs")
    p.add_argument("--prefix")
    a = p.parse_args(argv)
    d = json.loads(Path(a.data).read_text(encoding="utf-8"))
    deck = attendance_deck(d) if a.kind == "attendance" else nationality_deck(d)
    try:
        paths = deck.render(a.out_dir, a.prefix or f"{a.kind}_mw{d.get('mw')}")
    except fs.LayoutError as e:
        print("LAYOUT ERROR:", e, file=sys.stderr)
        return 2
    for path in paths:
        print("Wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
