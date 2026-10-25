"""End-to-end: the scholarly Greek profile through the real API, worker, database and exporters
(Gemini replaced by a fake that reads the synthetic page's ground truth)."""
from __future__ import annotations

import json
import os
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, create_engine, inspect, text

from app.config import settings
from app.db import Base, migrate_columns
from app.main import app
from app.ocr import gemini_engine
from tests.scholarly_fakes import FakeScholarModels
from tools.synth_greek import APPARATUS, PASSAGES, render_page, scan

DATA = Path(os.environ["DATA_DIR"])


def _wait(c, doc_id, statuses, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = c.get(f"/api/docs/{doc_id}").json()
        if d["status"] in statuses:
            return d
        time.sleep(0.4)
    raise AssertionError(f"timeout; status={d['status']}")


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    """Two scanned pages (400 dpi images inside a PDF) with exact ground truth."""
    imgs, gts, boxes = [], [], None
    for i, ps in enumerate((PASSAGES[0:2], PASSAGES[2:4])):
        master, gt, bx = render_page(list(ps), dpi=600, apparatus=APPARATUS[i], page_no=str(17 + i))
        imgs.append(scan(master, from_dpi=600, to_dpi=400, seed=i).convert("RGB"))
        gts.append(gt)
        boxes = boxes or bx
    pdf = tmp_path_factory.mktemp("book") / "greek_book.pdf"
    imgs[0].save(pdf, "PDF", save_all=True, append_images=imgs[1:], resolution=400)
    return pdf, gts, boxes


def test_scholarly_end_to_end(book, monkeypatch):
    pdf, gts, boxes = book
    all_gt = gts[0] + gts[1]
    wrong = 1  # Gemini will misread one accent on the second printed line
    fake = FakeScholarModels(all_gt, edits={wrong: all_gt[wrong].replace("ἁρμόζει", "ἁρμὀζει")
                                             if "ἁρμόζει" in all_gt[wrong] else all_gt[wrong] + "ς"})
    monkeypatch.setattr(settings, "gemini_api_key", "fake")
    monkeypatch.setattr(settings, "scholarly_segment_lines", 100)   # one strip per page: fake reads in order
    monkeypatch.setattr(settings, "ocr_concurrency", 1)
    monkeypatch.setattr(gemini_engine, "_get_client", lambda: SimpleNamespace(models=fake))

    with TestClient(app) as c:
        c.post("/api/login", json={"password": os.environ["ADMIN_PASSWORD"]})
        with pdf.open("rb") as f:
            r = c.post("/api/docs", files={"files": ("greek_book.pdf", f, "application/pdf")},
                       data={"title": "Iamblichus test", "languages": "grc", "footnote_mode": "drop"})
        doc_id = r.json()[0]["id"]
        d = _wait(c, doc_id, {"ready", "error"})
        assert d["status"] == "ready"
        full = c.get(f"/api/docs/{doc_id}").json()
        assert full["effective_profile"] == "scholarly_greek"

        pages = c.get(f"/api/docs/{doc_id}/pages").json()
        p1 = c.get(f"/api/pages/{pages[0]['id']}").json()
        meta = p1["image_meta"]
        assert meta["lossless"] and meta["stored_format"] == "png"         # lossless master
        assert meta["native_dpi"] == 400 and meta["dpi"] == 400             # rendered at the scan's own DPI
        img = c.get(f"/api/pages/{pages[0]['id']}/image?full=1")
        assert img.headers["content-type"] == "image/png"

        layout = {"main": boxes["main"], "notes": boxes.get("notes"), "mode": "single", "split": 0.5}
        c.patch(f"/api/docs/{doc_id}", json={"layouts": {"global": layout, "carry_forward": True}})
        c.post(f"/api/docs/{doc_id}/run", json={"scope": "all"})
        _wait(c, doc_id, {"review"})

        p1 = c.get(f"/api/pages/{pages[0]['id']}").json()
        assert p1["text_format"] == "lines" and p1["profile"] == "scholarly_greek"
        assert p1["witness_status"] in ("engines-agree", "engines-disagree")
        assert p1["engine"] == "dual"
        # working text = Gemini's physical lines, verbatim (including its deliberate error)
        body_lines = [l for l in p1["body"].splitlines() if l.strip()]
        assert body_lines == [fake.edits.get(i, g) for i, g in enumerate(gts[0])]
        # the misreading is reported as a disagreement with location and both readings
        items = p1["collation"]["regions"]["main"]["items"]
        assert any(it["b"] and it["b"]["line"] == wrong for it in items)
        assert p1["provenance"]["image"]["lossless"] is True
        assert p1["provenance"]["tesseract"]["lang"] == "grc"

        # both raw witnesses are stored, untouched
        recs = c.get(f"/api/pages/{pages[0]['id']}/recognitions?raw=1").json()
        engines = {(r["engine"], r["status"]) for r in recs}
        assert ("tesseract", "ok") in engines and ("gemini", "ok") in engines
        g = next(r for r in recs if r["engine"] == "gemini")
        assert json.loads(g["raw"])["lines"][wrong]["text"] == fake.edits[wrong]
        t = next(r for r in recs if r["engine"] == "tesseract")
        assert t["raw"].startswith("level\tpage_num")

        # human correction, then a re-run: the corrected text survives as a revision and can be restored
        corrected = p1["body"].replace(fake.edits[wrong], gts[0][wrong])
        c.patch(f"/api/pages/{p1['id']}", json={"body": corrected, "reviewed": True})
        assert c.get(f"/api/docs/{doc_id}/pages").json()[0]["witness_status"] == "human-reviewed"
        fake.pos = 0
        c.post(f"/api/pages/{p1['id']}/rerun", json={"engine": ""})
        _wait(c, doc_id, {"review"})
        revs = c.get(f"/api/pages/{p1['id']}/revisions").json()
        mine = next(r for r in revs if r["edited"] and r["reviewed"])
        assert mine["reason"] == "before OCR re-run"
        c.post(f"/api/pages/{p1['id']}/revisions/{mine['id']}/restore")
        assert c.get(f"/api/pages/{p1['id']}").json()["body"] == corrected
        assert len(c.get(f"/api/pages/{p1['id']}/recognitions").json()) >= 4   # older run kept too

        # exports: raw UTF-8 Greek, character-faithful, no OCR metadata, no apparatus
        txt = c.get(f"/api/docs/{doc_id}/export/txt?page_markers=0&title_page=0").content
        s = txt.decode("utf-8")
        assert unicodedata.is_normalized("NFC", s)
        for word in ("⟨ἔφη⟩", "[καὶ", "†ἀκριβῶς†", "κἀγὼ", "ἀΐδιος", "τῷ", "ὅλῳ;", "Πειραιᾶ"):
            if any(word in g for g in all_gt):
                assert word in s, word
        assert "‖" not in s and "Kiessling" not in s           # apparatus excluded
        assert "[p." not in s and "OCR" not in s and "confidence" not in s
        # printed lines are joined into prose at export (the stored lines are untouched)
        assert gts[0][0] + " " + gts[0][1] in s
        diplomatic = c.get(f"/api/docs/{doc_id}/export/txt?page_markers=0&join_lines=0").content.decode()
        assert gts[0][0] + "\n" + gts[0][1] in diplomatic
        gp = c.get(f"/api/docs/{doc_id}/export/txt?page_markers=0&greek_punct=1").content.decode()
        assert "·" in gp
        for fmt, magic in (("md", b"# "), ("pdf", b"%PDF"), ("docx", b"PK"), ("epub", b"PK")):
            r = c.get(f"/api/docs/{doc_id}/export/{fmt}")
            assert r.status_code == 200 and r.content.startswith(magic), fmt

        audit = c.get(f"/api/docs/{doc_id}/collation.json").json()
        pg = audit["pages"][0]
        assert pg["verification"] == "human-reviewed"
        assert audit["pages"][1]["verification"] in ("unverified", "engines agree (NOT verified against the page)")
        assert set(pg["witnesses"]) >= {"tesseract:main", "gemini:main"}

        # resumption: an interrupted page goes back in the queue
        from app.db import Page, session_scope
        from app.worker import Worker
        with session_scope() as s_:
            s_.get(Page, pages[1]["id"]).status = "running"
        n, _ = Worker.requeue_interrupted()
        assert n == 1
        with session_scope() as s_:
            assert s_.get(Page, pages[1]["id"]).status == "queued"
            s_.get(Page, pages[1]["id"]).status = "done"


def test_gemini_unavailable_gives_single_witness(book, monkeypatch):
    pdf, gts, boxes = book
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with TestClient(app) as c:
        c.post("/api/login", json={"password": os.environ["ADMIN_PASSWORD"]})
        with pdf.open("rb") as f:
            doc_id = c.post("/api/docs", files={"files": ("b.pdf", f, "application/pdf")},
                            data={"languages": "grc"}).json()[0]["id"]
        _wait(c, doc_id, {"ready"})
        c.patch(f"/api/docs/{doc_id}", json={"layouts": {"global": {"main": boxes["main"], "notes": None,
                                                                    "mode": "single", "split": 0.5}}})
        c.post(f"/api/docs/{doc_id}/run", json={"scope": "all"})
        _wait(c, doc_id, {"review"})
        p = c.get(f"/api/pages/{c.get(f'/api/docs/{doc_id}/pages').json()[0]['id']}").json()
        assert p["witness_status"] == "single-witness" and p["engine"] == "tesseract"
        assert "NOT been cross-checked" in p["warning"]
        assert p["collation"]["regions"] == {}
        c.delete(f"/api/docs/{doc_id}")


def test_standard_documents_unchanged(book):
    """A Latin/English document still uses the standard pipeline and prose text format."""
    from app.ocr.scholarly import profile_for
    assert profile_for({"languages": ["eng"]}) == "standard"
    assert profile_for({"languages": ["lat", "eng"]}) == "standard"


def test_export_page_boundary_and_verse_for_line_format():
    from app.export.assemble import assemble
    from app.export.options import ExportOptions
    from app.export.render_text import render_txt
    pages = [SimpleNamespace(index=0, skip=False, text_format="lines", printed_page="",
                             body="Πᾶν πλῆθος μετέχει\nπῃ τοῦ ἑνός, ἐπιχεί-\nρησιν δὲ τὴν πλῆ-", right="", notes=""),
             SimpleNamespace(index=1, skip=False, text_format="lines", printed_page="",
                             body="θος ὁρῶμεν.\n\n%verse\nΜῆνιν ἄειδε θεὰ\nοὐλομένην, ἣ μυρί᾽\n\n## ΚΕΦ. Β", right="", notes="")]
    doc = SimpleNamespace(settings={"footnote_mode": "drop", "languages": ["grc"]}, pages=pages, title="t", author="")
    o = ExportOptions()
    o.markup = False
    out = render_txt(assemble(doc, o), doc, o)
    assert "ἐπιχείρησιν δὲ τὴν πλῆθος [p. 2] ὁρῶμεν." in out           # hyphens rejoined, marker after the word
    assert "Μῆνιν ἄειδε θεὰ\nοὐλομένην, ἣ μυρί᾽" in out                # verse keeps its lines
    assert "ΚΕΦ. Β" in out
    o2 = ExportOptions()
    o2.markup, o2.join_lines, o2.page_markers = False, False, False
    out2 = render_txt(assemble(doc, o2), doc, o2)
    assert "μετέχει\nπῃ τοῦ ἑνός, ἐπιχεί-" in out2                      # diplomatic: printed lines as printed


def test_migration_adds_columns_to_old_schema(tmp_path):
    """An existing database from before the upgrade gets the new columns; old rows are kept."""
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    new_cols = {"text_format", "profile", "witness_status", "collation_data", "provenance", "image_meta"}
    old_md = MetaData()
    for name in ("documents", "pages"):
        src = Base.metadata.tables[name]
        Table(name, old_md, *[Column(cc.name, cc.type, primary_key=cc.primary_key)
                              for cc in src.columns if cc.name not in new_cols])
    old_md.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO documents (id, title, status) VALUES ('d1', 'Old', 'review')"))
        conn.execute(text("INSERT INTO pages (id, doc_id, \"index\", body, status) VALUES (1, 'd1', 0, 'ὁ λόγος', 'done')"))
    Base.metadata.create_all(eng)            # new tables only
    added = migrate_columns(eng)
    assert {a.split(".")[1] for a in added if a.startswith("pages.")} == new_cols
    assert migrate_columns(eng) == []        # idempotent
    with eng.begin() as conn:
        row = conn.execute(text("SELECT body, text_format FROM pages WHERE id = 1")).one()
    assert row == ("ὁ λόγος", "prose")
    assert inspect(eng).has_table("recognitions") and inspect(eng).has_table("page_revisions")


def test_deployment_configuration():
    root = Path(__file__).resolve().parent.parent
    docker = (root / "Dockerfile").read_text()
    assert "tesseract-ocr-grc" in docker and "fetch_models.py" in docker
    fetch = (root / "docker" / "fetch_models.py").read_text()
    assert "dfc9bda286cd9d8755b1832e5731a5425d1cf0803393a5fa6dee078466178cf1" in fetch
    req = (root / "requirements.txt").read_text()
    assert "numpy==" in req and "rapidfuzz==" in req
    assert json.loads((root / "railway.json").read_text())["deploy"]["numReplicas"] == 1
