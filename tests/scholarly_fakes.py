"""A fake Gemini that behaves like a careful reader of synthetic pages, for testing without an API key.

For each image strip it receives, it detects the printed lines (same band detector the app uses) and
returns the next ground-truth lines — optionally with deliberate errors — in the scholarly JSON schema.
It records everything it was sent so tests can prove it never saw Tesseract output.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

from PIL import Image

from app.ocr.segment import line_bands


class FakeScholarModels:
    def __init__(self, gt_lines: list[str], *, edits: dict[int, str] | None = None, responses: list | None = None,
                 reconcile_choice: str = "1"):
        self.gt = list(gt_lines)
        self.pos = 0
        self.edits = edits or {}           # gt line index -> replacement text (deliberate misreading)
        self.responses = list(responses or [])  # scripted raw responses / exceptions (take precedence)
        self.calls = []
        self.reconcile_choice = reconcile_choice

    def generate_content(self, model, contents, config):
        texts = [c for c in contents if isinstance(c, str)]
        images = [c for c in contents if not isinstance(c, str)]
        self.calls.append(SimpleNamespace(model=model, texts=texts, images=images, config=config))
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return _resp(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False))
        prompt = "\n".join(texts)
        if "Reading 1" in prompt:
            return _resp(json.dumps({"choice": self.reconcile_choice, "reading": "", "evidence": "visible marks"}))
        img = Image.open(io.BytesIO(images[0].inline_data.data))
        n = len(line_bands(img))
        lines = []
        for k in range(n):
            i = self.pos + k
            if i >= len(self.gt):
                break
            lines.append({"n": k + 1, "text": self.edits.get(i, self.gt[i]), "starts_paragraph": k == 0,
                          "kind": "text", "uncertain": []})
        self.pos += len(lines)
        return _resp(json.dumps({"printed_page": "", "lines": lines, "complete": True, "omitted": [],
                                 "illegible": []}, ensure_ascii=False))


def _resp(text: str):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason="STOP")],
        usage_metadata=SimpleNamespace(prompt_token_count=1100, candidates_token_count=400, thoughts_token_count=0),
    )
