"""Image analysis for the scholarly profile: text-line bands, line-safe segmentation, crop-edge checks,
and the (configurable, measurable) preprocessing variants used before Tesseract.

Segmentation rule: a region is only ever cut through rows that contain *no* ink at all, so a cut can
never pass through a letter, accent, breathing or iota subscript. If no fully blank row exists where a
cut is wanted, the segment simply grows; correctness wins over segment size.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps


def to_gray(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("L"), dtype=np.uint8)


def otsu(a: np.ndarray) -> int:
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    total = a.size
    if total == 0:
        return 128
    sum_all = np.dot(np.arange(256), hist)
    w_b = sum_b = 0.0
    best, thresh = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thresh = between, t
    return int(thresh)


def ink_mask(im: Image.Image) -> np.ndarray:
    a = to_gray(im)
    t = otsu(a)
    # Guard against blank or near-blank regions where Otsu picks a meaningless threshold.
    t = min(t, 200)
    return a <= t


@dataclass
class Band:
    top: int
    bottom: int  # exclusive

    @property
    def height(self) -> int:
        return self.bottom - self.top


def line_bands(im: Image.Image, mask: np.ndarray | None = None) -> list[Band]:
    """Estimate printed text lines from the horizontal ink profile. Small bands (accents/breathings that
    float above a line with a hairline gap) are merged into the neighbouring line."""
    m = ink_mask(im) if mask is None else mask
    if m.size == 0:
        return []
    w = m.shape[1]
    rows = m.sum(axis=1)
    thresh = max(1, int(w * 0.002))
    inked = rows > thresh
    bands: list[Band] = []
    y, H = 0, len(rows)
    while y < H:
        if inked[y]:
            y0 = y
            while y < H and inked[y]:
                y += 1
            bands.append(Band(y0, y))
        else:
            y += 1
    if not bands:
        return []
    for _ in range(3):  # merge small fragments into neighbours
        med = float(np.median([b.height for b in bands]))
        merged: list[Band] = []
        i = 0
        changed = False
        while i < len(bands):
            b = bands[i]
            if b.height < 0.45 * med and len(bands) > 1:
                gap_prev = b.top - merged[-1].bottom if merged else 10 ** 9
                gap_next = bands[i + 1].top - b.bottom if i + 1 < len(bands) else 10 ** 9
                if gap_next <= gap_prev and i + 1 < len(bands):
                    nb = bands[i + 1]
                    merged.append(Band(b.top, nb.bottom))
                    i += 2
                    changed = True
                    continue
                if merged:
                    merged[-1] = Band(merged[-1].top, b.bottom)
                    i += 1
                    changed = True
                    continue
            merged.append(b)
            i += 1
        bands = merged
        if not changed:
            break
    # Drop specks (very short bands far from any line) — they are noise, not lines.
    med = float(np.median([b.height for b in bands]))
    return [b for b in bands if b.height >= 0.25 * med]


def blank_rows(mask: np.ndarray) -> np.ndarray:
    return mask.sum(axis=1) == 0


@dataclass
class Segment:
    top: int
    bottom: int
    est_lines: int
    bands: list[Band]


def segments(im: Image.Image, max_lines: int = 12, max_height: int = 0) -> list[Segment]:
    """Split a region into horizontal strips of whole lines, cutting only through fully blank rows."""
    mask = ink_mask(im)
    H = mask.shape[0]
    bands = line_bands(im, mask)
    if not bands:
        return [Segment(0, H, 0, [])]
    blank = blank_rows(mask)
    out: list[Segment] = []
    start, cur = 0, []
    for i, b in enumerate(bands):
        cur.append(b)
        last = i == len(bands) - 1
        too_many = len(cur) >= max_lines
        too_tall = max_height and (b.bottom - start) >= max_height * 0.85
        if last or not (too_many or too_tall):
            continue
        nxt = bands[i + 1]
        # find a fully blank row in the gap, closest to its middle
        gap = range(b.bottom, nxt.top)
        cands = [y for y in gap if blank[y]]
        if not cands:
            continue  # cannot cut safely here; keep growing
        mid = (b.bottom + nxt.top) / 2
        cut = min(cands, key=lambda y: abs(y - mid))
        out.append(Segment(start, cut, len(cur), cur))
        start, cur = cut, []
    out.append(Segment(start, H, len(cur), cur))
    return out


def edge_contacts(im: Image.Image, depth: int = 3) -> list[str]:
    """Edges of a crop where ink touches the border (a letter or accent may have been cut)."""
    m = ink_mask(im)
    if m.size == 0:
        return []
    h, w = m.shape
    d = max(1, min(depth, h // 4, w // 4))
    out = []
    checks = {"top": m[:d, :], "bottom": m[-d:, :], "left": m[:, :d], "right": m[:, -d:]}
    for name, strip in checks.items():
        length = strip.shape[1] if name in ("top", "bottom") else strip.shape[0]
        # several separate ink columns/rows touching the edge = text, not a stray speck
        touching = strip.any(axis=0 if name in ("top", "bottom") else 1).sum()
        if touching > max(6, 0.01 * length):
            out.append(name)
    return out


# ------------------------------------------------------------------ preprocessing variants (benchmarkable)

def preprocess(im: Image.Image, mode: str = "gray", scale: float = 1.0) -> Image.Image:
    """Variants for Tesseract. The original image is never modified; a new image is returned.

    gray      — grayscale only (no other change)
    legacy    — the standard profile's behaviour: upscale small crops, autocontrast with 1% clipping
    contrast  — mild autocontrast (0.5% clipping), no binarization
    otsu      — global Otsu binarization
    sauvola   — local (Sauvola) binarization for uneven or faded pages
    """
    g = im.convert("L")
    if mode == "legacy":
        if g.width < 1200:
            f = 1200 / max(g.width, 1)
            g = g.resize((int(g.width * f), int(g.height * f)), Image.LANCZOS)
        g = ImageOps.autocontrast(g, cutoff=1)
    elif mode == "contrast":
        g = ImageOps.autocontrast(g, cutoff=0.5)
    elif mode == "otsu":
        a = to_gray(g)
        t = otsu(a)
        g = Image.fromarray(np.where(a <= t, 0, 255).astype(np.uint8))
    elif mode == "sauvola":
        g = Image.fromarray(_sauvola(to_gray(g)))
    if scale and abs(scale - 1.0) > 1e-3:
        g = g.resize((max(1, int(g.width * scale)), max(1, int(g.height * scale))), Image.LANCZOS)
    return g


def _sauvola(a: np.ndarray, window: int = 0, k: float = 0.2, r: float = 128.0) -> np.ndarray:
    h, w = a.shape
    window = window or max(15, (min(h, w) // 40) | 1)
    f = a.astype(np.float64)
    pad = window // 2
    p = np.pad(f, pad, mode="edge")
    ii = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    ii2 = np.pad((p * p).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    y0, x0 = np.arange(h), np.arange(w)
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij")
    Y1, X1 = Y0 + window, X0 + window
    n = window * window
    s = ii[Y1, X1] - ii[Y0, X1] - ii[Y1, X0] + ii[Y0, X0]
    s2 = ii2[Y1, X1] - ii2[Y0, X1] - ii2[Y1, X0] + ii2[Y0, X0]
    mean = s / n
    std = np.sqrt(np.maximum(s2 / n - mean * mean, 0))
    t = mean * (1 + k * (std / r - 1))
    return np.where(f <= t, 0, 255).astype(np.uint8)


def deskew_angle(im: Image.Image, max_deg: float = 2.0, step: float = 0.1) -> float:
    """Angle (degrees) that maximizes horizontal-profile sharpness. Used only when enabled."""
    small = im.convert("L")
    if small.width > 1400:
        f = 1400 / small.width
        small = small.resize((1400, max(1, int(small.height * f))), Image.BILINEAR)
    best, best_a = -1.0, 0.0
    for a in np.arange(-max_deg, max_deg + 1e-9, step):
        r = small.rotate(float(a), resample=Image.BILINEAR, expand=False, fillcolor=255)
        m = to_gray(r) < 128
        prof = m.sum(axis=1).astype(np.float64)
        score = float(np.var(prof))
        if score > best:
            best, best_a = score, float(a)
    return round(best_a, 2)
