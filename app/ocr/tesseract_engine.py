"""Tesseract pass: paragraphs, hyphen rejoining, heading guesses, per-word confidence."""
from __future__ import annotations

import statistics
from collections import OrderedDict
from functools import lru_cache

import pytesseract
from PIL import Image, ImageOps

from ..config import settings
from .base import OcrResult
from .textutil import join_lines

TESS_LANGS = {
    "eng": "English", "lat": "Latin", "grc": "Ancient Greek (polytonic)", "ell": "Modern Greek",
    "heb": "Hebrew", "ara": "Arabic", "syr": "Syriac", "deu": "German", "fra": "French",
    "ita": "Italian", "spa": "Spanish",
}
NON_LATIN_LANGS = {"grc", "ell", "heb", "ara", "syr"}
STRIP = ".,;:·!?()[]{}«»\"'“”‘’—–-*"


@lru_cache(maxsize=1)
def installed_langs() -> tuple[str, ...]:
    try:
        return tuple(sorted(l for l in pytesseract.get_languages(config="") if l != "osd"))
    except Exception:
        return ()


def lang_string(langs: list[str]) -> str:
    have = set(installed_langs())
    chosen = [l for l in langs if l in have] or (["eng"] if "eng" in have else list(have)[:1])
    return "+".join(chosen) or "eng"


def _prep(im: Image.Image) -> Image.Image:
    im = im.convert("L")
    if im.width < 1200:  # low-resolution scans OCR far better upscaled
        f = 1200 / max(im.width, 1)
        im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    return ImageOps.autocontrast(im, cutoff=1)


PARA_END = tuple(".!?»\"”’)")  # not ; or : — a short line ending in those is usually ragged setting


def _paragraphs(lines: list[dict], detect_headings: bool) -> list[str]:
    """Group OCR lines into paragraphs using vertical gaps, indents, short last lines and type size."""
    if not lines:
        return []
    for ln in lines:
        ln["h"] = statistics.median(ln["heights"]) if ln["heights"] else (ln["bottom"] - ln["top"])
        ln["text"] = " ".join(ln["words"])
    med_h = statistics.median(ln["h"] for ln in lines) or 1
    gaps = [b["top"] - a["bottom"] for a, b in zip(lines, lines[1:])
            if a["block"] == b["block"] and b["top"] > a["top"]]
    normal_gap = statistics.median(gaps) if gaps else med_h * 0.5
    min_left = statistics.median(sorted(ln["left"] for ln in lines)[: max(1, len(lines) // 3)])
    max_right = max(ln["right"] for ln in lines)
    measure = max(1, max_right - min_left)

    def is_heading(ln: dict) -> bool:
        return detect_headings and ln["h"] >= 1.35 * med_h and len(ln["words"]) <= 12

    groups: list[list[dict]] = []
    for i, ln in enumerate(lines):
        if i == 0:
            groups.append([ln])
            continue
        prev = lines[i - 1]
        gap = ln["top"] - prev["bottom"]
        new = (
            gap > normal_gap + 0.7 * med_h                                   # extra vertical space
            or ln["top"] < prev["top"]                                       # jumped back up: new column/block
            or (ln["left"] - min_left > 1.2 * med_h and prev["left"] - min_left < 0.6 * med_h)  # first-line indent
            or (max_right - prev["right"] > 0.15 * measure and prev["text"].rstrip().endswith(PARA_END))  # short last line
            or is_heading(ln) != is_heading(prev)
            or (is_heading(ln) and abs(ln["h"] - prev["h"]) > 0.25 * ln["h"])
        )
        if new:
            groups.append([ln])
        else:
            groups[-1].append(ln)

    out = []
    for g in groups:
        text = join_lines([ln["text"] for ln in g])
        if not text:
            continue
        if all(is_heading(ln) for ln in g) and len(g) <= 3:
            text = "## " + text
        out.append(text)
    return out


def ocr_region(im: Image.Image, langs: list[str], detect_headings: bool = True) -> OcrResult:
    data = pytesseract.image_to_data(
        _prep(im), lang=lang_string(langs), config="--psm 3", output_type=pytesseract.Output.DICT
    )
    # Collect lines with their geometry; Tesseract's own paragraph numbers are unreliable on book pages.
    lines: "OrderedDict[tuple, dict]" = OrderedDict()
    confs: list[tuple[float, int]] = []
    low: "OrderedDict[str, None]" = OrderedDict()

    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        l, t, w, h = (int(data[k][i]) for k in ("left", "top", "width", "height"))
        ln = lines.setdefault(key, {"words": [], "left": l, "top": t, "right": l + w, "bottom": t + h,
                                    "heights": [], "block": data["block_num"][i]})
        ln["words"].append(word)
        ln["left"], ln["top"] = min(ln["left"], l), min(ln["top"], t)
        ln["right"], ln["bottom"] = max(ln["right"], l + w), max(ln["bottom"], t + h)
        if any(c.isalpha() for c in word):
            ln["heights"].append(h)
        if conf >= 0:
            confs.append((conf, len(word)))
            if conf < settings.low_conf_word:
                ww = word.strip(STRIP)
                if len(ww) >= 2:
                    low[ww] = None

    blocks = _paragraphs(list(lines.values()), detect_headings)

    total_chars = sum(n for _, n in confs)
    mean = (sum(c * n for c, n in confs) / total_chars) if total_chars else None
    return OcrResult(
        body="\n\n".join(blocks),
        low_conf=list(low.keys())[:300],
        confidence=round(mean, 1) if mean is not None else None,
        word_count=len(confs),
    )


def plain(im: Image.Image, langs: list[str]) -> OcrResult:
    """Footnote regions: no heading detection."""
    return ocr_region(im, langs, detect_headings=False)
