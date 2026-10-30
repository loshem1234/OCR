"""JSON API used by the single-page frontend."""
from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from . import storage
from .auth import check_password, require_auth
from .config import settings
from .db import DEFAULT_DOC_SETTINGS, Document, Page, PageRevision, get_session, session_scope
from .export import EXPORTERS, ExportOptions, build_export
from .ingest import ACCEPTED_EXTS, kind_of
from .layout import clean_layout, clean_layouts, resolve_all
from .ocr.tesseract_engine import TESS_LANGS, installed_langs
from .worker import worker

router = APIRouter(prefix="/api")
authed = [Depends(require_auth)]

ENGINES = ("auto", "tesseract", "gemini", "embedded")


# ------------------------------------------------------------------ helpers

def _get_doc(s: Session, doc_id: str) -> Document:
    doc = s.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


def _get_page(s: Session, page_id: int) -> Page:
    page = s.get(Page, page_id)
    if page is None:
        raise HTTPException(404, "Page not found")
    return page


def _counts(s: Session, doc_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {d: {} for d in doc_ids}
    if not doc_ids:
        return out
    rows = s.execute(
        select(Page.doc_id, Page.status, func.count()).where(Page.doc_id.in_(doc_ids)).group_by(Page.doc_id, Page.status)
    )
    for doc_id, status, n in rows:
        out[doc_id][status] = n
    extra = s.execute(
        select(Page.doc_id, func.sum(Page.tokens_in), func.sum(Page.tokens_out),
               func.sum(case((Page.engine == "gemini", 1), else_=0)),
               func.sum(case((Page.skip.is_(True), 1), else_=0)))
        .where(Page.doc_id.in_(doc_ids)).group_by(Page.doc_id)
    )
    for doc_id, tin, tout, gem, skipped in extra:
        out[doc_id].update(tokens_in=int(tin or 0), tokens_out=int(tout or 0),
                           gemini_pages=int(gem or 0), skipped=int(skipped or 0))
    return out


def _doc_json(doc: Document, counts: dict | None = None, full: bool = False) -> dict:
    c = counts or {}
    d = {
        "id": doc.id, "title": doc.title, "author": doc.author, "status": doc.status, "error": doc.error,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "page_count": doc.page_count, "ingest_done": doc.ingest_done, "size_bytes": doc.size_bytes,
        "images_purged": doc.images_purged,
        "sources": [{"name": x.get("name"), "kind": x.get("kind"), "pages": x.get("pages")} for x in doc.source_files or []],
        "progress": {k: c.get(k, 0) for k in ("new", "queued", "running", "done", "error")},
        "gemini_pages": c.get("gemini_pages", 0), "skipped": c.get("skipped", 0),
        "tokens_in": c.get("tokens_in", 0), "tokens_out": c.get("tokens_out", 0),
    }
    if full:
        from .ocr.scholarly import profile_for
        d["settings"] = {**DEFAULT_DOC_SETTINGS, **(doc.settings or {})}
        d["effective_profile"] = profile_for(d["settings"])
        d["layouts"] = clean_layouts(doc.layouts)
        d["export_options"] = doc.export_options or {}
    return d


def _overrides(s: Session, doc_id: str) -> list[tuple[int, dict | None]]:
    return [(i, o) for i, o in s.execute(select(Page.index, Page.override).where(Page.doc_id == doc_id))]


def _effective(s: Session, doc: Document) -> dict[int, tuple[dict, str, int]]:
    return resolve_all(doc.layouts, _overrides(s, doc.id))


def _page_summary(p: Page, eff: tuple[dict, str, int]) -> dict:
    _layout, source, from_page = eff
    return {
        "id": p.id, "index": p.index, "number": p.index + 1, "width": p.width, "height": p.height,
        "status": p.status, "engine": p.engine, "route_reason": p.route_reason, "confidence": p.confidence,
        "skip": p.skip, "override": p.override, "layout_source": source, "layout_from": from_page,
        "edited": p.edited,
        "reviewed": p.reviewed, "flags": len(p.low_conf or []), "has_text_layer": p.has_text_layer,
        "printed_page": p.printed_page, "error": p.error, "warning": p.warning,
        "facing": bool(p.right), "has_notes": bool(p.notes),
        "text_format": p.text_format or "prose", "profile": p.profile or "",
        "spread": (p.image_meta or {}).get("spread"),
        "witness_status": ("human-reviewed" if p.reviewed and p.witness_status else p.witness_status or ""),
        "disagreements": _n_disagreements(p),
    }


def _n_disagreements(p: Page) -> int:
    c = p.collation or {}
    return sum(int(r.get("substantive", 0)) for r in (c.get("regions") or {}).values())


def _safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w.\- ]+", "_", name).strip() or "file"
    return name[:120]


