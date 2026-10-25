"""Gemini vision transcription for hard pages, polytonic Greek, Hebrew, Arabic and marginal references."""
from __future__ import annotations

import io
import json
import logging
import random
import re
import threading
import time

from PIL import Image

from ..config import settings
from .base import EngineUnavailable, OcrResult
from .tesseract_engine import TESS_LANGS

log = logging.getLogger("gemini")

_client = None
_client_lock = threading.Lock()

# Optional request features a given model has rejected. Keyed by model so a rejection for one model
# never changes requests to another; guarded by a lock because pages are read in parallel threads.
_unsupported: dict[str, set[str]] = {}
_feature_lock = threading.Lock()


def _supports(model: str, feature: str) -> bool:
    with _feature_lock:
        return feature not in _unsupported.get(model, set())


def _mark_unsupported(model: str, feature: str) -> None:
    with _feature_lock:
        _unsupported.setdefault(model, set()).add(feature)
    log.warning("model %s rejected %s; continuing without it for this model", model, feature)


def _rejected_feature(err) -> str:
    """Name the optional feature a 400 error complains about, if any."""
    msg = str(err).lower()
    for feat, words in (("thinking", ("think",)), ("media_resolution", ("media_resolution", "media resolution")),
                        ("seed", ("seed",))):
        if any(w in msg for w in words):
            return feat
    return ""

