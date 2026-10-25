"""Scholarly Greek profile — independent dual recognition, collation, proposals, provenance.

    The printed image is the authority. OCR engines are witnesses. AI corrections are proposals.
    Only human-reviewed readings belong in the master text.

Per region (main text, each column, or the apparatus box):
  A. Tesseract (strict Ancient Greek model, geometry kept)   ─┐ never see each other's output
  B. Gemini on lossless, line-safe strips (physical lines)    ─┘
  → collation (app/ocr/collation.py) → optional reconciliation *proposals* for disputed places
  → working text = the primary witness's lines (default Gemini), one printed line per line.

Raw outputs are stored in the `recognitions` table and never overwritten; the page's previous text is
snapshotted to `page_revisions` before a re-run replaces it.
"""
from __future__ import annotations

import logging
import statistics
import uuid

from PIL import Image
from sqlalchemy import func, select

from .. import storage
from ..config import settings
from ..db import Document, Page, PageRevision, Recognition, session_scope
from ..layout import crop_box, main_minus_notes, resolve
from . import gemini_engine
from .collation import collate, disagreement_words
from .greek import nfc
from .segment import edge_contacts, segments
from .tesseract_engine import LanguageUnavailable, recognize_lines

log = logging.getLogger("scholarly")

PROFILE = "scholarly_greek"
SCHOLARLY_LANGS = {"grc", "lat", "ell"}


# ------------------------------------------------------------------ profile selection

def profile_for(doc_settings: dict) -> str:
    mode = settings.scholarly_greek
    if mode == "never":
        return "standard"
    explicit = (doc_settings or {}).get("profile", "")
    if explicit in ("standard", PROFILE):
        return explicit
    if mode == "always":
        return PROFILE
    langs = set((doc_settings or {}).get("languages") or [])
    return PROFILE if "grc" in langs and langs <= SCHOLARLY_LANGS else "standard"


# ------------------------------------------------------------------ witnesses

def tesseract_witness(region: Image.Image, page_dpi: int | None = None) -> dict:
    target = settings.scholarly_tess_target_dpi
    scale = round(target / page_dpi, 4) if page_dpi and target and page_dpi > target * 1.15 else 1.0
    try:
        r = recognize_lines(region, settings.scholarly_tess_langs, psm=settings.scholarly_tess_psm,
                            tessdata_dir=settings.scholarly_tessdata_dir,
                            preprocess_mode=settings.scholarly_tess_preprocess, scale=scale)
    except LanguageUnavailable as e:
        return {"status": "unavailable", "lines": [], "warnings": [str(e)], "raw": "", "config": {}}
    except Exception as e:
        log.exception("tesseract failed")
        return {"status": "failed", "lines": [], "warnings": [f"Tesseract failed: {e}"], "raw": "", "config": {}}
    lines = r["lines"]
    _tesseract_paragraphs(lines)
    warns = []
    if r.get("suppressed_lines"):
        warns.append(f"{len(r['suppressed_lines'])} accent-strip pseudo-line(s) set aside (kept in raw output)")
    status = "ok" if lines else ("ok" if not region_has_text(region) else "invalid")
    if status == "invalid":
        warns.append("no text recognized although the region contains print")
    return {"status": status, "lines": lines, "suppressed": r.get("suppressed_lines", []), "warnings": warns,
            "raw": r["raw"], "confidence": r["confidence"], "low_conf": r["low_conf"],
            "config": {**r["config"], "page_dpi": page_dpi}}