def _clean_settings(raw: dict, base: dict | None = None) -> dict:
    s = {**DEFAULT_DOC_SETTINGS, **(base or {})}
    if "languages" in raw:
        langs = [l for l in raw["languages"] if isinstance(l, str) and l in TESS_LANGS]
        s["languages"] = langs or ["eng"]
    if raw.get("engine") in ENGINES:
        s["engine"] = raw["engine"]
    if raw.get("footnote_mode") in ("drop", "separate"):
        s["footnote_mode"] = raw["footnote_mode"]
    for k in ("preserve_refs", "use_text_layer", "split_spreads"):
        if k in raw:
            s[k] = bool(raw[k])
    if "hint" in raw:
        s["hint"] = str(raw["hint"] or "")[:1000]
    if raw.get("profile") in ("", "standard", "scholarly_greek"):
        s["profile"] = raw["profile"]
    return s


# ------------------------------------------------------------------ auth & meta

class LoginIn(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginIn, request: Request):
    if not check_password(request, body.password):
        raise HTTPException(401, "Incorrect password")
    request.session["auth"] = True
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/config", dependencies=authed)
def config():
    have = set(installed_langs())
    return {
        "languages": [{"code": k, "label": v, "installed": k in have} for k, v in TESS_LANGS.items()],
        "gemini_enabled": settings.gemini_enabled,
        "gemini_model": settings.gemini_model if settings.gemini_enabled else None,
        "hybrid_threshold": settings.hybrid_conf_threshold,
        "accepted": sorted(ACCEPTED_EXTS),
        "max_upload_mb": settings.max_upload_mb,
        "export_formats": list(EXPORTERS.keys()),
        "scholarly": {"mode": settings.scholarly_greek, "primary": settings.scholarly_primary,
                      "tesseract_model_dir": settings.scholarly_tessdata_dir or "system",
                      "gemini_model": settings.scholarly_gemini_model if settings.gemini_enabled else None,
                      "reconcile": settings.scholarly_reconcile},
    }


@router.get("/storage", dependencies=authed)
def storage_info():
    return storage.storage_report()


# ------------------------------------------------------------------ documents

@router.get("/docs", dependencies=authed)
def list_docs(s: Session = Depends(get_session)):
    docs = list(s.scalars(select(Document).order_by(Document.created_at.desc())))
    counts = _counts(s, [d.id for d in docs])
    return [_doc_json(d, counts.get(d.id)) for d in docs]


