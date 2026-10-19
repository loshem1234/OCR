"""End-to-end smoke test: upload a scanned PDF, crop, OCR with Tesseract, review, export every format.

Run with:  ADMIN_PASSWORD=test DATA_DIR=/tmp/so-test python -m pytest -q tests
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient  # noqa: E402

DATA = Path(os.environ["DATA_DIR"])

from app.main import app  # noqa: E402
from tests.make_sample import make_pdf  # noqa: E402


def _wait(client, doc_id, statuses, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/docs/{doc_id}").json()
        if d["status"] in statuses:
            return d
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {statuses}; last={d['status']}")


def test_end_to_end():
    pdf = make_pdf(DATA / "sample.pdf")
    with TestClient(app) as c:
        assert c.get("/api/docs").status_code == 401
        assert c.post("/api/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/login", json={"password": os.environ["ADMIN_PASSWORD"]}).status_code == 200

        with pdf.open("rb") as f:
            r = c.post("/api/docs", files={"files": ("sample.pdf", f, "application/pdf")},
                       data={"title": "Elements", "languages": "eng", "engine": "tesseract",
                             "footnote_mode": "separate"})
        assert r.status_code == 200, r.text
        doc_id = r.json()[0]["id"]
        d = _wait(c, doc_id, {"ready", "error"})
        assert d["status"] == "ready", d
        assert d["page_count"] == 3

        # Crop: main text box excludes running head; footnote box at the foot.
        layouts = {"global": {"main": {"x": 0.08, "y": 0.1, "w": 0.86, "h": 0.73},
                              "notes": {"x": 0.08, "y": 0.83, "w": 0.86, "h": 0.12}, "mode": "single"}}
        assert c.patch(f"/api/docs/{doc_id}", json={"layouts": layouts}).status_code == 200
        pages = c.get(f"/api/docs/{doc_id}/pages").json()
        assert all(p["layout_source"] == "global" for p in pages)

        # Per-page override on page 3, then skip nothing.
        c.patch(f"/api/pages/{pages[2]['id']}", json={"override": {**layouts["global"], "notes": None}})
        assert c.get(f"/api/docs/{doc_id}/pages").json()[2]["layout_source"] == "page"

        # Keep only pages 2-3, then include everything again.
        r = c.post(f"/api/docs/{doc_id}/pages/range", json={"start": 2, "end": 3, "isolate": True})
        assert r.json()["updated"] == 2
        assert [p["skip"] for p in c.get(f"/api/docs/{doc_id}/pages").json()] == [True, False, False]
        c.post(f"/api/docs/{doc_id}/pages/range", json={"start": 1, "end": 3, "isolate": True})
        assert not any(p["skip"] for p in c.get(f"/api/docs/{doc_id}/pages").json())

        r = c.post(f"/api/docs/{doc_id}/run", json={"scope": "all"})
        assert r.json()["queued"] == 3
        d = _wait(c, doc_id, {"review"})
        assert d["progress"]["done"] == 3, d

        p1 = c.get(f"/api/pages/{pages[0]['id']}").json()
        assert "infinity" in p1["body"], p1["body"]          # hyphen rejoined across the line break
        assert "ELEMENTS OF THEOLOGY" not in p1["body"]      # running head cropped away
        assert "Parmenides" in p1["notes"]                   # footnote region read separately
        assert p1["body"].startswith("## Chapter One"), p1["body"][:60]

        # Edit in the review editor.
        c.patch(f"/api/pages/{pages[2]['id']}", json={"body": p1["body"].replace("Chapter One", "Chapter Two"),
                                                       "reviewed": True})

        prev = c.get(f"/api/docs/{doc_id}/preview").json()["markdown"]
        assert "[p. 1]" in prev and "[p. 2]" in prev
        assert "## Notes" in prev
        # Paragraph continued across the page break with the marker inline.
        assert "procession of all things from the [p. 2] first principle" in prev, prev

        for fmt, magic in (("txt", b""), ("md", b"# "), ("pdf", b"%PDF"), ("docx", b"PK"), ("epub", b"PK")):
            r = c.get(f"/api/docs/{doc_id}/export/{fmt}?first_number=17")
            assert r.status_code == 200, (fmt, r.text[:300])
            assert r.content.startswith(magic), fmt
            assert len(r.content) > 200, fmt
        txt = c.get(f"/api/docs/{doc_id}/export/txt?first_number=17").text
        assert "[p. 17]" in txt and "[p. 18]" in txt

        st = c.get("/api/storage").json()
        assert st["used_bytes"] > 0
        assert c.post("/api/docs/bulk", json={"action": "purge", "ids": [doc_id]}).status_code == 200
        assert c.get(f"/api/docs/{doc_id}").json()["images_purged"] is True
        assert c.delete(f"/api/docs/{doc_id}").status_code == 200
        assert c.get(f"/api/docs/{doc_id}").status_code == 404
    shutil.rmtree(DATA, ignore_errors=True)