SCHEMA = {
    "type": "object",
    "properties": {
        "printed_page": {"type": "string", "description": "Page number printed on the page, or empty string."},
        "body": {"type": "string", "description": "Main text in the markdown conventions described."},
        "footnotes": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["printed_page", "body", "footnotes", "uncertain"],
}


def _get_client():
    global _client
    if not settings.gemini_enabled:
        raise EngineUnavailable("GEMINI_API_KEY is not set")
    with _client_lock:
        if _client is None:
            from google import genai
            _client = genai.Client(api_key=settings.gemini_api_key)
        return _client


def _encode(im: Image.Image) -> bytes:
    im = im.convert("L")
    m = settings.gemini_max_side
    if max(im.size) > m:
        s = m / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def build_prompt(doc_settings: dict, *, columns: bool, has_notes_image: bool, footnotes_wanted: bool) -> str:
    langs = ", ".join(TESS_LANGS.get(l, l) for l in doc_settings.get("languages", [])) or "unspecified"
    hint = (doc_settings.get("hint") or "").strip()
    refs = doc_settings.get("preserve_refs")
    rules = [
        "You are an expert transcriber of printed scholarly books, critical editions and old prints. "
        "Produce an exact diplomatic transcription of the supplied page region.",
        f"Expected languages: {langs}." + (f" Context from the editor: {hint}" if hint else ""),
        "RULES:",
        "1. Transcribe exactly what is printed. Never translate, summarise, modernise, normalise spelling, or "
        "correct the text. Preserve polytonic Greek precisely (accents, breathings, iota subscript, diaeresis, "
        "coronis); keep Hebrew niqqud and Arabic vowel signs when printed. Write long s as s. Output Unicode NFC.",
        "2. Join the lines of prose into paragraphs and separate paragraphs with one blank line. Rejoin words "
        "hyphenated at a line end. For verse, keep each verse line on its own line (single newline) and separate "
        "stanzas or speeches with a blank line.",
        "3. Mark headings with '## ' (use '# ' only for a major title such as a book or part title).",
        "4. Use *italic* and **bold** only where the print clearly uses them. Render small caps as normal text.",
        "5. Leave out running heads, printed page numbers, signature marks and catchwords. Put the printed page "
        "number (as printed, e.g. '42' or 'xvii') into printed_page, or an empty string if there is none.",
    ]
    if footnotes_wanted:
        rules.append(
            "6. Footnote reference markers in the main text become [^X] where X is the printed marker (1, 2, a, *). "
            "Footnote text printed at the foot of the page, and any critical apparatus, goes into the footnotes "
            "array (one entry per note, each beginning with its marker) and never into body."
        )
    else:
        rules.append(
            "6. Footnotes and critical apparatus are not wanted: leave their text out entirely, and leave footnote "
            "reference markers out of the main text. Return an empty footnotes array."
        )
    if refs:
        rules.append(
            "7. PRESERVE REFERENCE NUMBERS: marginal or inline reference numbers (Stephanus pages such as 17a, "
            "Bekker numbers such as 1094a5, chapter/section numbers, verse numbers, line numbers printed every "
            "few lines) must be kept as inline markers in double brackets, e.g. ⟦17a⟧ or ⟦1094a5⟧ or ⟦25⟧, placed "
            "at the start of the word where the marked line or section begins."
        )
    else:
        rules.append("7. Ignore line numbers and reference numbers printed in the margins.")
    rules.append(
        "8. List every word you could not read with confidence, exactly as you transcribed it, in uncertain. "
        "Write [illegible] for anything you cannot read at all. If the region is blank or only an illustration, "
        "return an empty body (transcribe a printed caption, if any, as a paragraph)."
    )
    if columns:
        rules.append("9. The main region is printed in two columns: transcribe the whole left column, then the right.")
    if has_notes_image:
        rules.append(
            "10. The second image is the page's footnote region: transcribe it only into the footnotes array."
        )
    rules.append("Return only JSON matching the schema.")
    return "\n".join(rules)


def _config(model: str | None = None, *, schema: dict | None = None, thinking_level: str | None = None,
            media_resolution: str | None = None):
    from google.genai import types

    model = model or settings.gemini_model
    kw: dict = {
        "temperature": 0.0,
        "response_mime_type": "application/json",
        "response_json_schema": schema or SCHEMA,
    }
    if _supports(model, "thinking"):
        if settings.gemini_thinking_budget != "":
            try:
                kw["thinking_config"] = types.ThinkingConfig(thinking_budget=int(settings.gemini_thinking_budget))
            except ValueError:
                pass
        else:
            level = settings.gemini_thinking_level if thinking_level is None else thinking_level
            if level:
                kw["thinking_config"] = types.ThinkingConfig(thinking_level=level)
    mr = settings.gemini_media_resolution if media_resolution is None else media_resolution
    if mr and _supports(model, "media_resolution"):
        kw["media_resolution"] = mr
    if settings.gemini_seed != "" and _supports(model, "seed"):
        try:
            kw["seed"] = int(settings.gemini_seed)
        except ValueError:
            pass
    return types.GenerateContentConfig(**kw)


def _parse(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Gemini returned non-object JSON")
    return data


def transcribe(main: Image.Image, doc_settings: dict, *, notes: Image.Image | None = None,
               columns: bool = False, footnotes_wanted: bool = True) -> OcrResult:
    from google.genai import errors, types

    client = _get_client()
    prompt = build_prompt(doc_settings, columns=columns, has_notes_image=notes is not None,
                          footnotes_wanted=footnotes_wanted)
    contents: list = ["MAIN TEXT REGION:", types.Part.from_bytes(data=_encode(main), mime_type="image/jpeg")]
    if notes is not None:
        contents += ["FOOTNOTE REGION:", types.Part.from_bytes(data=_encode(notes), mime_type="image/jpeg")]
    contents.append(prompt)

    last_err: Exception | None = None
    for attempt in range(6):
        try:
            resp = client.models.generate_content(model=settings.gemini_model, contents=contents, config=_config(settings.gemini_model))
            finish = ""
            if resp.candidates:
                finish = str(getattr(resp.candidates[0], "finish_reason", "") or "")
            if not resp.text:
                raise RuntimeError(f"Gemini returned no text (finish reason: {finish or 'unknown'})")
            data = _parse(resp.text)
            um = resp.usage_metadata
            notes_list = [str(n).strip() for n in data.get("footnotes") or [] if str(n).strip()]
            return OcrResult(
                body=str(data.get("body") or ""),
                notes="\n\n".join(notes_list),
                low_conf=[str(w) for w in (data.get("uncertain") or []) if str(w).strip()][:300],
                printed_page=str(data.get("printed_page") or "").strip()[:40],
                tokens_in=int(getattr(um, "prompt_token_count", 0) or 0),
                tokens_out=int((getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)),
            )
        except errors.APIError as e:
            last_err = e
            code = getattr(e, "code", 0) or 0
            feat = _rejected_feature(e) if code == 400 else ""
            if feat and _supports(settings.gemini_model, feat):
                _mark_unsupported(settings.gemini_model, feat)
                continue
            if code in (408, 429, 500, 502, 503, 504):
                time.sleep(min(60, 2 ** (attempt + 1)) + random.random())
                continue
            raise
        except (json.JSONDecodeError, ValueError) as e:
            last_err = e
            time.sleep(1 + attempt)
            continue
    raise RuntimeError(f"Gemini failed after retries: {last_err}")


# =============================================================================================
# Scholarly Greek profile: strict diplomatic line transcription (no formatting, no reconstruction)
# =============================================================================================

class InvalidResponse(ValueError):
    pass


LINES_SCHEMA = {
    "type": "object",
    "properties": {
        "printed_page": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "text": {"type": "string"},
                    "starts_paragraph": {"type": "boolean"},
                    "kind": {"type": "string", "enum": ["text", "heading", "verse"]},
                    "uncertain": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["n", "text", "starts_paragraph", "kind", "uncertain"],
            },
        },
        "complete": {"type": "boolean"},
        "omitted": {"type": "array", "items": {"type": "string"}},
        "illegible": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["printed_page", "lines", "complete", "omitted", "illegible"],
}

RECONCILE_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": ["1", "2", "neither", "ambiguous"]},
        "reading": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["choice", "reading", "evidence"],
}


