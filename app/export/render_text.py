"""Plain text and Markdown renderers."""
from __future__ import annotations

import html

from ..db import Document
from .assemble import Assembled, Block
from ..ocr.greek import greek_ratio
from .inline import superscript, tokens
from .options import ExportOptions


# ------------------------------------------------------------------ plain text

def _txt_inline(text: str, opts: ExportOptions) -> str:
    out = []
    for kind, v in tokens(text, opts.markup):
        if kind in ("text", "italic", "bold"):
            out.append(v)
        elif kind == "page":
            out.append(opts.fmt_marker(v))
        elif kind == "fn":
            out.append(superscript(v))
        elif kind == "ref":
            out.append(f"⟦{v}⟧")
        elif kind == "br":
            out.append("\n")
    return "".join(out)


def _txt_blocks(blocks: list[Block], opts: ExportOptions) -> list[str]:
    parts = []
    for b in blocks:
        if b.kind == "heading":
            t = _txt_inline(b.text, opts)
            # never upper-case Greek: str.upper() turns ᾳ into ΑΙ and drops accents
            parts.append(t.upper() if b.level == 1 and greek_ratio(t) == 0 else t)
        elif b.kind in ("para", "marker"):
            parts.append(_txt_inline(b.text, opts))
        elif b.kind == "facing":
            parts.extend(_txt_blocks(b.left, opts))
            parts.append("  ‖  ")
            parts.extend(_txt_blocks(b.right, opts))
        elif b.kind == "notes":
            parts.append("—— Notes " + opts.fmt_marker(b.label) + " ——\n" + _txt_inline(b.text, opts))
    return parts


def render_txt(a: Assembled, doc: Document, opts: ExportOptions) -> str:
    parts = _txt_blocks(a.blocks, opts)
    if a.notes:
        parts.append("\n" + "=" * 24 + "\nNOTES\n" + "=" * 24)
        for label, text in a.notes:
            parts.append(opts.fmt_marker(label) + "\n" + _txt_inline(text, opts))
    return "\n\n".join(parts).strip() + "\n"


# ------------------------------------------------------------------ markdown

def _md_inline(text: str, opts: ExportOptions) -> str:
    out = []
    for kind, v in tokens(text, opts.markup):
        if kind == "text":
            # in literal mode a printed * must stay a * in every Markdown viewer
            out.append(v if opts.markup else v.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_"))
        elif kind == "italic":
            out.append(f"*{v}*")
        elif kind == "bold":
            out.append(f"**{v}**")
        elif kind == "page":
            out.append(opts.fmt_marker(v))
        elif kind == "fn":
            out.append(f"<sup>{html.escape(v)}</sup>")
        elif kind == "ref":
            out.append(f"⟦{v}⟧")
        elif kind == "br":
            out.append("  \n")
    return "".join(out)


def _md_blocks(blocks: list[Block], opts: ExportOptions) -> list[str]:
    parts = []
    for b in blocks:
        if b.kind == "heading":
            parts.append("#" * min(b.level + 1, 6) + " " + _md_inline(b.text, opts))
        elif b.kind in ("para", "marker"):
            parts.append(_md_inline(b.text, opts))
        elif b.kind == "facing":
            from .render_html import blocks_html
            parts.append(f'<table class="facing"><tr><td>\n\n{blocks_html(b.left, opts)}\n\n</td>'
                         f'<td>\n\n{blocks_html(b.right, opts)}\n\n</td></tr></table>')
        elif b.kind == "notes":
            parts.append(f"> **Notes {opts.fmt_marker(b.label)}**  \n> " +
                         _md_inline(b.text, opts).replace("\n", "\n> "))
    return parts


def render_markdown(a: Assembled, doc: Document, opts: ExportOptions) -> str:
    head = [f"# {doc.title}"]
    if doc.author:
        head.append(f"*{doc.author}*")
    parts = head + _md_blocks(a.blocks, opts)
    if a.notes:
        parts.append("## Notes")
        for label, text in a.notes:
            parts.append(f"**{opts.fmt_marker(label)}**  \n" + _md_inline(text, opts).replace("\n\n", "  \n"))
    return "\n\n".join(parts).strip() + "\n"
