#!/usr/bin/env python3
"""
Football Siam social card: the 1080x1350 image that goes out with an article
(Instagram first, then the same file to X, LinkedIn, TikTok and LINE).

This replaces posting the 1200x675 featured image, which the Instagram grid
cuts to its middle half. The featured image stays for the website only.

    python3 src/fs_social_card.py OUT.jpg \
        --kicker "Thai League 2 · Matchweek 5 · Phil Williams" \
        --headline "Nakhon Pathom go top, Sisaket outdraw everyone" \
        --sub "Six goals at the top, and 6,111 fans in Satun" \
        [--big "6,111"] [--photo photo.jpg --focus 0.3] [--page 1/3]

--headline is set in Playfair caps, 84-110px, up to four lines; if it will
not fit at 84px the script stops and says so (cut words, do not shrink).
--image puts an existing chart, table or photo (any shape) on a data slide
under a green header instead: give --headline as the slide title, and
optionally --caption (e.g. "Data FootyStats"). Use it to turn landscape
charts into carousel slides; it never crops the image.

--photo fills the card with the photo cropped to 4:5 and puts the type on a
green scrim in the lower half; --focus is where the subject's face sits,
as a fraction of the photo's height (0 top, 1 bottom).

Writes OUT and OUT_grid.png (the 3:4 grid slice at 360px). Open both.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fs_social as fs  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out")
    p.add_argument("--kicker", required=True)
    p.add_argument("--headline", required=True)
    p.add_argument("--sub")
    p.add_argument("--big", help="short figure or score set large above the headline, e.g. 6,111")
    p.add_argument("--photo")
    p.add_argument("--focus", type=float, default=0.35)
    p.add_argument("--image", help="chart/table/photo to set on a data slide (not cropped)")
    p.add_argument("--caption")
    p.add_argument("--credit", help="photo credit set small above the footer on a photo cover")
    p.add_argument("--page", help="slide number for the footer when this is a carousel cover, e.g. 1/3")
    a = p.parse_args(argv)
    card = fs.Card()
    try:
        if a.image:
            card = fs.Card(bg=fs.GREEN)
            fs.image_slide(card, a.kicker, a.headline, a.image, a.sub, a.page, a.caption)
        else:
            fs.cover(card, a.kicker, a.headline, a.sub, a.big, a.page, a.photo, a.focus, a.credit)
        print("wrote", card.save(a.out))
    except fs.LayoutError as e:
        print("LAYOUT ERROR:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