def scholarly_prompt(doc_settings: dict, *, region: str, part: int = 1, parts: int = 1) -> str:
    hint = (doc_settings.get("hint") or "").strip()
    where = (f"This image is part {part} of {parts} of the selected region, cut only between printed lines."
             if parts > 1 else "This image is the selected region.")
    if region == "notes":
        what = ("The region is the critical apparatus / footnote area of the page. Transcribe its printed lines "
                "exactly, including sigla, line references, editors' names and separators such as ‖ or |.")
    else:
        what = ("The region is the main text of the page. Transcribe only the main Greek text. Do NOT transcribe "
                "footnotes, critical apparatus, marginal annotations, running heads or page numbers, even if part of "
                "them is visible; list anything you left out in `omitted`.")
    refs = ("Marginal reference numbers (chapter/section/line numbers) are wanted: write each one as ⟦number⟧ at the "
            "start of the line it stands beside."
            if doc_settings.get("preserve_refs") else
            "Do not transcribe marginal line, section or chapter numbers; mention them in `omitted`.")
    return "\n".join([
        "You are performing diplomatic transcription of a printed Greek critical edition.",
        where,
        what,
        "Copy each Greek character exactly as printed, including accent, breathing, diaeresis, iota subscript, "
        "coronis, elision mark and punctuation (· ; , .).",
        "Preserve editorial brackets and special symbols exactly as printed: ⟨ ⟩ [ ] ( ) † * and any others.",
        "Do not modernize spelling. Do not correct unusual grammatical forms. Do not substitute an expected reading "
        "for a visually uncertain reading. Do not infer omitted or damaged text from context. Do not paraphrase, "
        "translate, summarize or improve the Greek. Do not silently insert words. Syntactic plausibility must never "
        "override what the ink shows; rare or irregular forms must be kept exactly as printed.",
        "Return one entry per printed line, top to bottom, numbered from 1. Each entry's text is exactly that one "
        "printed line: never join lines, never rejoin a word hyphenated at a line end (keep the printed hyphen), "
        "never move words between lines. starts_paragraph is true when the line begins a new paragraph (indented or "
        "after extra space). kind is 'heading' for a heading, 'verse' for a line of verse, otherwise 'text'.",
        "Use no Markdown, HTML or commentary inside text.",
        "In each line's `uncertain`, list exactly as you transcribed them any words containing a letter or diacritic "
        "you could not read with confidence. If a stretch is completely illegible, write [illegible] at that position "
        "and describe it in `illegible`.",
        refs,
        "Set complete to false if any line of the main text was not transcribed, and say why in `omitted`. Put a "
        "printed page number, if visible, in printed_page (as printed), otherwise an empty string.",
        f"Context from the editor (for orientation only, never as a source of readings): {hint}" if hint else "",
        "Return only JSON matching the schema.",
    ]).strip()


