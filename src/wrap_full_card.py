#!/usr/bin/env python3
"""Wrap an already-rendered chart/table PNG (attendance_chart_inset.py or
nationality_chart_inset.py's output) in the Guardian-style branded card used
across all Football Siam social automations: a dark-green header band (title
left, league/match-week right), the chart PNG itself, and a footer strip.

Deliberately built with PIL/Pillow instead of matplotlib. The inset charts
already went through careful, hard-won matplotlib debugging (see
attendance_chart_inset.py's module docstring for two nasty Agg text-rendering
bugs found and fixed there); compositing a header/footer band as EXTRA
matplotlib Axes around that finished figure risks re-triggering exactly that
class of bug (one of the two confirmed causes was "a rotated label's extent
coming close to another Axes' patch" -- adding more Axes is asking for it
again). PIL just pastes a flat image onto a canvas and draws text with no
coordinate-transform/rotation involved anywhere, so it can't hit that bug
family at all. Keep it this way -- don't fold this back into the matplotlib
scripts.

Usage:
    python wrap_full_card.py --chart-png outputs/attendance_mw3_inset.png \\
        --out outputs/attendance_mw3_full.png \\
        --title "Attendances" \\
        --subtitle1 "Thai League 1" \\
        --subtitle2 "Match week 3   2026/27"

Zero repo-internal imports -- only Pillow (pip install Pillow), stdlib
argparse/pathlib. Runs anywhere, including a fresh cloud sandbox with no
access to the footballsiam-data repo.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DARK_GREEN = (10, 59, 37)
GOLD = (227, 197, 103)
LIGHT_GREEN = (201, 217, 201)
FOOTER_BG = (245, 245, 243)
WHITE = (255, 255, 255)

# Liberation Sans ships on the box that renders these (both the cloud
# sandbox and Simon's machine, confirmed 2026-09-20) -- not a perfect match
# for footballsiam.com's real Lato/Playfair stylesheet fonts (that upgrade
# lives in social_carousel_cards.py's bundled TTFs), but it's a close-enough
# generic bold sans for these two cards and needs no bundled asset files.
_FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES_BOLD:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _right_align_x(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, right_edge: int) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return right_edge - (bbox[2] - bbox[0])


def wrap(chart_png: Path, out_path: Path, title: str, subtitle1: str, subtitle2: str | None) -> None:
    chart = Image.open(chart_png).convert("RGBA")
    width, chart_h = chart.size

    header_h = max(90, round(width * 0.075))
    footer_h = max(45, round(width * 0.035))
    margin = round(width * 0.024)

    canvas = Image.new("RGB", (width, header_h + chart_h + footer_h), WHITE)
    draw = ImageDraw.Draw(canvas)

    # Header band
    draw.rectangle([0, 0, width, header_h], fill=DARK_GREEN)
    title_font = _font(round(header_h * 0.30))
    sub_font = _font(round(header_h * 0.15))
    title_y = header_h / 2 - (10 if subtitle2 else 0)
    draw.text((margin, title_y), title, font=title_font, fill=WHITE, anchor="lm")
    sub1_y = header_h / 2 - (16 if subtitle2 else 0)
    x1 = _right_align_x(draw, subtitle1, sub_font, width - margin)
    draw.text((x1, sub1_y), subtitle1, font=sub_font, fill=GOLD, anchor="lm")
    if subtitle2:
        sub2_font = _font(round(header_h * 0.13))
        x2 = _right_align_x(draw, subtitle2, sub2_font, width - margin)
        draw.text((x2, header_h / 2 + 16), subtitle2, font=sub2_font, fill=LIGHT_GREEN, anchor="lm")

    # Chart, flattened onto white (it's rendered with a transparent
    # background by the inset scripts -- composite it now so the final
    # card is fully opaque, matching Buffer's expectations for a finished
    # social image, not a compositing layer).
    chart_on_white = Image.new("RGB", chart.size, WHITE)
    chart_on_white.paste(chart, (0, 0), mask=chart)
    canvas.paste(chart_on_white, (0, header_h))

    # Footer band
    footer_y0 = header_h + chart_h
    draw.rectangle([0, footer_y0, width, footer_y0 + footer_h], fill=FOOTER_BG)
    footer_font = _font(round(footer_h * 0.34))
    fx = _right_align_x(draw, "footballsiam.com", footer_font, width - margin)
    draw.text((fx, footer_y0 + footer_h / 2), "footballsiam.com", font=footer_font, fill=DARK_GREEN, anchor="lm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    print(f"Wrote {out_path} ({canvas.size[0]}x{canvas.size[1]})")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chart-png", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--title", required=True)
    p.add_argument("--subtitle1", required=True)
    p.add_argument("--subtitle2", default=None)
    args = p.parse_args(argv)
    wrap(args.chart_png, args.out, args.title, args.subtitle1, args.subtitle2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
