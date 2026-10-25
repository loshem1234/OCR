"""File layout on the data volume and storage accounting."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import settings

# DATA_DIR/
#   docs/<doc_id>/source/<original files>
#   docs/<doc_id>/pages/0001.jpg     full-resolution page images
#   docs/<doc_id>/thumbs/0001.jpg    ~220px thumbnails
#   tmp/                             export scratch space


def docs_root() -> Path:
    p = settings.data_dir / "docs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tmp_dir() -> Path:
    p = settings.data_dir / "tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def doc_dir(doc_id: str) -> Path:
    return docs_root() / doc_id


def source_dir(doc_id: str) -> Path:
    return doc_dir(doc_id) / "source"


def pages_dir(doc_id: str) -> Path:
    return doc_dir(doc_id) / "pages"


def thumbs_dir(doc_id: str) -> Path:
    return doc_dir(doc_id) / "thumbs"


def image_ext() -> str:
    return "png" if settings.page_image_format == "png" else "jpg"


def page_image_path(doc_id: str, index: int) -> Path:
    d = pages_dir(doc_id)
    for ext in ("png", "jpg"):
        p = d / f"{index + 1:04d}.{ext}"
        if p.exists():
            return p
    return d / f"{index + 1:04d}.{image_ext()}"


def preview_path(doc_id: str, index: int) -> Path:
    return doc_dir(doc_id) / "previews" / f"{index + 1:04d}.jpg"


def display_image(doc_id: str, index: int) -> Path:
    """Browser display copy. Lossless masters can be several MB; the crop/review views get a JPEG preview
    (≤2400 px). OCR always reads the master, never this file."""
    master = page_image_path(doc_id, index)
    if master.suffix.lower() != ".png" or not master.exists() or master.stat().st_size < 1_500_000:
        return master
    prev = preview_path(doc_id, index)
    if prev.exists() and prev.stat().st_mtime >= master.stat().st_mtime:
        return prev
    from PIL import Image
    prev.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(master) as im:
        im = im.convert("L")
        if max(im.size) > 2400:
            f = 2400 / max(im.size)
            im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
        im.save(prev, "JPEG", quality=88)
    return prev


def thumb_path(doc_id: str, index: int) -> Path:
    return thumbs_dir(doc_id) / f"{index + 1:04d}.jpg"


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def delete_doc_files(doc_id: str) -> None:
    shutil.rmtree(doc_dir(doc_id), ignore_errors=True)


def purge_images(doc_id: str) -> None:
    """Free space but keep the OCR text: drop page images and originals, keep thumbnails."""
    shutil.rmtree(pages_dir(doc_id), ignore_errors=True)
    shutil.rmtree(source_dir(doc_id), ignore_errors=True)
    shutil.rmtree(doc_dir(doc_id) / "previews", ignore_errors=True)


def storage_report() -> dict:
    used = dir_size(settings.data_dir)
    du = shutil.disk_usage(settings.data_dir)
    if settings.storage_limit_gb > 0:
        capacity = int(settings.storage_limit_gb * 1024 ** 3)
        free = max(0, capacity - used)
    else:
        capacity = du.total
        free = du.free
    pct = (used / capacity * 100) if capacity else 0
    return {
        "used_bytes": used,
        "capacity_bytes": capacity,
        "free_bytes": free,
        "percent": round(pct, 1),
        "warning": pct >= 85 or free < 500 * 1024 ** 2,
    }