def _encode_png(im: Image.Image, max_side: int = 6000) -> bytes:
    im = im.convert("L")
    if max(im.size) > max_side:  # only for extraordinary regions; segments are normally far smaller
        s = max_side / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def validate_lines(data, est_lines: int = 0) -> tuple[list[dict], list[str]]:
    """Strict check of a scholarly response. Raises InvalidResponse; returns (lines, warnings)."""
    from .greek import hard_artifacts, mixed_script_words, nfc

    if not isinstance(data, dict):
        raise InvalidResponse("response is not a JSON object")
    for k in ("printed_page", "lines", "complete", "omitted", "illegible"):
        if k not in data:
            raise InvalidResponse(f"missing field '{k}'")
    lines = data["lines"]
    if not isinstance(lines, list):
        raise InvalidResponse("'lines' is not a list")
    out, warnings = [], []
    for i, ln in enumerate(lines):
        if not isinstance(ln, dict) or not isinstance(ln.get("text"), str):
            raise InvalidResponse(f"line {i + 1} is malformed")
        if ln.get("n") != i + 1:
            raise InvalidResponse(f"line numbering broken at entry {i + 1} (got {ln.get('n')!r})")
        text = ln["text"]
        if "\n" in text or "\r" in text:
            raise InvalidResponse(f"line {i + 1} contains a line break (lines must not be joined or split)")
        bad = hard_artifacts(text)
        if bad:
            raise InvalidResponse(f"line {i + 1} contains non-transcription content: {', '.join(bad)}")
        kind = ln.get("kind", "text")
        if kind not in ("text", "heading", "verse"):
            raise InvalidResponse(f"line {i + 1} has unknown kind {kind!r}")
        unc = ln.get("uncertain") or []
        if not isinstance(unc, list):
            raise InvalidResponse(f"line {i + 1} 'uncertain' is not a list")
        mixed = mixed_script_words(text)
        if mixed:
            warnings.append(f"line {i + 1}: mixed-script word(s) {', '.join(mixed[:3])}")
        out.append({"n": i + 1, "text": nfc(text).strip(), "raw_text": text,
                    "starts_paragraph": bool(ln.get("starts_paragraph")), "kind": kind,
                    "uncertain": [nfc(str(u)) for u in unc if str(u).strip()]})
    if hard_artifacts(str(data.get("printed_page", ""))):
        raise InvalidResponse("printed_page contains non-transcription content")
    if est_lines and not out:
        raise InvalidResponse(f"no lines returned although about {est_lines} printed lines are visible")
    if est_lines >= 3 and len(out) < 0.5 * est_lines:
        raise InvalidResponse(f"only {len(out)} lines returned for about {est_lines} printed lines")
    if est_lines and len(out) != est_lines:
        warnings.append(f"line count {len(out)} differs from the {est_lines} lines detected in the image")
    if not data.get("complete", True):
        warnings.append("model reports incomplete transcription: " + "; ".join(map(str, data.get("omitted") or []))[:300])
    if data.get("illegible"):
        warnings.append("illegible: " + "; ".join(map(str, data["illegible"]))[:300])
    if any(not l["text"] for l in out):
        warnings.append("empty line(s) returned")
    return out, warnings


