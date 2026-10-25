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
                if ww and any(c.isalpha() for c in ww):  # one-letter words (ὁ, ἡ, ὦ, ἤ) count too
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


# =============================================================================================
# Scholarly profile: strict language models, geometry kept, nothing reformatted.
# =============================================================================================

class LanguageUnavailable(RuntimeError):
    """A required Tesseract model is missing. Never substitute another language for it."""


def available_langs(tessdata_dir: str = "") -> set[str]:
    try:
        cfg = f'--tessdata-dir "{tessdata_dir}"' if tessdata_dir else ""
        return {l for l in pytesseract.get_languages(config=cfg) if l != "osd"}
    except Exception:
        return set()


def strict_lang_string(langs: list[str], tessdata_dir: str = "") -> str:
    have = available_langs(tessdata_dir)
    missing = [l for l in langs if l not in have]
    if missing or not langs:
        where = tessdata_dir or "the default tessdata directory"
        raise LanguageUnavailable(f"Tesseract model(s) {', '.join(missing) or '(none requested)'} not installed in {where}")
    return "+".join(langs)


def tesseract_version() -> str:
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception:
        return "unknown"


def _split_mark_strips(lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """Polytonic pages: Tesseract sometimes reads the row of accents/breathings above a line as a separate
    'line' of junk (e.g. "Ξ .“. 5 , 3"). Such a pseudo-line is much shorter than a text line and contains
    few real Greek letters. It is set aside (kept for audit), never silently merged into the text."""
    from .greek import script_of
    heights = sorted(l["bbox"][3] - l["bbox"][1] for l in lines)
    med = heights[len(heights) // 2] or 1
    keep, drop = [], []
    for l in lines:
        h = l["bbox"][3] - l["bbox"][1]
        letters = [c for c in l["text"] if c.isalpha()]
        greek = sum(1 for c in letters if script_of(c) == "greek")
        words = [w for w in l["text"].split() if any(c.isalpha() for c in w)]
        avg_word = (sum(len(w) for w in words) / len(words)) if words else 0
        conf = [w["conf"] for w in l["words"] if w["conf"] >= 0]
        mean_conf = sum(conf) / len(conf) if conf else 0
        junk = (h < 0.6 * med and (avg_word < 2.6 or mean_conf < 55)) or (not letters) or \
               (greek / max(1, len(letters)) < 0.5 and mean_conf < 60)
        (drop if junk else keep).append(l)
    return keep, drop


def recognize_lines(im: Image.Image, langs: list[str], *, psm: int = 6, tessdata_dir: str = "",
                    preprocess_mode: str = "gray", scale: float = 1.0, extra_config: str = "",
                    drop_mark_strips: bool = True) -> dict:
    """Read a region and return physical lines with word geometry and confidence — no reformatting.

    Returns {"lines": [{"n", "text", "bbox", "words": [{"text","conf","bbox"}], "para_start"}],
             "confidence", "word_count", "low_conf", "raw" (TSV), "config"}.
    bboxes are in the coordinate system of `im` (pixels), before any scaling.
    """
    from .greek import nfc
    from .segment import preprocess

    lang = strict_lang_string(langs, tessdata_dir)
    pre = preprocess(im, preprocess_mode, scale)
    sx, sy = im.width / max(pre.width, 1), im.height / max(pre.height, 1)
    cfg = f"--oem 1 --psm {int(psm)} -c preserve_interword_spaces=1"
    if tessdata_dir:
        cfg = f'--tessdata-dir "{tessdata_dir}" ' + cfg
    if extra_config:
        cfg += " " + extra_config
    raw = pytesseract.image_to_data(pre, lang=lang, config=cfg)
    rows = [r.split("\t") for r in raw.splitlines()]
    header, body = rows[0], rows[1:]
    col = {k: i for i, k in enumerate(header)}
    lines: "OrderedDict[tuple, dict]" = OrderedDict()
    confs, low = [], OrderedDict()
    for r in body:
        if len(r) < len(header):
            continue
        text = r[col["text"]]
        if not text.strip():
            continue
        try:
            conf = float(r[col["conf"]])
        except ValueError:
            conf = -1.0
        l, t, w, h = (int(r[col[k]]) for k in ("left", "top", "width", "height"))
        bbox = [round(l * sx), round(t * sy), round((l + w) * sx), round((t + h) * sy)]
        key = (int(r[col["block_num"]]), int(r[col["par_num"]]), int(r[col["line_num"]]))
        ln = lines.setdefault(key, {"words": [], "par": key[:2]})
        ln["words"].append({"text": nfc(text), "conf": conf, "bbox": bbox})
        if conf >= 0:
            confs.append((conf, len(text)))
            if conf < settings.low_conf_word and any(c.isalpha() for c in text):
                low[nfc(text)] = None
    out, prev_par = [], None
    for ln in lines.values():
        ws = ln["words"]
        bx = [min(w["bbox"][0] for w in ws), min(w["bbox"][1] for w in ws),
              max(w["bbox"][2] for w in ws), max(w["bbox"][3] for w in ws)]
        out.append({"n": 0, "text": " ".join(w["text"] for w in ws), "bbox": bx, "words": ws,
                    "para_start": ln["par"] != prev_par})
        prev_par = ln["par"]
    suppressed = []
    if drop_mark_strips and len(out) >= 3:
        out, suppressed = _split_mark_strips(out)
    for i, ln in enumerate(out):
        ln["n"] = i + 1
    total = sum(n for _, n in confs)
    mean = round(sum(c * n for c, n in confs) / total, 1) if total else None
    return {"lines": out, "suppressed_lines": suppressed, "confidence": mean, "word_count": len(confs),
            "low_conf": list(low)[:500],
            "raw": raw, "config": {"lang": lang, "psm": psm, "oem": 1, "preprocess": preprocess_mode, "scale": scale,
                                   "drop_mark_strips": drop_mark_strips,
                                   "tessdata_dir": tessdata_dir or "default", "version": tesseract_version()}}
