"""Use a born-digital PDF's own text layer, clipped to the crop regions."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from ..layout import main_minus_notes
from .base import OcrResult
from .textutil import join_lines


def _rect(page: "pymupdf.Page", box: dict | None, split: tuple[float, float] | None = None) -> "pymupdf.Rect":
    r = page.rect
    if box:
        x0, y0 = r.x0 + box["x"] * r.width, r.y0 + box["y"] * r.height
        x1, y1 = x0 + box["w"] * r.width, y0 + box["h"] * r.height
    else:
        x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
    if split:
        w = x1 - x0
        x0, x1 = x0 + split[0] * w, x0 + split[1] * w
    return pymupdf.Rect(x0, y0, x1, y1)


def _text(page, rect) -> str:
    blocks = page.get_text("blocks", clip=rect, sort=True)
    paras = []
    for b in blocks:
        if b[6] != 0:
            continue
        t = join_lines(b[4].splitlines())
        if t:
            paras.append(t)
    return "\n\n".join(paras)


def extract(pdf_path: Path, page_no: int, layout: dict) -> OcrResult:
    with pymupdf.open(pdf_path) as d:
        page = d[page_no]
        mode, s = layout.get("mode"), layout.get("split", 0.5)
        main = main_minus_notes(layout.get("main"), layout.get("notes"))
        res = OcrResult(confidence=100.0)
        if mode in ("columns", "facing"):
            left = _text(page, _rect(page, main, (0, s)))
            right = _text(page, _rect(page, main, (s, 1)))
            if mode == "facing":
                res.body, res.right = left, right
            else:
                res.body = "\n\n".join(x for x in (left, right) if x)
        else:
            res.body = _text(page, _rect(page, main))
        if layout.get("notes"):
            res.notes = _text(page, _rect(page, layout["notes"]))
        res.word_count = len(res.body.split())
        return res
