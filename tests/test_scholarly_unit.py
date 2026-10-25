"""Unit tests for the scholarly Greek profile: Unicode policy, collation categories, validation, artifacts,
segmentation, strict language models, independence of the two witnesses."""
from __future__ import annotations

import json
import unicodedata
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.layout import crop_box, main_minus_notes
from app.ocr import gemini_engine, scholarly
from app.ocr.collation import classify_pair, collate
from app.ocr.greek import clusters, greek_punctuation, hard_artifacts, mixed_script_words, nfc
from app.ocr.metrics import score
from app.ocr.segment import blank_rows, edge_contacts, ink_mask, line_bands, segments
from app.ocr.tesseract_engine import LanguageUnavailable, strict_lang_string
from tests.scholarly_fakes import FakeScholarModels
from tools.synth_greek import render_page, scan, PASSAGES, APPARATUS

# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def page():
    master, gt, boxes = render_page(PASSAGES[:3], dpi=600, apparatus=APPARATUS[0])
    im = scan(master, from_dpi=600, to_dpi=400, seed=3)
    return im, gt, boxes


# ---------------------------------------------------------------- Unicode policy

def test_nfc_is_canonical_only():
    decomposed = "ᾆ"          # alpha + smooth + circumflex + iota subscript
    assert nfc(decomposed) == "ᾆ"
    assert unicodedata.normalize("NFD", nfc(decomposed)) == unicodedata.normalize("NFD", decomposed)
    # oxia and tonos are canonically identical; ano teleia and Greek question mark map to · and ;
    assert nfc("ά") == "ά" and nfc("·") == "·" and nfc(";") == ";"
    assert all(len(c.marks) for c in clusters("ἆᾳῷ"))


def test_greek_punctuation_option_only_touches_greek_context():
    assert greek_punctuation("λέγω· τί ἐστιν;") == "λέγω· τί ἐστιν;"
    assert greek_punctuation("Note: a·b; c") == "Note: a·b; c"


def test_precomposed_vs_decomposed_is_agreement():
    a = ["ἀνθρώπῳ ἆρα ἐΐ"]
    b = [unicodedata.normalize("NFD", a[0])]
    r = collate(a, b)
    assert r["status"] == "engines-agree" and r["agreement"] == 1.0 and not r["items"]


# ---------------------------------------------------------------- collation categories

@pytest.mark.parametrize("a,b,cat", [
    ("γάρ", "γὰρ", "accent"),
    ("ἐνός", "ἑνός", "breathing"),
    ("τῇ", "τῆ", "iota-subscript"),
    ("ἀΐδιος", "ἀίδιος", "diaeresis"),
    ("ουτε", "οὔτε", "diacritics"),
    ("⟨ἔφη⟩", "ἔφη", "editorial-sign"),
    ("[καὶ", "καὶ", "editorial-sign"),
    ("λέγω·", "λέγω,", "punctuation"),
    ("λόγος", "λόγοσ", "letter"),
    ("δ᾽", "δ'", "apostrophe-form"),
])
def test_classify_pair(a, b, cat):
    assert classify_pair(a, b)[0] == cat


def test_mixed_script_artifact_detected():
    word = "λόγo" + "ς"  # Latin 'o' inside a Greek word
    assert mixed_script_words(word) == [word]
    assert classify_pair("λόγος", word)[0] == "script-artifact"


def test_collation_lines_omissions_duplicates_and_trace():
    a = ["α β γ", "δ ε ζ", "η θ ι"]
    b = ["α β γ", "η θ ι θ ι"]
    r = collate(a, b)
    cats = {it["category"] for it in r["items"]}
    assert "line-omission" in cats and "duplication" in cats
    lo = next(it for it in r["items"] if it["category"] == "line-omission")
    assert lo["a"]["line"] == 1 and lo["a"]["text"] == "δ ε ζ"   # traceable to Tesseract line 2
    assert all(it["status"] == "unresolved" for it in r["items"])
    assert r["status"] == "engines-disagree"


def test_agreement_is_never_called_verified():
    r = collate(["Πᾶν πλῆθος"], ["Πᾶν πλῆθος"])
    assert r["status"] == "engines-agree"
    assert "verified" not in json.dumps(r)


def test_line_end_hyphenation_is_word_division_not_error():
    r = collate(["ἐπιχεί-", "ρησιν ἐκ"], ["ἐπιχείρησιν ἐκ"])
    assert [it["category"] for it in r["items"]] == ["word-division"]
    assert r["substantive"] == 0


# ---------------------------------------------------------------- metrics

def test_metrics_count_diacritics_and_symbols():
    s = score("εἶναι ⟨καὶ⟩ τῇ·", "εἰναι καὶ τη,").summary()
    assert s["diacritics"]["circumflex"]["missing"] == 2   # εἶναι and τῇ
    assert s["diacritics"]["iota_subscript"]["missing"] == 1
    assert s["symbol_errors"].get("⟨") == 1 and s["symbol_errors"].get("⟩") == 1
    assert score("ἆρα", unicodedata.normalize("NFD", "ἆρα")).summary()["CER"] == 0.0


