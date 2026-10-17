#!/usr/bin/env python3
"""
Football Siam social card toolkit (Instagram-first, 17 October 2026).

Every still social image Football Siam posts is 1080x1350 (4:5), built for
Instagram and then sent unchanged to X, LinkedIn, TikTok and LINE. Simon's
rule: "the Instagram format will work on all other socials, not the other
way round." The profile grid shows each post as a 3:4 centre slice
(x 34-1046 of a 1080x1350 card), and shows it at about a third of its size,
so this module enforces:

  * side margins of 80px for every piece of text and the elephant
    (the grid crop only takes 34px, the rest is breathing room);
  * minimum type sizes that still read at thumbnail size;
  * a hard failure, never a silent overflow, when text does not fit;
  * a grid preview (centre 3:4 at 360px wide) saved beside every card so
    the run can look at it before posting.

Every renderer in this repo that makes a social image imports this module:
social_carousel_cards.py (fixtures, results, tables), social_charts.py
(attendance, nationalities) and fs_social_card.py (article and one-off
cards). Change the look here, once.

Colours and fonts follow the fs-brand skill: green #0A3B25, gold #e3c567,
Playfair Display for headlines, Lato for everything else, gold Erawan
elephant on green.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
BRAND_DIR = ROOT / "assets" / "branding"

# ---- canvas and safe zones -------------------------------------------------
W, H = 1080, 1350
MARGIN_X = 80                 # text and elephant never closer than this to a side
GRID_CROP_X = (W - round(H * 3 / 4)) // 2   # 34: what the profile grid trims each side
TOP = 70
FOOTER_H = 80                 # gold band at the foot of every card
FOOTER_Y = H - FOOTER_H
CONTENT_R = W - MARGIN_X      # right edge of the safe area (1000)
CONTENT_W = CONTENT_R - MARGIN_X  # 920

# ---- minimum sizes (px at 1080 wide) ---------------------------------------
MIN_HEADLINE = 84
MIN_BIG = 200
MIN_KICKER = 30
MIN_DATA = 34
MIN_SUB = 32
MIN_SMALL = 24

# ---- colours ---------------------------------------------------------------
GREEN = (10, 59, 37)
GREEN_DEEP = (6, 38, 24)
GREEN_MID = (15, 107, 61)
GOLD = (227, 197, 103)
GOLD_TINT = (249, 241, 214)
WHITE = (255, 255, 255)
INK = (17, 17, 17)
BODY = (51, 51, 51)
MUTED = (118, 118, 118)
MUTED_ON_GREEN = (190, 205, 196)
ROW_ALT = (249, 249, 249)
HAIRLINE = (223, 223, 223)
GREY_BAR = (170, 176, 176)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def playfair(size: int) -> ImageFont.FreeTypeFont:
    return _font("PlayfairDisplay-Bold.ttf", size)


def lato_bold(size: int) -> ImageFont.FreeTypeFont:
    return _font("Lato-Bold.ttf", size)


def lato(size: int) -> ImageFont.FreeTypeFont:
    return _font("Lato-Regular.ttf", size)


class LayoutError(RuntimeError):
    """Raised when content will not fit the card at the minimum sizes.
    The caller should cut words or move content to another slide, never
    shrink below the minimums."""


class Card:
    """A 1080x1350 canvas that records where every piece of text and the
    elephant land, so check() can prove nothing strays into the grid crop."""

    def __init__(self, bg=GREEN):
        self.im = Image.new("RGB", (W, H), bg)
        self.d = ImageDraw.Draw(self.im)
        self.boxes: list[tuple[str, tuple[int, int, int, int]]] = []

    # -- recording helpers --------------------------------------------------
    def _rec(self, label: str, box):
        self.boxes.append((label, tuple(int(round(v)) for v in box)))

    def text(self, xy, s, font, fill, anchor="la", label=None):
        self.d.text(xy, s, font=font, fill=fill, anchor=anchor)
        self._rec(label or s, self.d.textbbox(xy, s, font=font, anchor=anchor))

    def width(self, s, font) -> float:
        return self.d.textlength(s, font=font)

    # -- shapes (full-bleed bands are allowed to touch the edges) -----------
    def rect(self, box, fill):
        self.d.rectangle(box, fill=fill)

    def rrect(self, box, fill, r=10):
        self.d.rounded_rectangle(box, radius=r, fill=fill)

    # -- the mark -------------------------------------------------------------
    def elephant(self, x, y, h, colour="gold"):
        el = Image.open(BRAND_DIR / f"elephant-{colour}.png").convert("RGBA")
        w = round(el.width * h / el.height)
        el = el.resize((w, h), Image.LANCZOS)
        self.im.paste(el, (int(x), int(y)), el)
        self._rec("elephant", (x, y, x + w, y + h))
        return w

    # -- checks and output ----------------------------------------------------
    def check(self):
        bad = []
        for label, (x0, y0, x1, y1) in self.boxes:
            if x0 < MARGIN_X - 4 or x1 > CONTENT_R + 4 or y0 < 20 or y1 > H - 10:
                bad.append(f"{label!r} at {(x0, y0, x1, y1)}")
        if bad:
            raise LayoutError("outside the safe area: " + "; ".join(bad))

    def save(self, out, preview=True):
        """Check the layout, save as JPEG (quality 90) or PNG by extension,
        and write <name>_grid.png: the 3:4 centre slice the Instagram profile
        grid shows, at 360px wide. Open that preview before posting."""
        self.check()
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() in (".jpg", ".jpeg"):
            self.im.save(out, "JPEG", quality=90, optimize=True, progressive=True)
        else:
            self.im.save(out, optimize=True)
        if preview:
            grid_preview(self.im, out.with_name(out.stem + "_grid.png"))
        return str(out)


def grid_preview(im: Image.Image, out):
    crop = im.crop((GRID_CROP_X, 0, W - GRID_CROP_X, H))
    crop = crop.resize((360, round(360 * crop.height / crop.width)), Image.LANCZOS)
    crop.save(out)
    return str(out)


# ---- text fitting ------------------------------------------------------------
def wrap(card: Card, text: str, font, max_w: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if card.width(t, font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    for ln in lines:
        if card.width(ln, font) > max_w:
            raise LayoutError(f"the word(s) {ln!r} are wider than {max_w}px at {font.size}px")
    return lines


def fit_lines(card, text, font_fn, start, minimum, max_w, max_lines):
    """Largest size from start down to minimum at which text wraps into
    max_lines or fewer. Raises LayoutError below the minimum."""
    size = start
    while size >= minimum:
        f = font_fn(size)
        try:
            lines = wrap(card, text, f, max_w)
            if len(lines) <= max_lines:
                return f, lines
        except LayoutError:
            pass
        size -= 2
    raise LayoutError(f"{text!r} does not fit {max_lines} line(s) at {minimum}px; cut it or split the slide")


def fit_one(card, text, font_fn, start, minimum, max_w):
    return fit_lines(card, text, font_fn, start, minimum, max_w, 1)[0]


# ---- shared furniture ----------------------------------------------------------
def footer(card: Card, page: str | None = None, note: str | None = None):
    """Gold band with FOOTBALLSIAM.COM; slide number (e.g. 2/4) on the right."""
    card.rect([0, FOOTER_Y, W, H], GOLD)
    mid = FOOTER_Y + FOOTER_H / 2
    card.text((MARGIN_X, mid), "FOOTBALLSIAM.COM", lato_bold(26), GREEN, anchor="lm")
    right = page or note
    if right:
        card.text((CONTENT_R, mid), right, lato_bold(26), GREEN, anchor="rm")


def data_header(card: Card, kicker: str, title: str, sub: str | None = None) -> int:
    """Green band for a data slide: elephant + gold kicker, then the title
    (Playfair, 84px, up to two lines), a gold rule and an optional sub-line.
    Returns the y where the white body starts."""
    eh = 64
    ew = card.elephant(MARGIN_X, TOP - 10, eh)
    kf = fit_one(card, kicker.upper(), lato_bold, 32, MIN_KICKER, CONTENT_W - ew - 24)
    card.text((MARGIN_X + ew + 24, TOP - 10 + eh / 2), kicker.upper(), kf, GOLD, anchor="lm")
    tf, lines = fit_lines(card, title.upper(), playfair, 88, MIN_HEADLINE, CONTENT_W, 2)
    y = TOP + eh + 26
    step = round(tf.size * 1.08)
    for ln in lines:
        card.text((MARGIN_X, y), ln, tf, WHITE, anchor="lt")
        y += step
    y += 10
    card.rect([MARGIN_X, y, MARGIN_X + 90, y + 6], GOLD)
    y += 6
    if sub:
        sf = fit_one(card, sub, lato_bold, 32, MIN_SUB, CONTENT_W)
        card.text((MARGIN_X, y + 22), sub, sf, MUTED_ON_GREEN, anchor="lt")
        y += 22 + sf.size
    band = y + 30
    card.rect([0, band, W, FOOTER_Y], WHITE)
    return band


def cover(card: Card, kicker: str, headline: str, sub: str | None = None,
          big: str | None = None, page: str | None = None, photo=None,
          focus_y: float = 0.35):
    """A cover / single card on green: elephant top-left, gold kicker, an
    optional big gold figure, the headline (84-110px, up to 4 lines, inside
    the top 60% of the card), a gold rule and the sub-line. With a photo,
    the photo fills the card and the type sits on a green scrim below."""
    text_top = TOP
    if photo is not None:
        _photo_fill(card, photo, focus_y)
    eh = 96
    ew = card.elephant(MARGIN_X, TOP, eh)
    kf = fit_one(card, kicker.upper(), lato_bold, 32, MIN_KICKER, CONTENT_W - ew - 28)
    card.text((MARGIN_X + ew + 28, TOP + eh / 2), kicker.upper(), kf, GOLD, anchor="lm")

    sub_lines, sf = [], None
    if sub:
        sf, sub_lines = fit_lines(card, sub, lato, 36, MIN_SUB, CONTENT_W, 3)
    bf = playfair(220) if big else None
    # Largest headline size whose block still ends inside the top ~60% of
    # the card (the part the grid thumbnail has to sell).
    limit = H * 0.62 + 40
    start_y = TOP + eh + 60 if big else 330
    room = limit - start_y - ((bf.size + 20) if big else 0)
    hf = h_lines = None
    for size in range(110, MIN_HEADLINE - 1, -2):
        try:
            f, ls = fit_lines(card, headline.upper(), playfair, size, size, CONTENT_W, 4)
        except LayoutError:
            continue
        if round(size * 1.06) * len(ls) <= room:
            hf, h_lines = f, ls
            break
    if hf is None:
        raise LayoutError(f"headline {headline!r} will not fit the top of the card at {MIN_HEADLINE}px; "
                          "shorten it" + (" or drop the big figure" if big else ""))
    h_step = round(hf.size * 1.06)

    block = (round(bf.size * 1.0) + 20 if big else 0) + h_step * len(h_lines) + 34 + \
        (len(sub_lines) * round(sf.size * 1.3) + 10 if sub_lines else 0)
    if photo is not None:
        y = FOOTER_Y - 60 - block          # type at the foot, over the scrim
    else:
        _watermark(card)
        y = TOP + eh + 60 if big else max(TOP + eh + 70, min(330, FOOTER_Y - 60 - block))
    if big:
        card.text((MARGIN_X, y), big, fit_one(card, big, playfair, 220, MIN_BIG, CONTENT_W), GOLD, anchor="lt")
        y += bf.size + 20
    head_top = y
    for ln in h_lines:
        card.text((MARGIN_X, y), ln, hf, WHITE, anchor="lt")
        y += h_step
    if photo is None and head_top + h_step * len(h_lines) > H * 0.62 + 40:
        raise LayoutError("headline runs below the top 60% of the card; shorten it or drop the big figure")
    y += 12
    card.rect([MARGIN_X, y, MARGIN_X + 90, y + 6], GOLD)
    y += 28
    for ln in sub_lines:
        card.text((MARGIN_X, y), ln, sf, (225, 233, 228), anchor="lt")
        y += round(sf.size * 1.3)
    if y > FOOTER_Y - 20:
        raise LayoutError("cover text runs into the footer; cut the sub-line")
    footer(card, page)


def _watermark(card: Card):
    """A large, faint gold elephant in the lower right of a plain green
    cover, so the bottom of the card is not dead space. Decoration only:
    it is not recorded as content and sits inside the side margin."""
    el = Image.open(BRAND_DIR / "elephant-gold.png").convert("RGBA")
    h = 420
    w = round(el.width * h / el.height)
    el = el.resize((w, h), Image.LANCZOS)
    a = el.getchannel("A").point(lambda v: int(v * 0.10))
    el.putalpha(a)
    card.im.paste(el, (CONTENT_R - w + 40, FOOTER_Y - h - 50), el)


def _photo_fill(card: Card, photo, focus_y: float):
    src = Image.open(photo).convert("RGB") if not isinstance(photo, Image.Image) else photo.convert("RGB")
    sw, sh = src.size
    scale = max(W / sw, H / sh)
    src = src.resize((round(sw * scale), round(sh * scale)), Image.LANCZOS)
    sw, sh = src.size
    x0 = (sw - W) // 2
    y0 = int(max(0, min(sh - H, focus_y * sh - H * 0.35)))
    card.im.paste(src.crop((x0, y0, x0 + W, y0 + H)), (0, 0))
    # top veil for the kicker, and a green scrim over the lower half for the type
    alpha = []
    for y in range(H):
        if y < 300:
            a = int(225 * (1 - y / 300) ** 1.2)
        elif y > H * 0.42:
            t = (y - H * 0.42) / (H * 0.58)
            a = int(250 * min(1, t * 1.25) ** 1.3)
        else:
            a = 0
        alpha.append(a)
    mask = Image.new("L", (1, H))
    mask.putdata(alpha)
    over = Image.new("RGBA", (W, H), GREEN + (0,))
    over.putalpha(mask.resize((W, H)))
    card.im = Image.alpha_composite(card.im.convert("RGBA"), over).convert("RGB")
    card.d = ImageDraw.Draw(card.im)


def image_slide(card: Card, kicker: str, title: str, image, sub: str | None = None,
                page: str | None = None, caption: str | None = None):
    """Any existing chart, table or photo (landscape or not) set into a 4:5
    data slide: green header with elephant, kicker and title, the image
    scaled to fit the white body inside the side margins (never cropped,
    never upscaled past 2x), an optional caption, the gold footer."""
    top = data_header(card, kicker, title, sub)
    raw = Image.open(image) if not isinstance(image, Image.Image) else image
    if raw.mode in ("RGBA", "LA", "P"):
        raw = raw.convert("RGBA")
        flat = Image.new("RGB", raw.size, WHITE)
        flat.paste(raw, (0, 0), raw)
        src = flat
    else:
        src = raw.convert("RGB")
    cap_h = 50 if caption else 0
    box_w, box_h = CONTENT_W, FOOTER_Y - top - 60 - cap_h
    scale = min(box_w / src.width, box_h / src.height, 2.0)
    w, h = round(src.width * scale), round(src.height * scale)
    if w < CONTENT_W * 0.6 and h < box_h * 0.6:
        raise LayoutError("image is too small to read on a slide; export it larger")
    src = src.resize((w, h), Image.LANCZOS)
    x = MARGIN_X + (CONTENT_W - w) // 2
    y = top + 30 + (box_h - h) // 2
    card.im.paste(src, (x, y))
    card._rec("image", (x, y, x + w, y + h))
    if caption:
        f = fit_one(card, caption, lato, 26, MIN_SMALL, CONTENT_W)
        card.text((MARGIN_X, y + h + 30), caption, f, MUTED, anchor="lm")
    footer(card, page)
