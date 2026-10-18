"""Turn uploaded files (PDF, images, multi-page TIFF, DjVu, ZIP of scans) into page images."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator

import pymupdf
from PIL import Image, ImageOps, ImageSequence

from . import storage
from .config import settings
from .db import Document, Page, session_scope

log = logging.getLogger("ingest")
Image.MAX_IMAGE_PIXELS = 400_000_000  # large-format scans are legitimate here

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif", ".jp2", ".jpx"}
PDF_EXTS = {".pdf"}
DJVU_EXTS = {".djvu", ".djv"}
ZIP_EXTS = {".zip", ".cbz"}
ACCEPTED_EXTS = IMAGE_EXTS | PDF_EXTS | DJVU_EXTS | ZIP_EXTS
MAX_SIDE = 6000  # cap rendered page size in pixels


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in DJVU_EXTS:
        return "djvu"
    if ext in ZIP_EXTS:
        return "zip"
    if ext in IMAGE_EXTS:
        return "image"
    return ""


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------- counting

def count_pages(path: Path, kind: str) -> int:
    try:
        if kind == "pdf":
            with pymupdf.open(path) as d:
                return d.page_count
        if kind == "image":
            with Image.open(path) as im:
                return getattr(im, "n_frames", 1)
        if kind == "djvu":
            out = subprocess.run(["djvused", "-e", "n", str(path)], capture_output=True, text=True, timeout=120)
            return int(out.stdout.strip() or 0)
        if kind == "zip":
            with zipfile.ZipFile(path) as z:
                return sum(1 for n in z.namelist() if Path(n).suffix.lower() in IMAGE_EXTS and not n.startswith("__MACOSX"))
    except Exception as e:  # counting is only for the progress bar
        log.warning("could not count pages in %s: %s", path, e)
    return 0


# ---------------------------------------------------------------- iterators

def _normalize(im: Image.Image) -> Image.Image:
    im = ImageOps.exif_transpose(im)
    if im.mode != "L":
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, "white")
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        im = im.convert("L")
    w, h = im.size
    if max(w, h) > MAX_SIDE:
        s = MAX_SIDE / max(w, h)
        im = im.resize((int(w * s), int(h * s)), Image.LANCZOS)
    return im


def iter_pdf(path: Path) -> Iterator[tuple[Image.Image, bool]]:
    with pymupdf.open(path) as d:
        for page in d:
            rect = page.rect
            zoom = settings.render_dpi / 72.0
            longest = max(rect.width, rect.height) * zoom
            if longest > MAX_SIDE:
                zoom = MAX_SIDE / max(rect.width, rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
            im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            has_text = len(page.get_text("text").strip()) > 40
            yield im, has_text


def iter_image(path: Path) -> Iterator[tuple[Image.Image, bool]]:
    with Image.open(path) as im:
        for frame in ImageSequence.Iterator(im):
            yield _normalize(frame.copy()), False


def iter_djvu(path: Path) -> Iterator[tuple[Image.Image, bool]]:
    tmp = Path(tempfile.mkdtemp(dir=storage.tmp_dir()))
    try:
        n = count_pages(path, "djvu")
        for i in range(1, n + 1):
            out = tmp / f"p{i}.tif"
            subprocess.run(
                ["ddjvu", "-format=tiff", f"-page={i}", "-mode=color", str(path), str(out)],
                check=True, capture_output=True, timeout=300,
            )
            with Image.open(out) as im:
                yield _normalize(im.copy()), False
            out.unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def iter_zip(path: Path) -> Iterator[tuple[Image.Image, bool]]:
    with zipfile.ZipFile(path) as z:
        names = sorted(
            (n for n in z.namelist()
             if Path(n).suffix.lower() in IMAGE_EXTS and not n.startswith("__MACOSX") and not n.endswith("/")),
            key=natural_key,
        )
        for n in names:
            with z.open(n) as f, Image.open(f) as im:
                for frame in ImageSequence.Iterator(im):
                    yield _normalize(frame.copy()), False


ITERATORS = {"pdf": iter_pdf, "image": iter_image, "djvu": iter_djvu, "zip": iter_zip}


# ---------------------------------------------------------------- main entry

def _save_page(doc_id: str, index: int, im: Image.Image) -> None:
    storage.pages_dir(doc_id).mkdir(parents=True, exist_ok=True)
    storage.thumbs_dir(doc_id).mkdir(parents=True, exist_ok=True)
    ext = storage.image_ext()
    out = storage.pages_dir(doc_id) / f"{index + 1:04d}.{ext}"
    if ext == "png":
        im.save(out, "PNG", optimize=True)
    else:
        im.save(out, "JPEG", quality=settings.jpeg_quality, optimize=True)
    th = im.copy()
    th.thumbnail((220, 320))
    th.save(storage.thumb_path(doc_id, index), "JPEG", quality=80)


def ingest_document(doc_id: str) -> None:
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return
        sources = list(doc.source_files)
        # Restart-safe: wipe any half-finished previous attempt.
        for p in list(doc.pages):
            s.delete(p)
        doc.ingest_done = 0
        doc.status = "ingesting"
    shutil.rmtree(storage.pages_dir(doc_id), ignore_errors=True)
    shutil.rmtree(storage.thumbs_dir(doc_id), ignore_errors=True)

    try:
        src_dir = storage.source_dir(doc_id)
        total = 0
        for sf in sources:
            sf["pages"] = count_pages(src_dir / sf["stored"], sf["kind"])
            total += sf["pages"]
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            doc.source_files = [dict(x) for x in sources]
            doc.page_count = total

        index = 0
        for sf in sources:
            sf_start = index
            for im, has_text in ITERATORS[sf["kind"]](src_dir / sf["stored"]):
                _save_page(doc_id, index, im)
                with session_scope() as s:
                    s.add(Page(doc_id=doc_id, index=index, width=im.width, height=im.height,
                               has_text_layer=has_text))
                    doc = s.get(Document, doc_id)
                    doc.ingest_done = index + 1
                index += 1
                im.close()
            sf["first_index"] = sf_start
            sf["pages"] = index - sf_start

        if index == 0:
            raise ValueError("No pages could be read from the uploaded file(s).")
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            doc.source_files = [dict(x) for x in sources]
            doc.page_count = index
            doc.status = "ready"
            doc.size_bytes = storage.dir_size(storage.doc_dir(doc_id))
        log.info("ingested %s: %d pages", doc_id, index)
    except Exception as e:
        log.exception("ingest failed for %s", doc_id)
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc:
                doc.status = "error"
                doc.error = f"Could not read file: {e}"


def source_for_page(doc: Document, index: int) -> tuple[Path, int] | None:
    """Map a page index back to (pdf path, page number) for text-layer extraction."""
    for sf in doc.source_files:
        start = sf.get("first_index", 0)
        if sf.get("kind") == "pdf" and start <= index < start + sf.get("pages", 0):
            return storage.source_dir(doc.id) / sf["stored"], index - start
    return None
