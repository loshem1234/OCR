"""Crop-layout resolution: page override > odd/even layout > all-pages layout."""
from __future__ import annotations

from typing import Any

from PIL import Image

from .db import DEFAULT_LAYOUT

MODES = ("single", "columns", "facing")


def clean_box(b: Any) -> dict | None:
    if not isinstance(b, dict):
        return None
    try:
        x, y, w, h = (float(b[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(max(w, 0.0), 1.0 - x), min(max(h, 0.0), 1.0 - y)
    if w < 0.01 or h < 0.01:
        return None
    return {"x": round(x, 5), "y": round(y, 5), "w": round(w, 5), "h": round(h, 5)}


def clean_layout(layout: Any) -> dict | None:
    if layout is None:
        return None
    if not isinstance(layout, dict):
        return dict(DEFAULT_LAYOUT)
    mode = layout.get("mode", "single")
    try:
        split = float(layout.get("split", 0.5))
    except (TypeError, ValueError):
        split = 0.5
    return {
        "main": clean_box(layout.get("main")),
        "notes": clean_box(layout.get("notes")),
        "mode": mode if mode in MODES else "single",
        "split": min(max(split, 0.05), 0.95),
    }


def clean_layouts(layouts: Any) -> dict:
    layouts = layouts if isinstance(layouts, dict) else {}
    return {
        "global": clean_layout(layouts.get("global")) or dict(DEFAULT_LAYOUT),
        "odd": clean_layout(layouts.get("odd")),
        "even": clean_layout(layouts.get("even")),
        "use_odd_even": bool(layouts.get("use_odd_even", False)),
    }


def resolve(layouts: dict, index: int, override: dict | None) -> tuple[dict, str]:
    """Return (layout, source) where source is 'page' | 'odd' | 'even' | 'global'."""
    if override:
        return clean_layout(override), "page"
    layouts = clean_layouts(layouts)
    if layouts["use_odd_even"]:
        key = "odd" if (index + 1) % 2 == 1 else "even"
        if layouts[key]:
            return layouts[key], key
    return layouts["global"], "global"


def crop_box(im: Image.Image, box: dict | None) -> Image.Image:
    if not box:
        return im
    W, H = im.size
    l, t = int(box["x"] * W), int(box["y"] * H)
    r, b = int((box["x"] + box["w"]) * W), int((box["y"] + box["h"]) * H)
    return im.crop((l, t, max(r, l + 1), max(b, t + 1)))


def split_columns(im: Image.Image, split: float) -> tuple[Image.Image, Image.Image]:
    W, H = im.size
    x = int(W * split)
    return im.crop((0, 0, max(x, 1), H)), im.crop((min(x, W - 1), 0, W, H))


def main_minus_notes(main: dict | None, notes: dict | None) -> dict | None:
    """If the footnote box overlaps the main box, cut it out of the main box (top or bottom)."""
    if not notes:
        return main
    m = dict(main or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
    overlap_x = min(m["x"] + m["w"], notes["x"] + notes["w"]) - max(m["x"], notes["x"])
    overlap_y = min(m["y"] + m["h"], notes["y"] + notes["h"]) - max(m["y"], notes["y"])
    if overlap_x <= 0 or overlap_y <= 0:
        return main
    if notes["y"] + notes["h"] / 2 >= m["y"] + m["h"] / 2:      # notes at the foot
        new_h = notes["y"] - m["y"]
        if new_h > 0.05:
            m["h"] = new_h
            return m
    else:                                                        # notes at the head
        bottom = m["y"] + m["h"]
        new_y = notes["y"] + notes["h"]
        if bottom - new_y > 0.05:
            m["y"], m["h"] = new_y, bottom - new_y
            return m
    return main


def regions(im: Image.Image, layout: dict, exclude_notes: bool = True) -> dict:
    """Cut a page image into the regions the OCR engines need."""
    main_box = main_minus_notes(layout.get("main"), layout.get("notes")) if exclude_notes else layout.get("main")
    main = crop_box(im, main_box)
    notes = crop_box(im, layout["notes"]) if layout.get("notes") else None
    out = {"mode": layout.get("mode", "single"), "main": main, "notes": notes, "left": None, "right": None}
    if out["mode"] in ("columns", "facing"):
        out["left"], out["right"] = split_columns(main, layout.get("split", 0.5))
    return out
