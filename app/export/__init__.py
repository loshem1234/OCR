"""Export pipeline: assemble pages, then render to the requested format."""
from __future__ import annotations

import re
import tempfile

from sqlalchemy.orm import Session

from .. import storage
from ..db import Document
from .assemble import assemble
from .options import ExportOptions

EXPORTERS = {
    "txt": ("text/plain; charset=utf-8", "txt"),
    "md": ("text/markdown; charset=utf-8", "md"),
    "pdf": ("application/pdf", "pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "epub": ("application/epub+zip", "epub"),
}


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", title, flags=re.U).strip().replace(" ", "_")
    return s[:80] or "document"


def build_export(_s: Session, doc: Document, fmt: str, opts: ExportOptions) -> tuple[str, str, str]:
    media, ext = EXPORTERS[fmt]
    a = assemble(doc, opts)
    fd, path = tempfile.mkstemp(suffix=f".{ext}", dir=storage.tmp_dir())
    with open(fd, "wb") as f:
        if fmt == "txt":
            from .render_text import render_txt
            f.write(render_txt(a, doc, opts).encode("utf-8"))
        elif fmt == "md":
            from .render_text import render_markdown
            f.write(render_markdown(a, doc, opts).encode("utf-8"))
    if fmt == "pdf":
        from weasyprint import HTML

        from .render_html import full_html
        HTML(string=full_html(a, doc, opts)).write_pdf(path)
    elif fmt == "docx":
        from .render_docx import render_docx
        render_docx(a, doc, opts, path)
    elif fmt == "epub":
        from .render_epub import render_epub
        render_epub(a, doc, opts, path)
    return path, f"{_slug(doc.title)}.{ext}", media


__all__ = ["EXPORTERS", "ExportOptions", "build_export"]
