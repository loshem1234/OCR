"""Assemble per-page OCR text into one continuous document model."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..db import Document, Page
from ..ocr.textutil import LINE_HYPHENS, ends_sentence, starts_lower
from .options import ExportOptions

PG_OPEN, PG_CLOSE = "", ""
FN_RE = re.compile(r"\[\^([^\]\s]{1,8})\]")
REF_RE = re.compile(r"⟦([^⟧]{0,40})⟧")


@dataclass
class Block:
    kind: str                 # heading | para | marker | facing | notes
    text: str = ""
    level: int = 0
    label: str = ""           # page label for notes blocks
    left: list["Block"] = field(default_factory=list)
    right: list["Block"] = field(default_factory=list)


@dataclass
class Assembled:
    blocks: list[Block]
    notes: list[tuple[str, str]]   # (page label, notes text) for end-notes
    page_count: int


def page_label(p: Page, opts: ExportOptions) -> str:
    if opts.use_printed and p.printed_page:
        return p.printed_page
    return str(p.index + opts.first_number)


def marker(label: str) -> str:
    return f"{PG_OPEN}{label}{PG_CLOSE}"


def parse_blocks(md: str) -> list[Block]:
    out = []
    for chunk in re.split(r"\n\s*\n", (md or "").strip()):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", chunk, re.S)
        if m:
            out.append(Block("heading", m.group(2).replace("\n", " ").strip(), level=len(m.group(1))))
        else:
            out.append(Block("para", "\n".join(l.rstrip() for l in chunk.split("\n"))))
    return out


def _filter(blocks: list[Block], drop_fn: bool, hide_refs: bool) -> list[Block]:
    for b in blocks:
        if drop_fn:
            b.text = FN_RE.sub("", b.text)
        if hide_refs:
            b.text = re.sub(r"\s{2,}", " ", REF_RE.sub("", b.text)).strip()
    return [b for b in blocks if b.text.strip()]


def assemble(doc: Document, opts: ExportOptions) -> Assembled:
    ds = doc.settings or {}
    separate = ds.get("footnote_mode") == "separate" and opts.include_notes
    drop_fn = not separate
    hide_refs = opts.refs == "hide"

    blocks: list[Block] = []
    notes: list[tuple[str, str]] = []
    can_join = False
    used = 0

    for p in doc.pages:
        if p.skip or not (p.body or p.right or p.notes):
            continue
        used += 1
        label = page_label(p, opts)
        mk = marker(label) if opts.page_markers else ""
        body = _filter(parse_blocks(p.body), drop_fn, hide_refs)

        if p.right:
            if mk:
                blocks.append(Block("marker", mk))
            blocks.append(Block("facing", left=body, right=_filter(parse_blocks(p.right), drop_fn, hide_refs)))
            can_join = False
        else:
            first = body[0] if body else None
            prev = blocks[-1] if blocks else None
            if (opts.join_pages and can_join and first and first.kind == "para" and prev and prev.kind == "para"
                    and starts_lower(first.text)):
                if len(prev.text) > 1 and prev.text[-1] in LINE_HYPHENS and prev.text[-2].isalpha():
                    word, _, rest = first.text.partition(" ")
                    prev.text = prev.text[:-1] + word + (f" {mk}" if mk else "") + (f" {rest}" if rest else "")
                else:
                    prev.text = prev.text + (f" {mk} " if mk else " ") + first.text
                body = body[1:]
            elif mk:
                if first and first.kind == "para":
                    first.text = f"{mk} {first.text}"
                else:
                    blocks.append(Block("marker", mk))
            blocks.extend(body)
            last = blocks[-1] if blocks else None
            can_join = bool(last and last.kind == "para" and not ends_sentence(last.text))

        if separate and p.notes.strip():
            n = p.notes.strip()
            if hide_refs:
                n = REF_RE.sub("", n)
            if opts.notes_placement == "page":
                blocks.append(Block("notes", n, label=label))
                can_join = False
            else:
                notes.append((label, n))

    return Assembled(blocks=blocks, notes=notes, page_count=used)
