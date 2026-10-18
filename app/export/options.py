from __future__ import annotations

from dataclasses import asdict, dataclass

PAGE_SIZES = {"letter": "8.5in 11in", "a4": "210mm 297mm", "a5": "148mm 210mm", "6x9": "6in 9in"}


def _bool(v, default: bool) -> bool:
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


@dataclass
class ExportOptions:
    page_markers: bool = True
    marker_format: str = "[p. {n}]"
    first_number: int = 1          # printed number of PDF page 1
    use_printed: bool = True       # prefer page numbers Gemini read off the page
    join_pages: bool = True        # continue paragraphs across page breaks
    include_notes: bool = True
    notes_placement: str = "end"   # end | page
    refs: str = "inline"           # inline | hide
    page_size: str = "letter"
    font_size: float = 11.0
    title_page: bool = True

    @classmethod
    def from_query(cls, q: dict) -> "ExportOptions":
        d = cls()
        d.page_markers = _bool(q.get("page_markers"), d.page_markers)
        fmt = (q.get("marker_format") or d.marker_format)[:40]
        d.marker_format = fmt if "{n}" in fmt else d.marker_format
        try:
            d.first_number = int(q.get("first_number", d.first_number))
        except ValueError:
            pass
        d.use_printed = _bool(q.get("use_printed"), d.use_printed)
        d.join_pages = _bool(q.get("join_pages"), d.join_pages)
        d.include_notes = _bool(q.get("include_notes"), d.include_notes)
        if q.get("notes_placement") in ("end", "page"):
            d.notes_placement = q["notes_placement"]
        if q.get("refs") in ("inline", "hide"):
            d.refs = q["refs"]
        if q.get("page_size") in PAGE_SIZES:
            d.page_size = q["page_size"]
        try:
            d.font_size = min(max(float(q.get("font_size", d.font_size)), 7.0), 18.0)
        except ValueError:
            pass
        d.title_page = _bool(q.get("title_page"), d.title_page)
        return d

    def to_dict(self) -> dict:
        return asdict(self)

    def fmt_marker(self, label: str) -> str:
        return self.marker_format.replace("{n}", label)
