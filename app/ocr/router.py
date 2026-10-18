"""Hybrid routing: decide per page which engine reads it, run it, store the result."""
from __future__ import annotations

import logging

from PIL import Image
from sqlalchemy import select

from .. import storage
from ..config import settings
from ..db import Document, Page, session_scope
from ..ingest import source_for_page
from ..layout import regions, resolve
from . import embedded_engine, gemini_engine, tesseract_engine
from .base import OcrResult
from .textutil import clean_text, script_counts

log = logging.getLogger("router")


def _tess(reg: dict, langs: list[str], notes_wanted: bool) -> OcrResult:
    mode = reg["mode"]
    if mode in ("columns", "facing"):
        L = tesseract_engine.ocr_region(reg["left"], langs)
        R = tesseract_engine.ocr_region(reg["right"], langs)
        res = OcrResult(low_conf=L.low_conf + [w for w in R.low_conf if w not in L.low_conf],
                        word_count=L.word_count + R.word_count)
        if mode == "facing":
            res.body, res.right = L.body, R.body
        else:
            res.body = "\n\n".join(x for x in (L.body, R.body) if x)
        cs = [(r.confidence, r.word_count) for r in (L, R) if r.confidence is not None and r.word_count]
        if cs:
            res.confidence = round(sum(c * n for c, n in cs) / sum(n for _, n in cs), 1)
    else:
        res = tesseract_engine.ocr_region(reg["main"], langs)
    if notes_wanted and reg["notes"] is not None:
        n = tesseract_engine.plain(reg["notes"], langs)
        res.notes = n.body
        res.low_conf += [w for w in n.low_conf if w not in res.low_conf]
    return res


def _gemini(reg: dict, doc_settings: dict, notes_wanted: bool) -> OcrResult:
    notes_img = reg["notes"] if notes_wanted else None
    if reg["mode"] == "facing":
        L = gemini_engine.transcribe(reg["left"], doc_settings, notes=notes_img, footnotes_wanted=notes_wanted)
        R = gemini_engine.transcribe(reg["right"], doc_settings, footnotes_wanted=notes_wanted)
        return OcrResult(
            body=L.body, right=R.body, notes="\n\n".join(x for x in (L.notes, R.notes) if x),
            low_conf=L.low_conf + R.low_conf, printed_page=L.printed_page or R.printed_page,
            tokens_in=L.tokens_in + R.tokens_in, tokens_out=L.tokens_out + R.tokens_out,
        )
    return gemini_engine.transcribe(reg["main"], doc_settings, notes=notes_img,
                                    columns=reg["mode"] == "columns", footnotes_wanted=notes_wanted)


def _needs_gemini(res: OcrResult, langs: list[str]) -> str:
    c = script_counts(res.body + res.right + res.notes)
    if c["polytonic"] > 0 or (c["greek"] >= 3 and ("grc" in langs or "ell" not in langs)):
        return "Greek detected"
    if c["hebrew"] >= 3:
        return "Hebrew detected"
    if c["arabic"] >= 3:
        return "Arabic detected"
    if c["syriac"] >= 3:
        return "Syriac detected"
    if res.confidence is not None and res.word_count >= 5 and res.confidence < settings.hybrid_conf_threshold:
        return f"low confidence ({res.confidence:.0f}%)"
    return ""


def run_page(page_id: int) -> None:
    with session_scope() as s:
        page = s.get(Page, page_id)
        if page is None:  # document deleted while queued
            return
        doc = s.get(Document, page.doc_id)
        ds = dict(doc.settings or {})
        overrides = [(i, o) for i, o in s.execute(
            select(Page.index, Page.override).where(Page.doc_id == page.doc_id, Page.index <= page.index))]
        layout, _src = resolve(doc.layouts, page.index, page.override, overrides)
        index, forced, has_text = page.index, page.forced_engine, page.has_text_layer
        src = source_for_page(doc, index)
        doc_id = doc.id

    langs = ds.get("languages") or ["eng"]
    notes_wanted = ds.get("footnote_mode") == "separate"
    engine_choice = forced or ds.get("engine", "auto")
    warning = ""
    result: OcrResult | None = None
    used, reason = "", ""

    try:
        if engine_choice in ("embedded",) or (
            engine_choice == "auto" and ds.get("use_text_layer") and has_text and src and src[0].exists()
        ):
            if not (src and src[0].exists()):
                raise RuntimeError("No PDF text layer available for this page")
            result, used, reason = embedded_engine.extract(src[0], src[1], layout), "embedded", "PDF text layer"
        else:
            img_path = storage.page_image_path(doc_id, index)
            if not img_path.exists():
                raise RuntimeError("Page image was purged to save space; re-upload to re-run OCR.")
            with Image.open(img_path) as im:
                im.load()
                reg = regions(im, layout)

            if engine_choice == "tesseract" or (engine_choice == "auto" and not settings.gemini_enabled):
                result, used = _tess(reg, langs, notes_wanted), "tesseract"
                reason = "forced" if forced else ("Gemini not configured" if engine_choice == "auto" else "document setting")
            elif engine_choice == "gemini":
                result, used, reason = _gemini(reg, ds, notes_wanted), "gemini", "forced" if forced else "document setting"
            else:  # auto / hybrid
                tess_res: OcrResult | None = None
                if ds.get("preserve_refs"):
                    reason = "reference numbers preserved"
                elif set(langs) <= tesseract_engine.NON_LATIN_LANGS:
                    reason = "non-Latin script"
                else:
                    tess_res = _tess(reg, langs, notes_wanted)
                    reason = _needs_gemini(tess_res, langs)
                if not reason:
                    result, used, reason = tess_res, "tesseract", "high confidence"
                else:
                    try:
                        result, used = _gemini(reg, ds, notes_wanted), "gemini"
                    except Exception as e:  # fall back to Tesseract rather than losing the page
                        log.warning("gemini failed on page %s: %s", page_id, e)
                        warning = f"Gemini failed ({str(e)[:160]}); Tesseract result kept."
                        result = tess_res or _tess(reg, langs, notes_wanted)
                        used = "tesseract"
    except Exception as e:
        log.exception("OCR failed for page %s", page_id)
        with session_scope() as s:
            page = s.get(Page, page_id)
            if page is not None:
                page.status, page.error = "error", str(e)[:1000]
        return

    with session_scope() as s:
        page = s.get(Page, page_id)
        if page is None or page.status != "running":  # deleted, cancelled or re-queued meanwhile
            return
        page.body = clean_text(result.body)
        page.right = clean_text(result.right)
        page.notes = clean_text(result.notes)
        page.low_conf = result.low_conf
        page.confidence = result.confidence
        page.printed_page = result.printed_page
        page.engine, page.route_reason = used, reason[:200]
        page.tokens_in += result.tokens_in
        page.tokens_out += result.tokens_out
        page.warning = (warning or result.warning)[:1000]
        page.error = ""
        page.edited = False
        page.reviewed = False
        page.forced_engine = ""
        page.status = "done"
