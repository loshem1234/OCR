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

def _normalize(im: Image.Image, scholarly: bool = False) -> Image.Image:
    im = ImageOps.exif_transpose(im)
    if im.mode != "L":
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, "white")
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        im = im.convert("L")
    w, h = im.size
    limit = settings.scholarly_max_side if scholarly else MAX_SIDE
    if max(w, h) > limit:
        s = limit / max(w, h)
        im = im.resize((int(w * s), int(h * s)), Image.LANCZOS)
    return im


def native_scan_dpi(page) -> int | None:
    """If the PDF page is essentially one scanned image, its native resolution (pixels per inch)."""
    try:
        infos = page.get_image_info()
    except Exception:
        return None
    area = page.rect.width * page.rect.height or 1
    best = None
    for info in infos:
        bx = pymupdf.Rect(info.get("bbox", (0, 0, 0, 0)))
        if bx.width <= 0 or (bx.width * bx.height) / area < 0.6:
            continue
        dpi = info.get("width", 0) / (bx.width / 72.0)
        best = max(best or 0, dpi)
    return int(round(best)) if best else None


def choose_dpi(page, scholarly: bool) -> tuple[int, int | None]:
    """(render dpi, native scan dpi). Scholarly: render at the scan's own resolution (clamped), so no source
    pixel is thrown away and none is invented; vector/text pages use SCHOLARLY_RENDER_DPI."""
    native = native_scan_dpi(page)
    if not scholarly:
        return settings.render_dpi, native
    if native:
        return max(settings.scholarly_min_dpi, min(settings.scholarly_max_dpi, native)), native
    return settings.scholarly_render_dpi, None


def iter_pdf(path: Path, scholarly: bool = False) -> Iterator[tuple[Image.Image, bool, dict]]:
    max_side = settings.scholarly_max_side if scholarly else MAX_SIDE
    with pymupdf.open(path) as d:
        for page in d:
            rect = page.rect
            dpi, native = choose_dpi(page, scholarly)
            zoom = dpi / 72.0
            longest = max(rect.width, rect.height) * zoom
            capped = False
            if longest > max_side:
                zoom = max_side / max(rect.width, rect.height)
                capped = True
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
            im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            has_text = len(page.get_text("text").strip()) > 40
            yield im, has_text, {"source": "pdf", "dpi": int(round(zoom * 72)), "native_dpi": native,
                                 "capped": capped}


def _image_dpi(im: Image.Image) -> int | None:
    try:
        d = im.info.get("dpi")
        return int(round(float(d[0]))) if d and float(d[0]) > 50 else None
    except Exception:
        return None


def iter_image(path: Path, scholarly: bool = False) -> Iterator[tuple[Image.Image, bool, dict]]:
    with Image.open(path) as im:
        dpi = _image_dpi(im)
        fmt = im.format
        for frame in ImageSequence.Iterator(im):
            out = _normalize(frame.copy(), scholarly)
            yield out, False, {"source": f"image/{(fmt or '').lower()}", "dpi": dpi, "native_dpi": dpi,
                               "capped": out.size != frame.size}


def iter_djvu(path: Path, scholarly: bool = False) -> Iterator[tuple[Image.Image, bool, dict]]:
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
                yield _normalize(im.copy(), scholarly), False, {"source": "djvu", "dpi": _image_dpi(im)}
            out.unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def iter_zip(path: Path, scholarly: bool = False) -> Iterator[tuple[Image.Image, bool, dict]]:
    with zipfile.ZipFile(path) as z:
        names = sorted(
            (n for n in z.namelist()
             if Path(n).suffix.lower() in IMAGE_EXTS and not n.startswith("__MACOSX") and not n.endswith("/")),
            key=natural_key,
        )
        for n in names:
            with z.open(n) as f, Image.open(f) as im:
                dpi = _image_dpi(im)
                for frame in ImageSequence.Iterator(im):
                    yield _normalize(frame.copy(), scholarly), False, {"source": "zip", "dpi": dpi, "file": n}


ITERATORS = {"pdf": iter_pdf, "image": iter_image, "djvu": iter_djvu, "zip": iter_zip}


# ---------------------------------------------------------------- main entry

def _save_page(doc_id: str, index: int, im: Image.Image, lossless: bool = False) -> str:
    storage.pages_dir(doc_id).mkdir(parents=True, exist_ok=True)
    storage.thumbs_dir(doc_id).mkdir(parents=True, exist_ok=True)
    ext = "png" if lossless else storage.image_ext()
    out = storage.pages_dir(doc_id) / f"{index + 1:04d}.{ext}"
    if ext == "png":
        im.save(out, "PNG", optimize=True)
    else:
        im.save(out, "JPEG", quality=settings.jpeg_quality, optimize=True)
    th = im.copy()
    th.thumbnail((220, 320))
    th.save(storage.thumb_path(doc_id, index), "JPEG", quality=80)  # thumbnails are never OCR input
    return ext