def _call(model: str, contents: list, config_kw: dict) -> tuple[str, dict]:
    """One request with transient-error retries and per-model optional-feature fallback."""
    from google.genai import errors

    client = _get_client()
    last = None
    for attempt in range(6):
        try:
            t0 = time.time()
            resp = client.models.generate_content(model=model, contents=_parts(model, contents),
                                                  config=_config(model, **config_kw))
            um = resp.usage_metadata
            meta = {"latency_ms": int((time.time() - t0) * 1000),
                    "tokens_in": int(getattr(um, "prompt_token_count", 0) or 0),
                    "tokens_out": int((getattr(um, "candidates_token_count", 0) or 0)
                                      + (getattr(um, "thoughts_token_count", 0) or 0)),
                    "finish_reason": str(getattr(resp.candidates[0], "finish_reason", "") if resp.candidates else ""),
                    "features": sorted(f for f in ("thinking", "media_resolution", "seed", "part_media_resolution")
                                       if _supports(model, f))}
            if not resp.text:
                raise InvalidResponse(f"empty response (finish reason: {meta['finish_reason'] or 'unknown'})")
            return resp.text, meta
        except errors.APIError as e:
            last = e
            code = getattr(e, "code", 0) or 0
            feat = _rejected_feature(e) if code == 400 else ""
            if feat == "media_resolution" and _supports(model, "part_media_resolution"):
                _mark_unsupported(model, "part_media_resolution")
                continue
            if feat and _supports(model, feat):
                _mark_unsupported(model, feat)
                continue
            if code in (408, 429, 500, 502, 503, 504):
                time.sleep(min(60, 2 ** (attempt + 1)) + random.random())
                continue
            raise
    raise RuntimeError(f"Gemini request failed after retries: {last}")


def _parts(model: str, contents: list) -> list:
    """Turn ('image', PIL) markers into Parts, with per-part media resolution where the model accepts it."""
    from google.genai import types

    out = []
    for c in contents:
        if isinstance(c, tuple) and c[0] == "image":
            data = _encode_png(c[1])
            res = settings.scholarly_media_resolution
            if res and _supports(model, "part_media_resolution"):
                out.append(types.Part.from_bytes(data=data, mime_type="image/png", media_resolution=res))
            else:
                out.append(types.Part.from_bytes(data=data, mime_type="image/png"))
        else:
            out.append(c)
    return out


