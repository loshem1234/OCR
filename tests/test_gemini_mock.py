"""Gemini path with a fake client: hybrid routing, JSON parsing, config fallback, Tesseract fallback."""
from __future__ import annotations

import json
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.ocr import gemini_engine, router


class FakeModels:
    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        b = self.behaviours.pop(0)
        if isinstance(b, Exception):
            raise b
        return SimpleNamespace(
            text=json.dumps(b),
            candidates=[SimpleNamespace(finish_reason="STOP")],
            usage_metadata=SimpleNamespace(prompt_token_count=1200, candidates_token_count=300, thoughts_token_count=20),
        )


def _img(text="λόγος καὶ νοῦς ἐστιν ἡ ἀρχή"):
    im = Image.new("L", (1400, 500), 255)
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf", 44)
    d.text((60, 60), text, font=f, fill=0)
    d.text((60, 160), "In the beginning was the account and the mind.", font=f, fill=0)
    return im


def test_transcribe_parses_and_falls_back_on_config(monkeypatch):
    from google.genai import errors

    gemini_engine._unsupported.clear()
    fake = FakeModels([
        errors.ClientError(400, {"error": {"code": 400, "message": "thinking_level is not supported for this model", "status": "INVALID_ARGUMENT"}}),
        {"printed_page": "17", "body": "## Α\n\nλόγος [^1] ⟦17a⟧", "footnotes": ["1 cf. Plat."], "uncertain": ["λόγος"]},
    ])
    monkeypatch.setattr(gemini_engine, "_get_client", lambda: SimpleNamespace(models=fake))
    res = gemini_engine.transcribe(_img(), {"languages": ["grc"], "preserve_refs": True})
    assert res.printed_page == "17"
    assert res.notes == "1 cf. Plat."
    assert res.low_conf == ["λόγος"]
    assert res.tokens_in == 1200 and res.tokens_out == 320
    assert "thinking" in gemini_engine._unsupported[settings.gemini_model]
    assert not gemini_engine._unsupported.get("some-other-model")  # a rejection never leaks to other models
    assert fake.calls[1].config.thinking_config is None
    prompt = fake.calls[1].contents[-1]
    assert "⟦17a⟧" in prompt and "Ancient Greek" in prompt


def test_hybrid_routes_greek_to_gemini_and_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "fake")
    reg = {"mode": "single", "main": _img(), "notes": None, "left": None, "right": None}
    langs = ["eng", "grc"]

    tess = router._tess(reg, langs, False)
    assert router._needs_gemini(tess, langs) == "Greek detected"

    # English-only page with good print stays on Tesseract.
    reg_en = {**reg, "main": _img("Every manifold in some way participates unity.")}
    tess_en = router._tess(reg_en, ["eng"], False)
    assert router._needs_gemini(tess_en, ["eng"]) == ""
