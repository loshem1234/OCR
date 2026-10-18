from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OcrResult:
    body: str = ""
    right: str = ""          # facing-text right column
    notes: str = ""          # footnotes / apparatus
    low_conf: list[str] = field(default_factory=list)
    confidence: float | None = None
    printed_page: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    warning: str = ""
    word_count: int = 0


class EngineUnavailable(RuntimeError):
    pass