def _tesseract_paragraphs(lines: list[dict]) -> None:
    """Paragraph starts from geometry (indent or extra leading); Tesseract's own paragraph ids are unreliable."""
    if not lines:
        return
    hs = [l["bbox"][3] - l["bbox"][1] for l in lines]
    med_h = statistics.median(hs) or 1
    lefts = sorted(l["bbox"][0] for l in lines)
    base_left = lefts[len(lefts) // 4]
    gaps = [b["bbox"][1] - a["bbox"][3] for a, b in zip(lines, lines[1:])]
    med_gap = statistics.median(gaps) if gaps else 0
    for i, l in enumerate(lines):
        indent = l["bbox"][0] - base_left > 0.9 * med_h
        gap = (l["bbox"][1] - lines[i - 1]["bbox"][3]) if i else 0
        l["para_start"] = i == 0 or indent or gap > med_gap + 0.8 * med_h
        l.setdefault("kind", "text")


def region_has_text(region: Image.Image) -> bool:
    from .segment import line_bands
    return bool(line_bands(region))


def gemini_witness(region: Image.Image, doc_settings: dict, region_name: str = "main") -> dict:
    if not settings.gemini_enabled:
        return {"status": "unavailable", "lines": [], "warnings": ["GEMINI_API_KEY not set"], "raw": [],
                "tokens_in": 0, "tokens_out": 0, "model": "", "config": {}}
    segs = segments(region, max_lines=settings.scholarly_segment_lines)
    parts = [(region.crop((0, s.top, region.width, s.bottom)), s.est_lines) for s in segs]
    w = gemini_engine.transcribe_segments(parts, doc_settings, region=region_name)
    # approximate line boxes (region pixels) from the detected bands, when the counts line up
    by_seg: dict[int, list[dict]] = {}
    for ln in w["lines"]:
        by_seg.setdefault(ln.get("segment", 1), []).append(ln)
    for si, s in enumerate(segs, start=1):
        got = by_seg.get(si, [])
        if got and len(got) == len(s.bands):
            for ln, b in zip(got, s.bands):
                ln["bbox"] = [0, s.top + b.top, region.width, s.top + b.bottom]
                ln["bbox_source"] = "image bands"
        else:
            for ln in got:
                ln["bbox"] = [0, s.top, region.width, s.bottom]
                ln["bbox_source"] = "segment"
    w["segments"] = [{"top": s.top, "bottom": s.bottom, "est_lines": s.est_lines} for s in segs]
    return w


# ------------------------------------------------------------------ working text

def lines_to_working_text(lines: list[dict]) -> str:
    """Line-format working text: one printed line per line, blank line between paragraphs,
    '## ' for headings, a '%verse' line before a run of verse lines."""
    blocks: list[list[str]] = []
    prev_kind = None
    for i, ln in enumerate(lines):
        kind = ln.get("kind", "text")
        text = ln["text"]
        new_block = (i == 0 or ln.get("para_start") or ln.get("starts_paragraph") or kind != prev_kind
                     or kind == "heading")
        if new_block:
            blocks.append([])
            if kind == "verse":
                blocks[-1].append("%verse")
        blocks[-1].append(("## " + text) if kind == "heading" else text)
        prev_kind = kind
    return "\n\n".join("\n".join(b) for b in blocks if b)


def plain_lines(lines: list[dict]) -> str:
    return "\n".join(l["text"] for l in lines)


# ------------------------------------------------------------------ one region, both witnesses

def dual_read(region: Image.Image, doc_settings: dict, *, region_name: str = "main", page_dpi: int | None = None,
              use_tesseract: bool = True, use_gemini: bool = True, reconcile: bool | None = None,
              gemini_allowed: bool = True) -> dict:
    a = tesseract_witness(region, page_dpi) if use_tesseract else {"status": "skipped", "lines": [], "warnings": [],
                                                                    "raw": "", "config": {}}
    if use_gemini and gemini_allowed:
        b = gemini_witness(region, doc_settings, region_name)
    else:
        why = "Gemini token budget for this document is exhausted" if use_gemini and not gemini_allowed else "not requested"
        b = {"status": "skipped", "lines": [], "warnings": [why] if use_gemini else [], "raw": [], "tokens_in": 0,
             "tokens_out": 0, "model": "", "config": {}}
    a_ok, b_ok = a["status"] == "ok" and a["lines"], b["status"] in ("ok", "partial") and b["lines"]
    coll = collate([l["text"] for l in a["lines"]], [l["text"] for l in b["lines"]]) if (a_ok and b_ok) else None

    if b_ok and (settings.scholarly_primary == "gemini" or not a_ok):
        primary, prim_lines = "gemini", b["lines"]
    elif a_ok:
        primary, prim_lines = "tesseract", a["lines"]
    else:
        primary, prim_lines = "", []

    proposals = []
    do_rec = settings.scholarly_reconcile if reconcile is None else reconcile
    if coll and do_rec and gemini_allowed and settings.gemini_enabled:
        proposals = _reconcile(region, coll, a, b)

    tokens = int(b.get("tokens_in", 0)) + int(b.get("tokens_out", 0)) + sum(p.get("tokens", 0) for p in proposals)
    return {"a": a, "b": b, "collation": coll or {}, "primary": primary,
            "working_text": lines_to_working_text(prim_lines), "plain_text": plain_lines(prim_lines),
            "proposals": proposals, "tokens": tokens}


def _reconcile(region: Image.Image, coll: dict, a: dict, b: dict) -> list[dict]:
    """Proposals for the most important disagreements, from an image of just the disputed printed line."""
    order = {"letter": 0, "omission": 1, "addition": 1, "line-omission": 1, "line-addition": 1, "accent": 2,
             "breathing": 2, "iota-subscript": 2, "diaeresis": 2, "diacritics": 2, "editorial-sign": 3,
             "punctuation": 4, "script-artifact": 5}
    items = [it for it in coll["items"] if it["severity"] == "substantive" and it.get("a") and it.get("b")]
    items.sort(key=lambda it: order.get(it["category"].split("+")[0], 6))
    out = []
    for it in items[: max(0, settings.scholarly_reconcile_max)]:
        box = _line_box(a, b, it)
        if not box:
            continue
        x0, y0, x1, y1 = box
        h = y1 - y0
        crop = region.crop((0, max(0, int(y0 - 0.45 * h)), region.width, min(region.height, int(y1 + 0.35 * h))))
        try:
            r = gemini_engine.reconcile(crop, it["a"]["text"], it["b"]["text"], seed_key=f"{it['id']}:{it['a']['text']}")
        except Exception as e:
            it["proposal"] = {"status": "failed", "error": str(e)[:200]}
            continue
        it["proposal"] = {"status": "proposed", "choice": r["choice"], "reading": r["reading"],
                          "evidence": r["evidence"], "model": r["model"], "source": "gemini line-image reconciliation"}
        it["status"] = "proposed"
        out.append({"item": it["id"], "tokens": r["tokens_in"] + r["tokens_out"], "raw": r["raw"],
                    "choice": r["choice"], "reading": r["reading"]})
    return out


def _line_box(a: dict, b: dict, it: dict):
    if it.get("a") and a["lines"]:
        li = it["a"]["line"]
        if 0 <= li < len(a["lines"]):
            return a["lines"][li]["bbox"]
    if it.get("b") and b["lines"]:
        li = it["b"]["line"]
        if 0 <= li < len(b["lines"]):
            return b["lines"][li].get("bbox")
    return None


# ------------------------------------------------------------------ page orchestration

def _doc_tokens(s, doc_id: str) -> int:
    return int(s.scalar(select(func.coalesce(func.sum(Page.tokens_in + Page.tokens_out), 0))
                        .where(Page.doc_id == doc_id)) or 0)


def snapshot(s, page: Page, reason: str) -> PageRevision | None:
    """Keep the page's current text before anything replaces it."""
    if not (page.body or page.right or page.notes):
        return None
    rev = PageRevision(page_id=page.id, reason=reason, body=page.body, right=page.right, notes=page.notes,
                       text_format=page.text_format or "prose", printed_page=page.printed_page, edited=page.edited,
                       reviewed=page.reviewed, engine=page.engine, witness_status=page.witness_status or "",
                       provenance=page.provenance)
    s.add(rev)
    return rev


def run_page(page_id: int, forced: str = "") -> None:
    with session_scope() as s:
        page = s.get(Page, page_id)
        if page is None:
            return
        doc = s.get(Document, page.doc_id)
        ds = dict(doc.settings or {})
        overrides = [(i, o) for i, o in s.execute(select(Page.index, Page.override)
                                                  .where(Page.doc_id == page.doc_id, Page.index <= page.index))]
        layout, layout_src = resolve(doc.layouts, page.index, page.override, overrides)
        index, doc_id = page.index, doc.id
        image_meta = dict(page.image_meta or {})
        budget_used = _doc_tokens(s, doc_id)

    img_path = storage.page_image_path(doc_id, index)
    if not img_path.exists():
        _fail(page_id, "Page image was purged to save space; re-upload to re-run OCR.")
        return
    with Image.open(img_path) as im:
        im.load()
        page_img = im.convert("L")

    page_dpi = image_meta.get("dpi")
    lossless = img_path.suffix.lower() == ".png"
    warnings: list[str] = []
    if not lossless:
        warnings.append("Page image is stored as JPEG (lossy). Re-import the file with the scholarly profile for a "
                        "lossless master.")
    budget = settings.gemini_doc_token_budget
    gemini_allowed = not (budget and budget_used >= budget)
    if not gemini_allowed:
        warnings.append(f"Gemini skipped: document token budget ({budget:,}) reached")
    use_t = forced in ("", "tesseract")
    use_g = forced in ("", "gemini")
    notes_wanted = ds.get("footnote_mode") == "separate"

    main_box = main_minus_notes(layout.get("main"), layout.get("notes"))
    main_img = crop_box(page_img, main_box)
    edges = edge_contacts(main_img)
    if edges:
        warnings.append(f"Main crop edge touches print on the {', '.join(edges)} side — a line or accent may be "
                        "clipped; check the crop.")
    if layout.get("notes") and layout.get("main") and main_box != layout.get("main"):
        warnings.append("The footnote box overlaps the main box; the overlap was removed from the main text.")

    mode = layout.get("mode", "single")
    regions: list[tuple[str, Image.Image]] = []
    if mode in ("columns", "facing"):
        w = main_img.width
        x = int(w * layout.get("split", 0.5))
        regions = [("left", main_img.crop((0, 0, max(1, x), main_img.height))),
                   ("right", main_img.crop((min(x, w - 1), 0, w, main_img.height)))]
    else:
        regions = [("main", main_img)]
    if notes_wanted and layout.get("notes"):
        regions.append(("notes", crop_box(page_img, layout["notes"])))

    run_id = uuid.uuid4().hex[:12]
    results = {}
    tokens = 0
    for name, reg in regions:
        res = dual_read(reg, ds, region_name="notes" if name == "notes" else "main", page_dpi=page_dpi,
                        use_tesseract=use_t, use_gemini=use_g, gemini_allowed=gemini_allowed and
                        not (budget and budget_used + tokens >= budget))
        results[name] = res
        tokens += res["tokens"]

    _store(page_id, run_id, results, mode, warnings, layout, layout_src, image_meta, lossless, forced)


def _fail(page_id: int, msg: str) -> None:
    with session_scope() as s:
        p = s.get(Page, page_id)
        if p is not None:
            p.status, p.error = "error", msg


def _witness_status(results: dict) -> str:
    stats = []
    for name, r in results.items():
        if name == "notes":
            continue
        a_ok = r["a"]["status"] == "ok" and r["a"]["lines"]
        b_ok = r["b"]["status"] in ("ok", "partial") and r["b"]["lines"]
        if a_ok and b_ok:
            stats.append(r["collation"].get("status", "engines-disagree"))
        elif a_ok or b_ok:
            stats.append("single-witness")
        else:
            stats.append("no-witness")
    for s in ("no-witness", "single-witness", "engines-disagree"):
        if s in stats:
            return s
    return "engines-agree" if stats else "no-witness"


def _store(page_id, run_id, results, mode, warnings, layout, layout_src, image_meta, lossless, forced):
    with session_scope() as s:
        page = s.get(Page, page_id)
        if page is None or page.status != "running":
            return
        snapshot(s, page, "before OCR re-run")
        tok_in = tok_out = 0
        for name, r in results.items():
            for eng, w in (("tesseract", r["a"]), ("gemini", r["b"])):
                if w["status"] == "skipped" and not w.get("warnings"):
                    continue
                raw = w.get("raw", "")
                s.add(Recognition(
                    page_id=page_id, run_id=run_id, engine=eng, region=name, status=w["status"],
                    model=w.get("model", "") if eng == "gemini" else "tesseract " + (w.get("config") or {}).get("lang", ""),
                    engine_version=(w.get("config") or {}).get("version", "") if eng == "tesseract" else
                    f"google-genai {_sdk_version()}",
                    config=w.get("config") or {}, raw=raw if isinstance(raw, str) else "\n".join(raw),
                    lines=[{k: v for k, v in l.items() if k != "raw_text" or v != l.get("text")} for l in w["lines"]],
                    text=plain_lines(w["lines"]), tokens_in=int(w.get("tokens_in", 0)),
                    tokens_out=int(w.get("tokens_out", 0)), latency_ms=int(w.get("latency_ms", 0)),
                    warnings=w.get("warnings") or []))
                tok_in += int(w.get("tokens_in", 0))
                tok_out += int(w.get("tokens_out", 0))
            for p in r["proposals"]:
                s.add(Recognition(page_id=page_id, run_id=run_id, engine="reconcile", region=name, status="ok",
                                  model=settings.scholarly_gemini_model, raw=p["raw"],
                                  config={"item": p["item"], "choice": p["choice"]}, text=p["reading"],
                                  tokens_out=p["tokens"]))
                tok_out += p["tokens"]

        body_regions = [n for n in ("main", "left", "right") if n in results]
        if mode == "facing":
            page.body = results["left"]["working_text"]
            page.right = results["right"]["working_text"]
        else:
            page.body = "\n\n".join(results[n]["working_text"] for n in body_regions if results[n]["working_text"])
            page.right = ""
        page.notes = results["notes"]["working_text"] if "notes" in results else ""

        coll = {n: results[n]["collation"] for n in results if results[n]["collation"]}
        highlights = []
        for n in body_regions + (["notes"] if "notes" in results else []):
            r = results[n]
            side = "b" if r["primary"] == "gemini" else "a"
            highlights += disagreement_words(r["collation"], side) if r["collation"] else []
            if r["primary"] == "gemini":
                highlights += [u for l in r["b"]["lines"] for u in l.get("uncertain", [])]
            elif r["primary"] == "tesseract":
                highlights += r["a"].get("low_conf", [])
        status = _witness_status(results)
        all_warn = list(warnings)
        for n, r in results.items():
            for eng, w in (("Tesseract", r["a"]), ("Gemini", r["b"])):
                if w["status"] not in ("ok", "skipped"):
                    all_warn.append(f"{eng} ({n}): {w['status']}")
                # informational notes stay in the recognition record; the page shows only what needs attention
                all_warn += [f"{eng} ({n}): {x}" for x in (w.get("warnings") or []) if "accent-strip" not in x]
        if status == "single-witness":
            all_warn.insert(0, "Only one engine produced a reading — this page has NOT been cross-checked.")
        prim = {n: results[n]["primary"] for n in results}
        main_prim = prim.get("main") or prim.get("left") or ""
        n_dis = sum(c.get("substantive", 0) for k, c in coll.items() if k != "notes")
        page.collation = {"run_id": run_id, "regions": coll, "primary": prim}
        page.witness_status = status
        page.text_format = "lines"
        page.profile = PROFILE
        page.low_conf = list(dict.fromkeys(nfc(h) for h in highlights if h))[:500]
        page.engine = "dual" if status in ("engines-agree", "engines-disagree") else (main_prim or "none")
        page.route_reason = {"engines-agree": "2 readings, no substantive disagreement",
                             "engines-disagree": f"2 readings, {n_dis} disagreement(s)",
                             "single-witness": "1 reading only — not cross-checked",
                             "no-witness": "no usable reading"}.get(status, status)
        t = results.get("main", results.get("left", {})).get("a", {})
        page.confidence = t.get("confidence")
        g = results.get("main", results.get("left", {})).get("b", {})
        page.printed_page = g.get("printed_page", "") or page.printed_page
        page.tokens_in += tok_in
        page.tokens_out += tok_out
        page.warning = "\n".join(dict.fromkeys(all_warn))[:4000]
        page.error = "" if status != "no-witness" else "Neither engine produced a usable reading."
        page.provenance = {
            "run_id": run_id, "profile": PROFILE, "forced_engine": forced or None,
            "layout": layout, "layout_source": layout_src, "image": {**image_meta, "lossless": lossless},
            "tesseract": t.get("config"), "gemini": {"model": g.get("model"), **(g.get("config") or {})},
            "segments": g.get("segments"), "primary": prim,
        }
        page.edited = False
        page.reviewed = False
        page.forced_engine = ""
        page.status = "done" if status != "no-witness" else "error"


def _sdk_version() -> str:
    try:
        from google import genai
        return getattr(genai, "__version__", "?")
    except Exception:
        return "?"
