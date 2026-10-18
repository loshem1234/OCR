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
_feature_state = {"thinking": True, "media_resolution": True}

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


def _config():
    from google.genai import types

    kw: dict = {
        "temperature": 0.0,
        "response_mime_type": "application/json",
        "response_json_schema": SCHEMA,
    }
    if _feature_state["thinking"]:
        if settings.gemini_thinking_budget != "":
            try:
                kw["thinking_config"] = types.ThinkingConfig(thinking_budget=int(settings.gemini_thinking_budget))
            except ValueError:
                pass
        elif settings.gemini_thinking_level:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=settings.gemini_thinking_level)
    if _feature_state["media_resolution"] and settings.gemini_media_resolution:
        kw["media_resolution"] = settings.gemini_media_resolution
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
            resp = client.models.generate_content(model=settings.gemini_model, contents=contents, config=_config())
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
            msg = str(e).lower()
            if code == 400 and _feature_state["thinking"] and "think" in msg:
                _feature_state["thinking"] = False
                log.warning("model rejected thinking config; retrying without it")
                continue
            if code == 400 and _feature_state["media_resolution"] and "media" in msg:
                _feature_state["media_resolution"] = False
                log.warning("model rejected media_resolution; retrying without it")
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