# ---------------------------------------------------------------- artifacts & validation

@pytest.mark.parametrize("bad", [
    '{"lines": [', "```json", 'τοῦ "text": λόγος', "λόγος\\nκαὶ", "TODO", "λό�γος", "<br>",
])
def test_hard_artifacts(bad):
    assert hard_artifacts(bad)


def _resp_lines(lines, **kw):
    return {"printed_page": "", "lines": [{"n": i + 1, "text": t, "starts_paragraph": False, "kind": "text",
                                           "uncertain": []} for i, t in enumerate(lines)],
            "complete": True, "omitted": [], "illegible": [], **kw}


def test_validate_lines_accepts_good_and_rejects_bad():
    lines, warns = gemini_engine.validate_lines(_resp_lines(["Πᾶν πλῆθος", "μετέχει"]), est_lines=2)
    assert [l["text"] for l in lines] == ["Πᾶν πλῆθος", "μετέχει"] and not warns
    bad = [
        {"lines": []},                                                      # missing fields
        {**_resp_lines(["α"]), "lines": [{"n": 2, "text": "α", "starts_paragraph": False, "kind": "text",
                                          "uncertain": []}]},              # broken numbering
        _resp_lines(["α\nβ"]),                                              # joined lines
        _resp_lines(['{"text": "α"}']),                                     # JSON inside a line
        _resp_lines(["α"]) | {"lines": "α"},                                # wrong type
    ]
    for b in bad:
        with pytest.raises(gemini_engine.InvalidResponse):
            gemini_engine.validate_lines(b, est_lines=1)
    with pytest.raises(gemini_engine.InvalidResponse):
        gemini_engine.validate_lines(_resp_lines([]), est_lines=6)          # empty although text visible
    with pytest.raises(gemini_engine.InvalidResponse):
        gemini_engine.validate_lines(_resp_lines(["α"]), est_lines=8)      # gross omission
    _, warns = gemini_engine.validate_lines(_resp_lines(["α", "β"]), est_lines=3)
    assert any("line count" in w for w in warns)


# ---------------------------------------------------------------- segmentation & crops

def test_segments_cut_only_blank_rows_and_reassemble_exactly(page):
    im, gt, boxes = page
    main = crop_box(im, boxes["main"])
    bands = line_bands(main)
    assert len(bands) == len(gt)
    segs = segments(main, max_lines=4)
    assert len(segs) >= 2
    blank = blank_rows(ink_mask(main))
    for s in segs[1:]:
        assert blank[s.top], "a cut passes through ink"
    assert segs[0].top == 0 and segs[-1].bottom == main.height
    assert all(a.bottom == b.top for a, b in zip(segs, segs[1:]))
    assert sum(s.est_lines for s in segs) == len(gt)
    rebuilt = Image.new("L", main.size)
    for s in segs:
        rebuilt.paste(main.crop((0, s.top, main.width, s.bottom)), (0, s.top))
    assert list(rebuilt.getdata()) == list(main.getdata())


def test_crop_edge_warning(page):
    im, _gt, boxes = page
    assert not edge_contacts(crop_box(im, boxes["main"]))
    tight = dict(boxes["main"], y=boxes["main"]["y"] + 0.035)
    assert edge_contacts(crop_box(im, tight))