def ingest_document(doc_id: str) -> None:
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return
        sources = list(doc.source_files)
        from .ocr.scholarly import PROFILE, profile_for
        scholarly = profile_for(dict(doc.settings or {})) == PROFILE
        split = bool((doc.settings or {}).get("split_spreads"))
        # Restart-safe: wipe any half-finished previous attempt.
        for p in list(doc.pages):
            s.delete(p)
        doc.ingest_done = 0
        doc.status = "ingesting"
    shutil.rmtree(storage.pages_dir(doc_id), ignore_errors=True)
    shutil.rmtree(storage.thumbs_dir(doc_id), ignore_errors=True)
    shutil.rmtree(storage.spreads_dir(doc_id), ignore_errors=True)
    shutil.rmtree(storage.doc_dir(doc_id) / "previews", ignore_errors=True)

    try:
        src_dir = storage.source_dir(doc_id)
        total = 0
        for sf in sources:
            sf["pages"] = count_pages(src_dir / sf["stored"], sf["kind"])
            total += sf["pages"]
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            doc.source_files = [dict(x) for x in sources]
            doc.page_count = total * 2 if split else total  # estimate for the progress bar

        index, scan_no = 0, 0
        for sf in sources:
            sf_start = index
            for im, has_text, meta in ITERATORS[sf["kind"]](src_dir / sf["stored"], scholarly):
                parts = [(im, meta, has_text)]
                if split:
                    from .spreads import detect_gutter, split_at
                    det = detect_gutter(im)
                    if det["is_spread"]:
                        scan_no += 1
                        _save_spread(doc_id, scan_no, im, lossless=scholarly)
                        left, right = split_at(im, det["x"])
                        sp = {"scan": scan_no, "split_x": det["x"], "spread_width": im.width,
                              "spread_height": im.height, "auto": True, "confidence": det["confidence"]}
                        # a text layer can't be mapped onto half a page, so it is not used for split scans
                        parts = [(left, {**meta, "spread": {**sp, "side": "left"}}, False),
                                 (right, {**meta, "spread": {**sp, "side": "right"}}, False)]
                for part, pmeta, ptext in parts:
                    fmt = _save_page(doc_id, index, part, lossless=scholarly)
                    pmeta = {**pmeta, "stored_format": fmt, "lossless": fmt == "png", "width": part.width,
                             "height": part.height, "profile": "scholarly_greek" if scholarly else "standard"}
                    with session_scope() as s:
                        s.add(Page(doc_id=doc_id, index=index, width=part.width, height=part.height,
                                   has_text_layer=ptext, image_meta=pmeta))
                        doc = s.get(Document, doc_id)
                        doc.ingest_done = index + 1
                        if doc.page_count < index + 1:
                            doc.page_count = index + 1
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


def _save_spread(doc_id: str, scan: int, im: Image.Image, lossless: bool) -> None:
    """Keep the whole two-page scan so the split can be moved later."""
    d = storage.spreads_dir(doc_id)
    d.mkdir(parents=True, exist_ok=True)
    if lossless:
        im.save(d / f"S{scan:04d}.png", "PNG", optimize=True)
    else:
        im.save(d / f"S{scan:04d}.jpg", "JPEG", quality=settings.jpeg_quality, optimize=True)


def resplit(doc_id: str, scan: int, x: int, pages: list) -> None:
    """Cut a stored two-page scan again at x and rewrite both halves (images + thumbnails)."""
    from .spreads import split_at
    with Image.open(storage.spread_path(doc_id, scan)) as im:
        im.load()
        left, right = split_at(im.convert("L"), x)
    for p in pages:
        part = left if p.image_meta["spread"]["side"] == "left" else right
        old = storage.page_image_path(doc_id, p.index)
        lossless = old.suffix.lower() == ".png"
        for ext in ("png", "jpg"):
            (storage.pages_dir(doc_id) / f"{p.index + 1:04d}.{ext}").unlink(missing_ok=True)
        storage.preview_path(doc_id, p.index).unlink(missing_ok=True)
        _save_page(doc_id, p.index, part, lossless=lossless)
        p.width, p.height = part.width, part.height
        meta = dict(p.image_meta)
        meta["spread"] = {**meta["spread"], "split_x": int(x), "auto": False}
        meta["width"], meta["height"] = part.width, part.height
        p.image_meta = meta


def source_for_page(doc: Document, index: int) -> tuple[Path, int] | None:
    """Map a page index back to (pdf path, page number) for text-layer extraction."""
    if (doc.settings or {}).get("split_spreads"):
        return None  # page indices no longer correspond to PDF pages
    for sf in doc.source_files:
        start = sf.get("first_index", 0)
        if sf.get("kind") == "pdf" and start <= index < start + sf.get("pages", 0):
            return storage.source_dir(doc.id) / sf["stored"], index - start
    return None