@router.post("/docs", dependencies=authed)
def create_docs(
    files: list[UploadFile] = File(...),
    title: str = Form(""),
    author: str = Form(""),
    languages: str = Form("eng"),
    engine: str = Form("auto"),
    footnote_mode: str = Form("drop"),
    preserve_refs: bool = Form(False),
    use_text_layer: bool = Form(False),
    hint: str = Form(""),
    combine: bool = Form(True),
    split_spreads: bool = Form(False),
    s: Session = Depends(get_session),
):
    files = [f for f in files if f.filename]
    if not files:
        raise HTTPException(400, "No files uploaded")
    bad = [f.filename for f in files if not kind_of(f.filename)]
    if bad:
        raise HTTPException(400, f"Unsupported file type: {', '.join(bad)}")
    doc_settings = _clean_settings({
        "languages": [l.strip() for l in languages.split(",") if l.strip()],
        "engine": engine, "footnote_mode": footnote_mode, "preserve_refs": preserve_refs,
        "use_text_layer": use_text_layer, "hint": hint, "split_spreads": split_spreads,
    })
    groups = [files] if (combine or len(files) == 1) else [[f] for f in files]
    limit = settings.max_upload_mb * 1024 * 1024
    created = []
    for group in groups:
        doc = Document(
            title=(title.strip() if title.strip() and len(groups) == 1 else Path(group[0].filename).stem)[:500],
            author=author.strip()[:500], settings=doc_settings,
        )
        s.add(doc)
        s.flush()
        sdir = storage.source_dir(doc.id)
        sdir.mkdir(parents=True, exist_ok=True)
        sources = []
        for i, f in enumerate(group):
            stored = f"{i:03d}_{_safe_name(f.filename)}"
            dest = sdir / stored
            written = 0
            with dest.open("wb") as out:
                while chunk := f.file.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        out.close()
                        storage.delete_doc_files(doc.id)
                        raise HTTPException(413, f"{f.filename} exceeds the {settings.max_upload_mb} MB upload limit")
                    out.write(chunk)
            sources.append({"name": f.filename, "stored": stored, "kind": kind_of(f.filename), "pages": 0})
        doc.source_files = sources
        doc.status = "ingesting"
        created.append(doc)
    s.commit()
    for doc in created:
        worker.submit_ingest(doc.id)
    return [_doc_json(d) for d in created]