def transcribe_segments(segments: list[tuple[Image.Image, int]], doc_settings: dict, *, region: str = "main",
                        model: str | None = None) -> dict:
    """Transcribe each line-safe strip independently. Returns a witness dict:
    {status, lines, raw (list of raw JSON texts), printed_page, warnings, tokens_in, tokens_out, latency_ms, model,
     config}. status: ok | partial | invalid | failed."""
    model = model or settings.scholarly_gemini_model
    witness = {"status": "ok", "lines": [], "raw": [], "printed_page": "", "warnings": [], "tokens_in": 0,
               "tokens_out": 0, "latency_ms": 0, "model": model,
               "config": {"thinking_level": settings.scholarly_thinking_level,
                          "media_resolution": settings.scholarly_media_resolution, "seed": settings.gemini_seed,
                          "segments": len(segments), "segment_lines": settings.scholarly_segment_lines,
                          "image_format": "png"}}
    failed = 0
    for si, (img, est) in enumerate(segments, start=1):
        prompt = scholarly_prompt(doc_settings, region=region, part=si, parts=len(segments))
        note = ""
        seg_lines = None
        for attempt in range(1 + max(0, settings.scholarly_invalid_retries)):
            contents = [("image", img), prompt + (f"\n\nYour previous answer was rejected: {note}. "
                                                  "Return the JSON again, transcribing exactly what is printed." if note else "")]
            try:
                text, meta = _call(model, contents, {"schema": LINES_SCHEMA,
                                                     "thinking_level": settings.scholarly_thinking_level,
                                                     "media_resolution": ""})
            except InvalidResponse as e:
                note = str(e)
                witness["warnings"].append(f"segment {si}: {e}")
                continue
            except Exception as e:  # network / quota / auth
                witness["warnings"].append(f"segment {si}: request failed: {str(e)[:200]}")
                break
            witness["raw"].append(text)
            witness["tokens_in"] += meta["tokens_in"]
            witness["tokens_out"] += meta["tokens_out"]
            witness["latency_ms"] += meta["latency_ms"]
            witness["config"]["features"] = meta["features"]
            try:
                data = _parse(text)
                seg_lines, warns = validate_lines(data, est)
            except (InvalidResponse, ValueError, json.JSONDecodeError) as e:
                note = str(e)[:200]
                witness["warnings"].append(f"segment {si} attempt {attempt + 1} rejected: {note}")
                continue
            witness["warnings"].extend(f"segment {si}: {w}" for w in warns)
            if data.get("printed_page") and not witness["printed_page"]:
                witness["printed_page"] = str(data["printed_page"]).strip()[:40]
            break
        if seg_lines is None:
            failed += 1
            witness["warnings"].append(f"segment {si}: no valid transcription — its lines are missing from this witness")
            continue
        base = len(witness["lines"])
        for ln in seg_lines:
            ln["n"] = base + ln["n"]
            ln["segment"] = si
            witness["lines"].append(ln)
    if failed == len(segments):
        witness["status"] = "failed" if not witness["raw"] else "invalid"
    elif failed:
        witness["status"] = "partial"
    return witness


def reconcile(line_image: Image.Image, reading_a: str, reading_b: str, *, seed_key: str = "",
              model: str | None = None) -> dict:
    """Ask the model what is printed at one disputed place, given only that printed line's image and the two
    candidate readings (order shuffled deterministically to avoid position bias). Returns a *proposal*."""
    import hashlib

    model = model or settings.scholarly_gemini_model
    flip = int(hashlib.sha1(seed_key.encode()).hexdigest(), 16) % 2 == 1
    r1, r2 = (reading_b, reading_a) if flip else (reading_a, reading_b)
    prompt = "\n".join([
        "The image shows one printed line of a Greek critical edition.",
        "Two independent OCR systems disagree about one place in this line:",
        f"Reading 1: «{r1}»",
        f"Reading 2: «{r2}»",
        "Look only at the image and report exactly what is printed at that place, character by character, with every "
        "accent, breathing, iota subscript, diaeresis, punctuation mark and bracket as printed.",
        "Grammar, sense and how familiar a form is are irrelevant: report only what the ink shows.",
        "choice: '1' or '2' if that reading matches the print exactly; 'neither' if neither does (then give the printed "
        "form in reading); 'ambiguous' if the print itself is genuinely unclear. evidence: one short sentence about the "
        "visible marks. Return only JSON.",
    ])
    text, meta = _call(model, [("image", line_image), prompt], {"schema": RECONCILE_SCHEMA, "media_resolution": ""})
    data = _parse(text)
    from .greek import hard_artifacts, nfc
    choice = str(data.get("choice", ""))
    if choice not in ("1", "2", "neither", "ambiguous"):
        raise InvalidResponse(f"bad choice {choice!r}")
    reading = nfc(str(data.get("reading", "")))
    if hard_artifacts(reading):
        raise InvalidResponse("reading contains non-transcription content")
    mapped = {"1": "b" if flip else "a", "2": "a" if flip else "b"}.get(choice, choice)
    if mapped == "a":
        reading = nfc(reading_a)
    elif mapped == "b":
        reading = nfc(reading_b)
    return {"choice": mapped, "reading": reading, "evidence": str(data.get("evidence", ""))[:300], "model": model,
            "raw": text, **{k: meta[k] for k in ("tokens_in", "tokens_out", "latency_ms")}}
