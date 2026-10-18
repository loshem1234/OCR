"""Generate a small 'scanned book' PDF (image-only) for smoke tests."""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path("/usr/share/fonts/truetype/noto")


def _font(size: int, bold: bool = False):
    name = "NotoSerif-Bold.ttf" if bold else "NotoSerif-Regular.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


PAGES = [
    {
        "head": "ON THE ELEMENTS OF THEOLOGY",
        "num": "17",
        "title": "Chapter One",
        "lines": [
            "Every manifold in some way participates unity. For if it",
            "in no way participates, neither will the whole be one whole,",
            "nor each of the many parts from which the manifold arises;",
            "but each part will itself be a manifold, and this to infi-",
            "nity, and of these infinites each will be, again, infinitely",
            "manifold.1 This argument is set out at greater length in",
            "the commentary, where the author returns to the theme of",
            "participation and the procession of all things from the",
        ],
        "notes": ["1 Compare Plato, Parmenides 137c and following."],
    },
    {
        "head": "PROCLUS",
        "num": "18",
        "title": None,
        "lines": [
            "first principle, which is beyond being and beyond the",
            "intellect itself. Whatever participates unity is both one",
            "and not-one. For inasmuch as it is not unity itself, it is",
            "not one; but since it participates, it is affected by one.",
            "",
            "Every manifold is posterior to the One. For if the One is",
            "posterior to the manifold, it will participate the manifold.",
        ],
        "notes": ["2 See also the scholia on this proposition."],
    },
    {
        "head": "ON THE ELEMENTS OF THEOLOGY",
        "num": "19",
        "title": "Chapter Two",
        "lines": [
            "All that is produced is produced by another. For if it were",
            "to produce itself, it would be both producer and produced,",
            "which is impossible for that which is simple and without",
            "parts. Therefore the cause is other than the effect.",
        ],
        "notes": [],
    },
]


def make_pdf(out: Path) -> Path:
    W, H = 1700, 2200  # ~200 dpi letter
    images = []
    for pg in PAGES:
        im = Image.new("L", (W, H), 248)
        d = ImageDraw.Draw(im)
        d.text((W // 2 - 300, 110), pg["head"], font=_font(30), fill=40)
        d.text((W - 220, 110), pg["num"], font=_font(30), fill=40)
        y = 260
        if pg["title"]:
            d.text((200, y), pg["title"], font=_font(64, bold=True), fill=20)
            y += 140
        for line in pg["lines"]:
            if line:
                d.text((200, y), line, font=_font(40), fill=15)
            y += 64
        if pg["notes"]:
            d.line((200, 1850, 700, 1850), fill=60, width=2)
            ny = 1880
            for n in pg["notes"]:
                d.text((200, ny), n, font=_font(30), fill=25)
                ny += 46
        im = im.rotate(0.3, fillcolor=248).filter(ImageFilter.GaussianBlur(0.6))
        images.append(im.convert("RGB"))
    images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=200)
    return out


if __name__ == "__main__":
    print(make_pdf(Path(sys.argv[1] if len(sys.argv) > 1 else "sample_scan.pdf")))