def test_notes_overlap_removed_from_main_only_below():
    m = main_minus_notes({"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}, {"x": 0.1, "y": 0.75, "w": 0.8, "h": 0.2})
    assert m["y"] == 0.1 and abs(m["h"] - 0.65) < 1e-9


# ---------------------------------------------------------------- strict Tesseract language

def test_no_silent_language_substitution(monkeypatch):
    with pytest.raises(LanguageUnavailable):
        strict_lang_string(["grc_missing_model"])
    monkeypatch.setattr(settings, "scholarly_tess_langs", ["grc_missing_model"])
    w = scholarly.tesseract_witness(Image.new("L", (400, 100), 255))
    assert w["status"] == "unavailable" and not w["lines"]
    assert "not installed" in w["warnings"][0]


def test_grc_model_is_installed_and_used():
    assert strict_lang_string(["grc"]) == "grc"
    if settings.scholarly_tessdata_dir:
        assert strict_lang_string(["grc"], settings.scholarly_tessdata_dir) == "grc"


# ---------------------------------------------------------------- profile selection

def test_profile_selection(monkeypatch):
    assert scholarly.profile_for({"languages": ["grc"]}) == "scholarly_greek"
    assert scholarly.profile_for({"languages": ["grc", "lat"]}) == "scholarly_greek"
    assert scholarly.profile_for({"languages": ["grc", "eng"]}) == "standard"
    assert scholarly.profile_for({"languages": ["eng"]}) == "standard"
    assert scholarly.profile_for({"languages": ["eng"], "profile": "scholarly_greek"}) == "scholarly_greek"
    assert scholarly.profile_for({"languages": ["grc"], "profile": "standard"}) == "standard"
    monkeypatch.setattr(settings, "scholarly_greek", "never")
    assert scholarly.profile_for({"languages": ["grc"], "profile": "scholarly_greek"}) == "standard"


# ---------------------------------------------------------------- independence, PNG, raw preservation

def test_dual_read_is_independent_lossless_and_keeps_both_witnesses(page, monkeypatch):
    im, gt, boxes = page
    main = crop_box(im, boxes["main"])
    fake = FakeScholarModels(gt, edits={1: gt[1].replace("ἀριθμόν", "ἀριθμὸν")})
    monkeypatch.setattr(settings, "gemini_api_key", "fake")
    monkeypatch.setattr(settings, "scholarly_segment_lines", 4)
    monkeypatch.setattr(gemini_engine, "_get_client", lambda: SimpleNamespace(models=fake))
    out = scholarly.dual_read(main, {"languages": ["grc"]}, reconcile=True)

    a, b = out["a"], out["b"]
    assert a["status"] == "ok" and b["status"] == "ok"
    assert len(b["lines"]) == len(gt)                      # every line, across several strips
    assert len(fake.calls) >= 3                            # several strips + at least one reconciliation
    # Gemini never saw any Tesseract text: no Tesseract word of 4+ letters occurs in any prompt
    t_words = {w for l in a["lines"] for w in l["text"].split() if len(w) >= 4}
    for c in fake.calls:
        prompt = "\n".join(c.texts)
        if "Reading 1" in prompt:      # reconciliation is the only step that shows readings, by design
            continue
        assert not any(w in prompt for w in t_words)
        assert all(p.inline_data.mime_type == "image/png" for p in c.images)
    # the deliberate Gemini error is caught as an accent disagreement
    cats = {it["category"] for it in out["collation"]["items"]}
    assert "accent" in cats
    # working text is Gemini's lines verbatim (primary witness), never rewritten by reconciliation
    assert out["plain_text"].splitlines() == [l["text"] for l in b["lines"]]
    assert any(it.get("proposal") for it in out["collation"]["items"])
    assert out["working_text"].replace("\n\n", "\n").splitlines() == out["plain_text"].splitlines()


def test_gemini_failure_falls_back_to_single_witness(page, monkeypatch):
    from google.genai import errors
    im, gt, boxes = page
    main = crop_box(im, boxes["main"])
    fake = FakeScholarModels(gt, responses=[errors.ClientError(403, {"error": {"code": 403, "message": "denied"}})] * 50)
    monkeypatch.setattr(settings, "gemini_api_key", "fake")
    monkeypatch.setattr(settings, "scholarly_segment_lines", 100)
    monkeypatch.setattr(gemini_engine, "_get_client", lambda: SimpleNamespace(models=fake))
    out = scholarly.dual_read(main, {"languages": ["grc"]})
    assert out["b"]["status"] == "failed" and not out["b"]["lines"]
    assert out["primary"] == "tesseract" and out["collation"] == {}
    assert scholarly._witness_status({"main": out}) == "single-witness"


def test_malformed_gemini_output_is_retried_then_rejected(page, monkeypatch):
    im, gt, boxes = page
    main = crop_box(im, boxes["main"])
    junk = ['{"printed_page": "", "lines": [', "```json\n{}\n```", json.dumps(_resp_lines(["α\nβ"]))]
    fake = FakeScholarModels(gt, responses=junk)
    monkeypatch.setattr(settings, "gemini_api_key", "fake")
    monkeypatch.setattr(settings, "scholarly_segment_lines", 100)
    monkeypatch.setattr(settings, "scholarly_invalid_retries", 2)
    monkeypatch.setattr(gemini_engine, "_get_client", lambda: SimpleNamespace(models=fake))
    w = scholarly.gemini_witness(main, {"languages": ["grc"]})
    assert w["status"] == "invalid" and not w["lines"]
    assert len(w["raw"]) == 3                           # every rejected raw response is kept for audit
    assert any("rejected" in x for x in w["warnings"])
    # the retry note explains the rejection but never contains page text from another engine
    assert "rejected" in fake.calls[1].texts[-1]


def test_working_text_line_format():
    lines = [{"text": "## ΠΕΡΙ ΤΟΥ ΠΥΘΑΓΟΡΙΚΟΥ ΒΙΟΥ", "kind": "text"}]
    lines = [
        {"text": "ΠΕΡΙ ΒΙΟΥ", "kind": "heading", "starts_paragraph": True},
        {"text": "Ἐπὶ πάσης μὲν", "kind": "text", "starts_paragraph": True},
        {"text": "φιλοσοφίας", "kind": "text", "starts_paragraph": False},
        {"text": "Μῆνιν ἄειδε θεὰ", "kind": "verse", "starts_paragraph": False},
        {"text": "οὐλομένην", "kind": "verse", "starts_paragraph": False},
    ]
    assert scholarly.lines_to_working_text(lines) == (
        "## ΠΕΡΙ ΒΙΟΥ\n\nἘπὶ πάσης μὲν\nφιλοσοφίας\n\n%verse\nΜῆνιν ἄειδε θεὰ\nοὐλομένην")