@router.get("/docs/{doc_id}", dependencies=authed)
def get_doc(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    return _doc_json(doc, _counts(s, [doc_id])[doc_id], full=True)


class DocPatch(BaseModel):
    title: str | None = None
    author: str | None = None
    settings: dict[str, Any] | None = None
    layouts: dict[str, Any] | None = None


@router.patch("/docs/{doc_id}", dependencies=authed)
def patch_doc(doc_id: str, body: DocPatch, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    if body.title is not None:
        doc.title = body.title.strip()[:500] or "Untitled"
    if body.author is not None:
        doc.author = body.author.strip()[:500]
    if body.settings is not None:
        doc.settings = _clean_settings(body.settings, doc.settings)
    if body.layouts is not None:
        merged = {**clean_layouts(doc.layouts), **body.layouts}
        doc.layouts = clean_layouts(merged)
    s.flush()
    return _doc_json(doc, _counts(s, [doc_id])[doc_id], full=True)


@router.delete("/docs/{doc_id}", dependencies=authed)
def delete_doc(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    s.delete(doc)
    s.flush()
    storage.delete_doc_files(doc_id)
    return {"ok": True}


def _purge(s: Session, doc: Document) -> None:
    if doc.status in ("ingesting", "processing"):
        raise HTTPException(409, f"'{doc.title}' is still being processed; pause it first.")
    storage.purge_images(doc.id)
    doc.images_purged = True
    doc.size_bytes = storage.dir_size(storage.doc_dir(doc.id))


@router.post("/docs/{doc_id}/purge-images", dependencies=authed)
def purge_doc_images(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    _purge(s, doc)
    return _doc_json(doc)


class BulkIn(BaseModel):
    action: str
    ids: list[str]


@router.post("/docs/bulk", dependencies=authed)
def bulk(body: BulkIn, s: Session = Depends(get_session)):
    done = []
    for doc_id in body.ids:
        doc = s.get(Document, doc_id)
        if not doc:
            continue
        if body.action == "delete":
            s.delete(doc)
            s.flush()
            storage.delete_doc_files(doc_id)
        elif body.action == "purge":
            _purge(s, doc)
        else:
            raise HTTPException(400, "Unknown action")
        done.append(doc_id)
    return {"ok": True, "ids": done, "storage": storage.storage_report()}


@router.get("/docs/{doc_id}/source", dependencies=authed)
def download_source(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    for sf in doc.source_files or []:
        p = storage.source_dir(doc_id) / sf["stored"]
        if p.exists():
            return FileResponse(p, filename=sf["name"])
    raise HTTPException(404, "Original file is no longer stored")


# ------------------------------------------------------------------ processing

class RunIn(BaseModel):
    scope: str = "unprocessed"   # all | unprocessed | errors | flagged | pages
    pages: list[int] = []         # page ids, for scope == pages
    engine: str = ""              # "" (use document setting) | tesseract | gemini | embedded
    include_edited: bool = False


@router.post("/docs/{doc_id}/run", dependencies=authed)
def run_doc(doc_id: str, body: RunIn, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    if doc.status == "ingesting":
        raise HTTPException(409, "The document is still being imported")
    if doc.images_purged and body.engine != "embedded":
        raise HTTPException(409, "Page images were purged for this document; OCR can't be re-run.")
    if body.engine and body.engine not in ENGINES[1:]:
        raise HTTPException(400, "Unknown engine")
    queued = 0
    wanted = set(body.pages)
    for p in doc.pages:
        if p.skip or p.status in ("queued", "running"):
            continue
        if p.edited and not body.include_edited and body.scope != "pages":
            continue
        sel = {
            "all": True,
            "unprocessed": p.status in ("new", "error"),
            "errors": p.status == "error",
            "flagged": p.status == "done" and (bool(p.low_conf) or bool(p.warning)),
            "pages": p.id in wanted,
        }.get(body.scope, False)
        if sel:
            p.status, p.forced_engine, p.error = "queued", body.engine, ""
            queued += 1
    if queued:
        doc.status = "processing"
    elif doc.status in ("ready",):
        pass
    s.flush()
    return {"queued": queued, "doc": _doc_json(doc, _counts(s, [doc_id])[doc_id])}


@router.post("/docs/{doc_id}/pause", dependencies=authed)
def pause_doc(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    if doc.status == "processing":
        doc.status = "paused"
    return {"status": doc.status}


@router.post("/docs/{doc_id}/resume", dependencies=authed)
def resume_doc(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    if doc.status == "paused":
        doc.status = "processing"
    return {"status": doc.status}


@router.post("/docs/{doc_id}/cancel", dependencies=authed)
def cancel_doc(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    for p in doc.pages:
        if p.status == "queued":
            p.status = "done" if (p.body or p.notes or p.right) else "new"
    if doc.status in ("processing", "paused"):
        doc.status = "review" if any(p.status == "done" for p in doc.pages) else "ready"
    return {"status": doc.status}


# ------------------------------------------------------------------ pages

@router.get("/docs/{doc_id}/pages", dependencies=authed)
def list_pages(doc_id: str, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    eff = resolve_all(doc.layouts, [(p.index, p.override) for p in doc.pages])
    return [_page_summary(p, eff[p.index]) for p in doc.pages]


@router.get("/pages/{page_id}", dependencies=authed)
def get_page(page_id: int, s: Session = Depends(get_session)):
    p = _get_page(s, page_id)
    doc = s.get(Document, p.doc_id)
    eff = _effective(s, doc)[p.index]
    layout = eff[0]
    return {**_page_summary(p, eff), "collation": p.collation, "provenance": p.provenance, "image_meta": p.image_meta,
            "revisions": len(p.revisions), "body": p.body, "right": p.right, "notes": p.notes,
            "low_conf": p.low_conf or [], "layout": layout, "tokens_in": p.tokens_in, "tokens_out": p.tokens_out}


class PagePatch(BaseModel):
    body: str | None = None
    right: str | None = None
    notes: str | None = None
    skip: bool | None = None
    reviewed: bool | None = None
    override: dict[str, Any] | None = None
    clear_override: bool = False
    printed_page: str | None = None


@router.patch("/pages/{page_id}", dependencies=authed)
def patch_page(page_id: int, body: PagePatch, s: Session = Depends(get_session)):
    p = _get_page(s, page_id)
    text_changed = False
    for field in ("body", "right", "notes"):
        val = getattr(body, field)
        if val is not None:
            val = unicodedata.normalize("NFC", val)  # one storage normalization for everything (canonical only)
        if val is not None and val != getattr(p, field):
            setattr(p, field, val)
            text_changed = True
    if text_changed:
        p.edited = True
        if p.status == "new":
            p.status = "done"
            p.engine = p.engine or "manual"
    if body.skip is not None:
        p.skip = body.skip
    if body.reviewed is not None:
        p.reviewed = body.reviewed
    if body.printed_page is not None:
        p.printed_page = body.printed_page.strip()[:40]
    if body.clear_override:
        p.override = None
    elif body.override is not None:
        p.override = clean_layout(body.override)
    s.flush()
    doc = s.get(Document, p.doc_id)
    return _page_summary(p, _effective(s, doc)[p.index])


class RangeIn(BaseModel):
    start: int  # 1-based, inclusive
    end: int
    layout: dict[str, Any] | None = None  # None clears overrides in the range
    skip: bool | None = None
    isolate: bool = False                 # keep only start..end: skip everything else, un-skip inside


@router.post("/docs/{doc_id}/pages/range", dependencies=authed)
def apply_range(doc_id: str, body: RangeIn, s: Session = Depends(get_session)):
    doc = _get_doc(s, doc_id)
    lo, hi = sorted((body.start, body.end))
    n = 0
    if body.isolate:
        for p in doc.pages:
            p.skip = not (lo <= p.index + 1 <= hi)
            n += 0 if p.skip else 1
        return {"updated": n}
    for p in doc.pages:
        if lo <= p.index + 1 <= hi:
            if body.skip is not None:
                p.skip = body.skip
            else:
                p.override = clean_layout(body.layout) if body.layout else None
            n += 1
    return {"updated": n}


@router.post("/pages/{page_id}/rerun", dependencies=authed)
def rerun_page(page_id: int, body: RunIn, s: Session = Depends(get_session)):
    p = _get_page(s, page_id)
    doc = s.get(Document, p.doc_id)
    if body.engine and body.engine not in ENGINES[1:]:
        raise HTTPException(400, "Unknown engine")
    if p.status == "running":
        raise HTTPException(409, "This page is already being read")
    p.status, p.forced_engine, p.error = "queued", body.engine, ""
    if doc.status != "processing":
        doc.status = "processing"
    return {"ok": True}


def _image_response(path: Path) -> FileResponse:
    if not path.exists():
        raise HTTPException(404, "Image not available")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


# page id -> (doc id, index) never changes, so image requests don't need the database after the first hit.
# Image endpoints must not hold a pooled connection while the file streams: a long book's thumbnail rail
# fires dozens of parallel requests over HTTP/2, which would otherwise exhaust the pool.
_page_loc: dict[int, tuple[str, int]] = {}


def _locate(page_id: int) -> tuple[str, int]:
    loc = _page_loc.get(page_id)
    if loc is None:
        with session_scope() as s:
            row = s.execute(select(Page.doc_id, Page.index).where(Page.id == page_id)).first()
        if row is None:
            raise HTTPException(404, "Page not found")
        loc = _page_loc[page_id] = (row[0], row[1])
    return loc


@router.get("/pages/{page_id}/image", dependencies=authed)
def page_image(page_id: int, full: int = 0):
    doc_id, index = _locate(page_id)
    path = storage.page_image_path(doc_id, index) if full else storage.display_image(doc_id, index)
    if not path.exists():
        path = storage.thumb_path(doc_id, index)  # purged: fall back to thumbnail
    return _image_response(path)


@router.get("/pages/{page_id}/thumb", dependencies=authed)
def page_thumb(page_id: int):
    doc_id, index = _locate(page_id)
    return _image_response(storage.thumb_path(doc_id, index))


@router.get("/docs/{doc_id}/thumb", dependencies=authed)
def doc_thumb(doc_id: str):
    return _image_response(storage.thumb_path(doc_id, 0))


# ------------------------------------------------------------------ export

@router.get("/docs/{doc_id}/export/{fmt}", dependencies=authed)
def export(doc_id: str, fmt: str, request: Request, s: Session = Depends(get_session)):
    if fmt not in EXPORTERS:
        raise HTTPException(404, "Unknown format")
    doc = _get_doc(s, doc_id)
    opts = ExportOptions.from_query(dict(request.query_params))
    doc.export_options = opts.to_dict()
    s.commit()
    path, filename, media = build_export(s, doc, fmt, opts)
    return FileResponse(path, filename=filename, media_type=media,
                        background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)))


@router.get("/docs/{doc_id}/preview", dependencies=authed)
def preview(doc_id: str, request: Request, s: Session = Depends(get_session)):
    """Assembled markdown for the export pane's live preview (first ~40k chars)."""
    doc = _get_doc(s, doc_id)
    from .export import prepare_options
    opts = prepare_options(doc, ExportOptions.from_query(dict(request.query_params)))
    from .export.assemble import assemble
    from .export.render_text import render_markdown
    md = render_markdown(assemble(doc, opts), doc, opts)
    return {"markdown": md[:40000], "truncated": len(md) > 40000, "chars": len(md)}


# ------------------------------------------------------------------ witnesses, history, audit

@router.get("/pages/{page_id}/recognitions", dependencies=authed)
def page_recognitions(page_id: int, raw: int = 0, s: Session = Depends(get_session)):
    """Every engine's untouched reading of this page (newest run first)."""
    p = _get_page(s, page_id)
    out = []
    for r in sorted(p.recognitions, key=lambda r: r.id, reverse=True):
        out.append({"id": r.id, "run_id": r.run_id, "created_at": r.created_at.isoformat() if r.created_at else None,
                    "engine": r.engine, "region": r.region, "status": r.status, "model": r.model,
                    "engine_version": r.engine_version, "config": r.config, "text": r.text, "lines": r.lines,
                    "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "latency_ms": r.latency_ms,
                    "warnings": r.warnings, **({"raw": r.raw} if raw else {})})
    return out


@router.get("/pages/{page_id}/revisions", dependencies=authed)
def page_revisions(page_id: int, s: Session = Depends(get_session)):
    p = _get_page(s, page_id)
    return [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None, "reason": r.reason,
             "edited": r.edited, "reviewed": r.reviewed, "engine": r.engine, "text_format": r.text_format,
             "witness_status": r.witness_status, "chars": len(r.body or "") + len(r.right or "") + len(r.notes or ""),
             "preview": (r.body or "")[:160]}
            for r in sorted(p.revisions, key=lambda r: r.id, reverse=True)]


@router.post("/pages/{page_id}/revisions/{rev_id}/restore", dependencies=authed)
def restore_revision(page_id: int, rev_id: int, s: Session = Depends(get_session)):
    """Bring back an earlier text. The current text is itself snapshotted first, so nothing is lost."""
    from .ocr.scholarly import snapshot
    p = _get_page(s, page_id)
    rev = s.get(PageRevision, rev_id)
    if rev is None or rev.page_id != page_id:
        raise HTTPException(404, "Revision not found")
    snapshot(s, p, "before restore")
    p.body, p.right, p.notes = rev.body, rev.right, rev.notes
    p.text_format = rev.text_format or "prose"
    p.printed_page = rev.printed_page or p.printed_page
    p.edited, p.reviewed = rev.edited, rev.reviewed
    if p.status == "new":
        p.status = "done"
    s.flush()
    doc = s.get(Document, p.doc_id)
    return _page_summary(p, _effective(s, doc)[p.index])


@router.get("/docs/{doc_id}/collation.json", dependencies=authed)
def collation_report(doc_id: str, raw: int = 0, s: Session = Depends(get_session)):
    """Machine-readable audit of the whole document: per page, both witnesses' lines, every disagreement with
    its category, location and any proposal, the working text, and review status."""
    import json as _json

    from fastapi.responses import Response
    doc = _get_doc(s, doc_id)
    pages = []
    for p in doc.pages:
        latest = {}
        for r in sorted(p.recognitions, key=lambda r: r.id):
            if r.engine in ("tesseract", "gemini"):
                latest[(r.engine, r.region)] = r
        run = (p.collation or {}).get("run_id")
        pages.append({
            "page_id": p.id, "page_number": p.index + 1, "printed_page": p.printed_page, "skipped": p.skip,
            "profile": p.profile, "text_format": p.text_format, "witness_status": p.witness_status,
            "human_reviewed": p.reviewed, "edited_by_user": p.edited,
            "verification": "human-reviewed" if p.reviewed else (
                "engines agree (NOT verified against the page)" if p.witness_status == "engines-agree" else
                "unverified"),
            "provenance": p.provenance,
            "witnesses": {f"{e}:{reg}": {"status": r.status, "model": r.model, "run_id": r.run_id,
                                         "lines": [l.get("text") for l in (r.lines or [])],
                                         **({"raw": r.raw} if raw else {})}
                          for (e, reg), r in latest.items() if not run or r.run_id == run},
            "collation": p.collation,
            "working_text": p.body, "right": p.right, "notes": p.notes,
            "warnings": [w for w in (p.warning or "").splitlines() if w],
        })
    body = _json.dumps({"document": {"id": doc.id, "title": doc.title, "author": doc.author,
                                     "settings": doc.settings}, "pages": pages}, ensure_ascii=False, indent=1)
    name = re.sub(r"[^\w\- ]+", "", doc.title).strip().replace(" ", "_") or "document"
    return Response(body, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}_collation.json"'})


# ------------------------------------------------------------------ two-page scans

@router.get("/pages/{page_id}/spread", dependencies=authed)
def spread_image(page_id: int, s: Session = Depends(get_session)):
    p = _get_page(s, page_id)
    sp = (p.image_meta or {}).get("spread")
    if not sp:
        raise HTTPException(404, "This page is not part of a two-page scan")
    doc_id = p.doc_id
    s.close()  # don't hold a pooled connection while the image is built and sent
    if not storage.spread_path(doc_id, sp["scan"]).exists():
        raise HTTPException(404, "The original two-page scan is no longer stored (images purged)")
    return _image_response(storage.spread_preview(doc_id, sp["scan"]))


class ResplitIn(BaseModel):
    fraction: float  # split position as a fraction of the scan's width


@router.post("/pages/{page_id}/resplit", dependencies=authed)
def resplit_page(page_id: int, body: ResplitIn, s: Session = Depends(get_session)):
    """Move the cut between the two halves of a two-page scan. Both pages are re-cut from the stored scan."""
    from .ingest import resplit
    p = _get_page(s, page_id)
    sp = (p.image_meta or {}).get("spread")
    if not sp:
        raise HTTPException(400, "This page is not part of a two-page scan")
    if not storage.spread_path(p.doc_id, sp["scan"]).exists():
        raise HTTPException(409, "The original two-page scan is no longer stored (images purged)")
    if not 0.05 <= body.fraction <= 0.95:
        raise HTTPException(400, "Split must be between 5% and 95% of the width")
    doc = s.get(Document, p.doc_id)
    pair = [q for q in doc.pages if (q.image_meta or {}).get("spread", {}).get("scan") == sp["scan"]]
    if any(q.status in ("queued", "running") for q in pair):
        raise HTTPException(409, "These pages are being read; wait for OCR to finish")
    x = int(round(body.fraction * sp["spread_width"]))
    resplit(p.doc_id, sp["scan"], x, pair)
    for q in pair:
        if q.body or q.right or q.notes:
            note = "The split of this two-page scan was moved after OCR — re-read this page."
            q.warning = (note + "\n" + (q.warning or "")).strip()[:4000]
    s.flush()
    eff = _effective(s, doc)
    return [_page_summary(q, eff[q.index]) for q in pair]


class ReimportIn(BaseModel):
    split_spreads: bool
    confirm_discard_text: bool = False


@router.post("/docs/{doc_id}/reimport", dependencies=authed)
def reimport(doc_id: str, body: ReimportIn, s: Session = Depends(get_session)):
    """Import the stored original again (e.g. to split two-page scans). This replaces all pages."""
    doc = _get_doc(s, doc_id)
    if doc.status in ("ingesting", "processing"):
        raise HTTPException(409, "Wait until importing/reading has finished (or pause it).")
    if doc.images_purged or not all((storage.source_dir(doc_id) / sf["stored"]).exists() for sf in doc.source_files):
        raise HTTPException(409, "The original file is no longer stored, so it can't be re-imported. Upload it again.")
    with_text = sum(1 for p in doc.pages if p.body or p.right or p.notes)
    if with_text and not body.confirm_discard_text:
        raise HTTPException(409, f"{with_text} page(s) already have OCR text, which re-importing would discard.")
    doc.settings = _clean_settings({"split_spreads": body.split_spreads}, doc.settings)
    doc.status = "ingesting"
    doc.ingest_done = 0
    doc.error = ""
    s.commit()
    worker.submit_ingest(doc_id)
    return {"ok": True}
