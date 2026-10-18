"""HTML renderer shared by the PDF (WeasyPrint) and EPUB exporters."""
from __future__ import annotations

import html

from ..db import Document
from ..ocr.textutil import is_rtl
from .assemble import Assembled, Block
from .inline import tokens
from .options import PAGE_SIZES, ExportOptions

FONT_STACK = '"Noto Serif", "GFS Didot", "Noto Serif Hebrew", "Noto Naskh Arabic", "Noto Sans Syriac", "DejaVu Serif", serif'


def inline(text: str, opts: ExportOptions) -> str:
    out = []
    for kind, v in tokens(text):
        e = html.escape(v)
        if kind == "text":
            out.append(e)
        elif kind == "italic":
            out.append(f"<em>{e}</em>")
        elif kind == "bold":
            out.append(f"<strong>{e}</strong>")
        elif kind == "page":
            out.append(f'<span class="pg">{html.escape(opts.fmt_marker(v))}</span>')
        elif kind == "fn":
            out.append(f'<sup class="fn">{e}</sup>')
        elif kind == "ref":
            out.append(f'<span class="ref">{e}</span>')
        elif kind == "br":
            out.append("<br/>")
    return "".join(out)


def _dir(text: str) -> str:
    return ' dir="rtl"' if is_rtl(text) else ""


def _para(text: str, opts: ExportOptions, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    if "\n" in text:  # verse or deliberate line breaks
        c = f' class="{(cls + " verse").strip()}"'
    return f"<p{c}{_dir(text)}>{inline(text, opts)}</p>"


def blocks_html(blocks: list[Block], opts: ExportOptions) -> str:
    out = []
    for b in blocks:
        if b.kind == "heading":
            lvl = min(b.level + 1, 6)
            out.append(f"<h{lvl}{_dir(b.text)}>{inline(b.text, opts)}</h{lvl}>")
        elif b.kind == "para":
            out.append(_para(b.text, opts))
        elif b.kind == "marker":
            out.append(_para(b.text, opts, "marker"))
        elif b.kind == "facing":
            out.append(
                '<table class="facing"><tr>'
                f'<td>{blocks_html(b.left, opts)}</td><td>{blocks_html(b.right, opts)}</td>'
                "</tr></table>"
            )
        elif b.kind == "notes":
            paras = "".join(_para(t, opts) for t in b.text.split("\n\n") if t.strip())
            out.append(f'<aside class="pagenotes"><div class="nlabel">{html.escape(opts.fmt_marker(b.label))}</div>{paras}</aside>')
    return "\n".join(out)


def notes_html(a: Assembled, opts: ExportOptions) -> str:
    if not a.notes:
        return ""
    items = []
    for label, text in a.notes:
        paras = "".join(_para(t, opts) for t in text.split("\n\n") if t.strip())
        items.append(f'<div class="note"><div class="nlabel">{html.escape(opts.fmt_marker(label))}</div>{paras}</div>')
    return '<section class="endnotes"><h2>Notes</h2>' + "".join(items) + "</section>"


def css(opts: ExportOptions, for_pdf: bool, title: str = "") -> str:
    fs = opts.font_size
    page = ""
    if for_pdf:
        t = title.replace('"', "'")
        page = f"""
@page {{ size: {PAGE_SIZES.get(opts.page_size, PAGE_SIZES['letter'])}; margin: 22mm 20mm 22mm 20mm;
  @top-center {{ content: "{html.escape(t)}"; font-family: {FONT_STACK}; font-size: 8pt; color: #777; letter-spacing: .06em; }}
  @bottom-center {{ content: counter(page); font-family: {FONT_STACK}; font-size: 9pt; color: #555; }} }}
@page :first {{ @top-center {{ content: none; }} @bottom-center {{ content: none; }} }}
.titlepage {{ page-break-after: always; text-align: center; padding-top: 30%; }}
.endnotes {{ page-break-before: always; }}
"""
    return page + f"""
body {{ font-family: {FONT_STACK}; font-size: {fs}pt; line-height: 1.45; color: #111; }}
p {{ margin: 0 0 .55em 0; text-align: justify; orphans: 2; widows: 2; }}
p.verse {{ text-align: left; }}
p[dir="rtl"] {{ text-align: right; }}
p.marker {{ text-align: left; }}
h1, h2, h3, h4 {{ font-weight: 600; line-height: 1.25; page-break-after: avoid; }}
h2 {{ font-size: {fs * 1.45:.1f}pt; margin: 1.4em 0 .6em; }}
h3 {{ font-size: {fs * 1.2:.1f}pt; margin: 1.2em 0 .5em; }}
h4, h5, h6 {{ font-size: {fs * 1.05:.1f}pt; margin: 1em 0 .4em; }}
.pg {{ font-size: .72em; color: #8a7b55; font-style: normal; white-space: nowrap; }}
.ref {{ font-size: .7em; color: #6b6b6b; vertical-align: super; padding: 0 .1em; }}
sup.fn {{ font-size: .68em; line-height: 0; }}
table.facing {{ width: 100%; border-collapse: collapse; margin: .4em 0 1em; }}
table.facing td {{ width: 50%; vertical-align: top; padding: 0 .7em; }}
table.facing td + td {{ border-left: .5pt solid #bbb; }}
aside.pagenotes {{ font-size: .86em; border-top: .5pt solid #aaa; margin: .4em 0 1.1em; padding-top: .3em; }}
.endnotes {{ font-size: .9em; }}
.note {{ margin-bottom: .7em; }}
.nlabel {{ font-size: .8em; color: #8a7b55; margin-bottom: .15em; }}
.titlepage h1 {{ font-size: {fs * 2.2:.1f}pt; font-weight: 500; margin-bottom: .4em; }}
.titlepage .author {{ font-size: {fs * 1.3:.1f}pt; font-style: italic; }}
"""


def title_html(doc: Document) -> str:
    a = f'<div class="author">{html.escape(doc.author)}</div>' if doc.author else ""
    return f'<section class="titlepage"><h1>{html.escape(doc.title)}</h1>{a}</section>'


def full_html(a: Assembled, doc: Document, opts: ExportOptions) -> str:
    body = (title_html(doc) if opts.title_page else "") + blocks_html(a.blocks, opts) + notes_html(a, opts)
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(doc.title)}</title>'
        f"<style>{css(opts, True, doc.title)}</style></head><body>{body}</body></html>"
    )
