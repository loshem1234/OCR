"""Two-page scans ("spreads"): find the gutter and cut a scan into a left and a right page.

The gutter is found by *text texture*, not by darkness: scans of bound books often have a dark binding
shadow in the gutter, which an ink-based method would mistake for text. Printed text produces many sharp
light/dark transitions down each pixel column; a gutter (white or shadowed) produces almost none.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

LANDSCAPE_RATIO = 1.15   # width/height above which a scan is treated as two pages
SEARCH_BAND = (0.30, 0.70)


def _texture_profile(im: Image.Image, work_width: int = 1200) -> tuple[np.ndarray, float]:
    g = im.convert("L")
    scale = 1.0
    if g.width > work_width:
        scale = work_width / g.width
        g = g.resize((work_width, max(1, int(g.height * scale))), Image.BILINEAR)
    a = np.asarray(g, dtype=np.int16)
    # sharp vertical transitions = edges of letters (a smooth shadow gradient has none)
    trans = (np.abs(np.diff(a, axis=0)) > 40).sum(axis=0).astype(np.float64)
    win = max(3, int(g.width * 0.012)) | 1
    kernel = np.ones(win) / win
    return np.convolve(trans, kernel, mode="same"), scale


def detect_gutter(im: Image.Image) -> dict:
    """Return {"is_spread", "x" (pixels in `im`), "fraction", "confidence" 0..1}."""
    w, h = im.size
    if w < LANDSCAPE_RATIO * h:
        return {"is_spread": False, "x": None, "fraction": None, "confidence": 0.0}
    prof, scale = _texture_profile(im)
    n = len(prof)
    lo, hi = int(n * SEARCH_BAND[0]), int(n * SEARCH_BAND[1])
    band = prof[lo:hi]
    m = float(band.min())
    # text level: typical texture of the two text blocks
    text_level = float(np.median(np.concatenate([prof[int(n * 0.1):int(n * 0.4)], prof[int(n * 0.6):int(n * 0.9)]])))
    thresh = m + 0.15 * max(text_level - m, 1.0)
    # widest run of near-minimal columns inside the band, preferring the one nearest the centre
    runs, start = [], None
    for i, v in enumerate(band):
        if v <= thresh and start is None:
            start = i
        elif v > thresh and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(band)))
    centre = (hi - lo) / 2
    best = max(runs, key=lambda r: (r[1] - r[0]) - 0.15 * abs((r[0] + r[1]) / 2 - centre)) if runs else (0, len(band))
    x_small = lo + (best[0] + best[1]) / 2
    conf = 0.0 if text_level <= 0 else max(0.0, min(1.0, 1.0 - m / text_level))
    x = int(round(x_small / scale))
    return {"is_spread": True, "x": x, "fraction": round(x / w, 5), "confidence": round(conf, 3)}


def split_at(im: Image.Image, x: int) -> tuple[Image.Image, Image.Image]:
    x = max(1, min(im.width - 1, int(x)))
    return im.crop((0, 0, x, im.height)), im.crop((x, 0, im.width, im.height))
