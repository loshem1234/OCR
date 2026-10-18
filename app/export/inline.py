"""Tokenise the inline conventions: *italic*, **bold**, [^n] footnote refs, ⟦ref⟧ markers, page markers."""
from __future__ import annotations

import re

TOKEN_RE = re.compile(
    r"(?P<pg>(?P<pgv>[^]*))"
    r"|(?P<fn>\[\^(?P<fnv>[^\]\s]{1,8})\])"
    r"|(?P<ref>⟦(?P<refv>[^⟧]{0,40})⟧)"
    r"|(?P<b>\*\*(?P<bv>[^*\n]+?)\*\*)"
    r"|(?P<i>(?<![\w*])\*(?P<iv>[^*\n]+?)\*(?![\w*]))"
    r"|(?P<br>\n)"
)

SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def tokens(text: str) -> list[tuple[str, str]]:
    """Return a list of (kind, value): text | italic | bold | fn | ref | page | br."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in TOKEN_RE.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        kind = m.lastgroup if m.lastgroup in ("br",) else next(
            k for k in ("pg", "fn", "ref", "b", "i") if m.group(k) is not None
        )
        val = {"pg": m.group("pgv"), "fn": m.group("fnv"), "ref": m.group("refv"),
               "b": m.group("bv"), "i": m.group("iv"), "br": ""}[kind]
        out.append(({"pg": "page", "b": "bold", "i": "italic"}.get(kind, kind), val))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out


def superscript(v: str) -> str:
    return v.translate(SUPERSCRIPT) if v.isdigit() else f"[{v}]"
