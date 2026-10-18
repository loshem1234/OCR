"""Crop-layout resolution: page-by-page carry-forward, odd/even, all-pages mode."""
from app.layout import main_minus_notes, resolve, resolve_all

A = {"main": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}, "notes": None, "mode": "single", "split": 0.5}
B = {"main": {"x": 0.2, "y": 0.1, "w": 0.7, "h": 0.8}, "notes": None, "mode": "single", "split": 0.5}


def test_carry_forward_from_previous_page():
    pages = [(0, A), (1, None), (2, None), (3, B), (4, None)]
    eff = resolve_all({}, pages)  # carry_forward defaults on
    assert eff[0][1] == "page"
    assert eff[1][:2] == (eff[0][0], "inherited") and eff[1][2] == 1
    assert eff[2][2] == 1
    assert eff[3][1] == "page"
    assert eff[4][0]["main"]["x"] == 0.2 and eff[4][2] == 4
    # single-page resolve agrees with the bulk pass (this is what the OCR worker uses)
    lay, src = resolve({}, 4, None, pages[:4])
    assert src == "inherited" and lay["main"]["x"] == 0.2


def test_pages_before_first_crop_use_global():
    eff = resolve_all({}, [(0, None), (1, A)])
    assert eff[0][1] == "global" and eff[0][0]["main"] is None


def test_odd_even_carry_forward_keeps_sides_apart():
    pages = [(0, A), (1, B), (2, None), (3, None)]
    eff = resolve_all({"use_odd_even": True}, pages)
    assert eff[2][2] == 1 and eff[3][2] == 2  # page 3 follows page 1, page 4 follows page 2


def test_all_pages_mode_ignores_carry_forward():
    eff = resolve_all({"carry_forward": False}, [(0, A), (1, None)])
    assert eff[1][1] == "global"


def test_notes_box_cut_out_of_main():
    m = main_minus_notes(None, {"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.15})
    assert abs(m["h"] - 0.8) < 1e-9 and m["y"] == 0
