"""EPUB 3 renderer with embedded Noto fonts so Greek, Hebrew and Arabic display on any reader."""
from __future__ import annotations

import html
import re
import uuid
from pathlib import Path

from ebooklib import epub

from ..db import Document
from ..ocr.textutil import script_counts
from .assemble import Assembled, Block
from .render_html import FONT_STACK, blocks_html, css, notes_html

FONT_DIRS = [Path("/usr/share/fonts/truetype/noto"), Path("/usr/share/fonts/opentype/noto"),
             Path("/usr/share/fonts/truetype/gfs")]
FONT_FILES = {
    "base": [("Noto Serif", "NotoSerif-Regular.ttf", "normal", "normal"),
             ("Noto Serif", "NotoSerif-Italic.ttf", "italic", "normal"),
             ("Noto Serif", "NotoSerif-Bold.ttf", "normal", "bold")],
    "hebrew": [("Noto Serif Hebrew", "NotoSerifHebrew-Regular.ttf", "normal", "normal")],
    "arabic": [("Noto Naskh Arabic", "NotoNaskhArabic-Regular.ttf", "normal", "normal")],
}


def _find_font(name: str) -> Path | None:
    for d in FONT_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


def _plain(text: str) -> str:
    return re.sub(r"[*]|\[\^[^\]]*\]|⟦[^⟧]*⟧", "", text).strip()[:120] or "Section"


def _chapters(blocks: list[Block]) -> list[tuple[str, list[Block]]]:
    levels = [b.level for b in blocks if b.kind == "heading"]
    if not levels:
        return [("Text", blocks)]
    top = min(levels)
    if levels.count(top) > 300:
        return [("Text", blocks)]
    chapters: list[tuple[str, list[Block]]] = []
    current: list[Block] = []
    title = "Front matter"
    for b in blocks:
        if b.kind == "heading" and b.level == top:
            if current:
                chapters.append((title, current))
            title, current = _plain(b.text), [b]
        else:
            current.append(b)
    if current:
        chapters.append((title, current))
    return chapters


def render_epub(a: Assembled, doc: Document, opts, path: str) -> None:
    book = epub.EpubBook()
    book.set_identifier(f"scriptorium-{doc.id}-{uuid.uuid4().hex[:6]}")
    book.set_title(doc.title)
    book.set_language("en")
    if doc.author:
        book.add_author(doc.author)

    all_text = " ".join(b.text for b in a.blocks) + " ".join(t for _, t in a.notes)
    counts = script_counts(all_text)
    groups = ["base"] + (["hebrew"] if counts["hebrew"] else []) + (["arabic"] if counts["arabic"] else [])
    faces = []
    for g in groups:
        for family, fname, style, weight in FONT_FILES[g]:
            fp = _find_font(fname)
            if not fp:
                continue
            book.add_item(epub.EpubItem(uid=f"font-{fname}", file_name=f"fonts/{fname}",
                                        media_type="font/ttf", content=fp.read_bytes()))
            faces.append(f'@font-face {{ font-family: "{family}"; font-style: {style}; font-weight: {weight}; '
                         f'src: url("../fonts/{fname}"); }}')
    style = epub.EpubItem(uid="style", file_name="style/main.css", media_type="text/css",
                          content=("\n".join(faces) + css(opts, False)).encode())
    book.add_item(style)

    chapters = []
    if opts.title_page:
        tp = epub.EpubHtml(title=doc.title, file_name="title.xhtml", lang="en")
        tp.content = (f'<section class="titlepage"><h1>{html.escape(doc.title)}</h1>'
                      + (f'<p class="author"><em>{html.escape(doc.author)}</em></p>' if doc.author else "")
                      + "</section>")
        tp.add_item(style)
        chapters.append(tp)
    for i, (title, blocks) in enumerate(_chapters(a.blocks), start=1):
        ch = epub.EpubHtml(title=title, file_name=f"ch{i:03d}.xhtml", lang="en")
        ch.content = blocks_html(blocks, opts) or "<p></p>"
        ch.add_item(style)
        chapters.append(ch)
    if a.notes:
        nt = epub.EpubHtml(title="Notes", file_name="notes.xhtml", lang="en")
        nt.content = notes_html(a, opts)
        nt.add_item(style)
        chapters.append(nt)

    for ch in chapters:
        book.add_item(ch)
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters
    epub.write_epub(path, book)


__all__ = ["render_epub", "FONT_STACK"]
