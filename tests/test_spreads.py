"""Two-page scans: gutter detection, splitting at import, moving the split, re-import guard."""
from __future__ import annotations

import os
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.spreads import detect_gutter, split_at
from tools.synth_greek import PASSAGES, render_page, scan


def spread(left_passages, right_passages, *, shadow=False, offset=0, gap=60):
    L, _, _ = render_page(left_passages, dpi=300)
    R, _, _ = render_page(right_passages, dpi=300)
    W, H = L.width + R.width + gap, L.height
    sp = Image.new("L", (W, H), 250)
    sp.paste(L, (0, 0))
    sp.paste(R, (L.width + gap, 0))
    true_x = L.width + gap // 2
    if shadow:  # dark binding shadow in the gutter
        a = np.asarray(sp, dtype=np.float32)
        xs = np.arange(W)
        prof = 1 - 0.75 * np.exp(-((xs - true_x) / 90.0) ** 2)
        sp = Image.fromarray((a * prof[None, :]).clip(0, 255).astype("uint8"))
    if offset:
        sp = sp.crop((offset, 0, W, H))
        true_x -= offset
    return scan(sp, from_dpi=300, to_dpi=300, seed=2), true_x


@pytest.mark.parametrize("shadow,offset", [(False, 0), (True, 0), (True, 150)])
def test_gutter_detection(shadow, offset):
    im, true_x = spread(PASSAGES[0:2], PASSAGES[2:4], shadow=shadow, offset=offset)
    d = detect_gutter(im)
    assert d["is_spread"]
    assert abs(d["x"] - true_x) < 0.03 * im.width
    left, right = split_at(im, d["x"])
    assert left.width + right.width == im.width


def test_portrait_page_is_not_split():
    page, _, _ = render_page(PASSAGES[0:2], dpi=200)
    assert detect_gutter(page)["is_spread"] is False


def _wait(c, doc_id, statuses, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = c.get(f"/api/docs/{doc_id}").json()
        if d["status"] in statuses:
            return d
        time.sleep(0.3)
    raise AssertionError(d["status"])


def test_split_import_resplit_and_reimport(tmp_path):
    s1, _ = spread(PASSAGES[0:2], PASSAGES[2:4])
    s2, _ = spread(PASSAGES[4:6], PASSAGES[0:2], shadow=True)
    cover, _, _ = render_page(PASSAGES[3:4], dpi=300)
    pdf = tmp_path / "spreads.pdf"
    cover.convert("RGB").save(pdf, "PDF", save_all=True, append_images=[s1.convert("RGB"), s2.convert("RGB")], resolution=300)

    with TestClient(app) as c:
        c.post("/api/login", json={"password": os.environ["ADMIN_PASSWORD"]})
        with pdf.open("rb") as f:
            doc_id = c.post("/api/docs", files={"files": ("spreads.pdf", f, "application/pdf")},
                            data={"languages": "grc", "split_spreads": "true"}).json()[0]["id"]
        d = _wait(c, doc_id, {"ready", "error"})
        assert d["status"] == "ready" and d["page_count"] == 5          # cover + 2 scans × 2
        pages = c.get(f"/api/docs/{doc_id}/pages").json()
        assert pages[0]["spread"] is None                                # portrait cover untouched
        assert [p["spread"]["side"] for p in pages[1:]] == ["left", "right", "left", "right"]
        assert pages[1]["spread"]["scan"] == pages[2]["spread"]["scan"] == 1
        assert not any(p["has_text_layer"] for p in pages[1:])
        assert pages[1]["width"] + pages[2]["width"] == pages[1]["spread"]["spread_width"]

        # the whole scan is kept and viewable
        r = c.get(f"/api/pages/{pages[1]['id']}/spread")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

        # move the cut: both halves are re-cut from the stored scan
        res = c.post(f"/api/pages/{pages[2]['id']}/resplit", json={"fraction": 0.45}).json()
        assert len(res) == 2
        left = next(q for q in res if q["spread"]["side"] == "left")
        assert abs(left["width"] / left["spread"]["spread_width"] - 0.45) < 0.002
        assert left["spread"]["auto"] is False
        img = Image.open(__import__("io").BytesIO(c.get(f"/api/pages/{left['id']}/image?full=1").content))
        assert img.width == left["width"]
        assert c.post(f"/api/pages/{pages[2]['id']}/resplit", json={"fraction": 0.99}).status_code == 400
        assert c.post(f"/api/pages/{pages[0]['id']}/resplit", json={"fraction": 0.5}).status_code == 400

        # re-import without splitting: allowed (no OCR text yet)
        assert c.post(f"/api/docs/{doc_id}/reimport", json={"split_spreads": False}).status_code == 200
        d = _wait(c, doc_id, {"ready"})
        assert d["page_count"] == 3

        # once a page has text, re-import needs explicit confirmation
        p0 = c.get(f"/api/docs/{doc_id}/pages").json()[0]
        c.patch(f"/api/pages/{p0['id']}", json={"body": "ὁ λόγος"})
        r = c.post(f"/api/docs/{doc_id}/reimport", json={"split_spreads": True})
        assert r.status_code == 409 and "discard" in r.json()["detail"]
        assert c.post(f"/api/docs/{doc_id}/reimport", json={"split_spreads": True,
                                                            "confirm_discard_text": True}).status_code == 200
        assert _wait(c, doc_id, {"ready"})["page_count"] == 5
        assert c.get(f"/api/docs/{doc_id}").json()["settings"]["split_spreads"] is True
        c.delete(f"/api/docs/{doc_id}")
