"""Synthetic 'printed edition' pages of polytonic Greek with exact ground truth.

Used by the tests and as a stand-in benchmark corpus. It is NOT a substitute for real pages of the
Deubner/Klein edition: real scans have their own type wear, inking and paper. See tools/benchmark.py.

    python tools/synth_greek.py OUT_DIR [--pages 6] [--dpi 400] [--font porson]
"""
from __future__ import annotations

import argparse
import json
import random
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = {
    "porson": ["/usr/share/fonts/opentype/porson/GFSPorson.otf", "/usr/share/fonts/truetype/gfs/GFSPorson.otf"],
    "didot": ["/usr/share/fonts/opentype/didot/GFSDidot.otf", "/usr/share/fonts/truetype/gfs/GFSDidot.otf"],
    "noto": ["/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf"],
    "dejavu": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}
# Draw-time glyph choice only (ground truth stays NFC): the raised Greek ano teleia.
DRAW_MAP = {"\u00b7": "\u0387"}

# Varied polytonic Greek with every diacritic type, editorial signs and Greek punctuation.
PASSAGES = [
    "Ἐπὶ πάσης μὲν φιλοσοφίας ὁρμῇ θεὸν δήπου παρακαλεῖν ἔθος ἅπασι τοῖς γε σώφροσιν, ἐπὶ δὲ τῇ τοῦ θείου "
    "Πυθαγόρου ἐπωνύμῳ δικαίως νομιζομένῃ πολὺ δήπου μᾶλλον ἁρμόζει τοῦτο ποιεῖν· οὐ γὰρ ῥᾴδιον ἄνευ θεοῦ "
    "τὴν τοιαύτην ἐπιχείρησιν ἐκ τῶν ἀρχῶν διεξελθεῖν.",
    "Πᾶν πλῆθος μετέχει πῃ τοῦ ἑνός. εἰ γὰρ μηδαμῇ μετέχοι, οὔτε τὸ ὅλον ἓν ἔσται οὔτε ἕκαστον τῶν πολλῶν "
    "ἐξ ὧν τὸ πλῆθος, ἀλλ᾽ ἔσται καὶ ἐκείνων ἕκαστον πλῆθος, καὶ τοῦτο εἰς ἄπειρον.",
    "ὁ δὲ Πυθαγόρας ⟨ἔφη⟩ τὸν κόσμον [καὶ τὸν οὐρανὸν] ἁρμονίαν εἶναι καὶ ἀριθμόν· †ἀκριβῶς† δ᾽ οὖν κἀγὼ "
    "λέγω· τί ἐστιν ἀρετή; ἆρά γε ἡ τῆς ψυχῆς ὑγίεια καὶ ἡ ἀΐδιος τάξις ἡ ἐν τῷ ὅλῳ;",
    "Κατέβην χθὲς εἰς Πειραιᾶ μετὰ Γλαύκωνος τοῦ Ἀρίστωνος προσευξόμενός τε τῇ θεῷ καὶ ἅμα τὴν ἑορτὴν "
    "βουλόμενος θεάσασθαι τίνα τρόπον ποιήσουσιν ἅτε νῦν πρῶτον ἄγοντες. ὦ Σώκρατες, ἦ δ᾽ ὅς, ἆρ᾽ οὐχ ὁρᾷς;",
    "Μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος οὐλομένην, ἣ μυρί᾽ Ἀχαιοῖς ἄλγε᾽ ἔθηκε, πολλὰς δ᾽ ἰφθίμους ψυχὰς "
    "Ἄϊδι προΐαψεν ἡρώων, αὐτοὺς δὲ ἑλώρια τεῦχε κύνεσσιν οἰωνοῖσί τε πᾶσι.",
    "ἐν ᾧ δὲ ᾠδαῖς τε καὶ ὕμνοις ἐχρῆτο, ηὔξησε τὴν παιδείαν· ὃ δὴ καὶ Ἐμπεδοκλῆς ὁ Ἀκραγαντῖνος "
    "ἐμαρτύρησεν ἐν τοῖς ἔπεσιν ⟨οὕτως⟩ λέγων περὶ αὐτοῦ, ὡς ἄρα ἦν τις ἐν κείνοισιν ἀνὴρ περιώσια εἰδώς.",
    "ἔτι δὲ Ἀρχύτας ὁ Ταραντῖνος καὶ Φιλόλαος καὶ Εὔρυτος ἀκροαταὶ γενόμενοι τῶν Πυθαγορείων λόγων "
    "διεφύλαξαν τὰ δόγματα· ὁμοίως δὲ καὶ Τίμαιος ὁ Λοκρὸς [ὃν Πλάτων μέμνηται] τὰ αὐτὰ ἐφρόνει.",
]
APPARATUS = [
    "1 ὁρμῇ] ὁρμῆς F ‖ 3 ἁρμόζει] ἁρμόττει Kiessling ‖ 4 ἐπιχείρησιν Deubner: ἐπιχείρημα F",
    "7 ⟨ἔφη⟩ add. Nauck ‖ 8 καὶ τὸν οὐρανὸν del. Rohde ‖ ἀκριβῶς F: ἀκριβέστερον Cobet",
    "12 ἦ δ᾽ ὅς] ἔφη Aldina ‖ 14 Πηληϊάδεω] Πηλείδεω V ‖ 16 προΐαψεν] προέηκεν M",
]


class MixedFont:
    """A primary font with per-character fallback, so every ground-truth character is really drawn."""

    def __init__(self, name: str, size: int):
        from fontTools.ttLib import TTFont
        self.size = size
        self.fonts = []
        for n in (name, "noto", "dejavu"):
            for p in FONTS.get(n, []):
                if Path(p).exists():
                    cmap = set(TTFont(p, lazy=True).getBestCmap().keys())
                    self.fonts.append((ImageFont.truetype(p, size), cmap))
                    break
        if not self.fonts:
            raise SystemExit("No Greek-capable font found (install fonts-gfs-porson or fonts-noto-core)")

    def runs(self, text: str):
        cur_font, buf = None, ""
        for ch in text:
            if DRAW_MAP.get(ch) and ord(DRAW_MAP[ch]) in self.fonts[0][1]:
                ch = DRAW_MAP[ch]
            f = next((f for f, cmap in self.fonts if ord(ch) in cmap or ch == " "), self.fonts[-1][0])
            if f is not cur_font and buf:
                yield cur_font, buf
                buf = ""
            cur_font = f
            buf += ch
        if buf:
            yield cur_font, buf

    def getlength(self, text: str) -> float:
        return sum(f.getlength(t) for f, t in self.runs(text))

    def draw(self, d, xy, text: str, fill):
        x, y = xy
        for f, t in self.runs(text):
            d.text((x, y), t, font=f, fill=fill)
            x += f.getlength(t)


def _font(name: str, size: int) -> MixedFont:
    return MixedFont(name, size)


def wrap(text: str, font, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_page(passages: list[str], *, dpi: int = 600, font: str = "porson", apparatus: str | None = None,
                page_no: str = "17", head: str = "ΙΑΜΒΛΙΧΟΥ", seed: int = 0) -> tuple[Image.Image, list[str], dict]:
    """Render at `dpi`. Returns (image, ground-truth main-text lines, layout boxes normalized 0..1)."""
    W, H = int(5.5 * dpi), int(8.5 * dpi)               # a small octavo page
    size = int(dpi * 11 / 72)                            # 11 pt text
    f_main, f_small = _font(font, size), _font(font, int(size * 0.78))
    im = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(im)
    left, right = int(0.75 * dpi), W - int(0.75 * dpi)
    f_small.draw(d, (W // 2 - f_small.getlength(head) // 2, int(0.45 * dpi)), head, fill=30)
    f_small.draw(d, (right - f_small.getlength(page_no), int(0.45 * dpi)), page_no, fill=30)
    y = int(0.85 * dpi)
    top = y
    lead = int(size * 1.42)
    gt: list[str] = []
    for p in passages:
        for i, line in enumerate(wrap(unicodedata.normalize("NFC", p), f_main, right - left)):
            x = left + (int(size * 1.2) if i == 0 else 0)  # paragraph indent
            if i == 0 and f_main.getlength(line) > right - x:
                x = left
            f_main.draw(d, (x, y), line, fill=15)
            gt.append(line)
            y += lead
            if y > H - int(2.2 * dpi):
                break
        if y > H - int(2.2 * dpi):
            break
    bottom = y
    boxes = {"main": {"x": (left - 0.12 * dpi) / W, "y": (top - 0.18 * dpi) / H,
                      "w": (right - left + 0.24 * dpi) / W, "h": (bottom - top + 0.12 * dpi) / H}}
    if apparatus:
        ry = y + int(0.12 * dpi)
        d.line((left, ry, left + int(1.5 * dpi), ry), fill=40, width=max(1, dpi // 200))
        ay = ry + int(0.08 * dpi)
        for line in wrap(apparatus, f_small, right - left):
            f_small.draw(d, (left, ay), line, fill=20)
            ay += int(f_small.size * 1.35)
        boxes["notes"] = {"x": boxes["main"]["x"], "y": (ry + 0.04 * dpi) / H, "w": boxes["main"]["w"],
                          "h": (ay - ry) / H}
    return im, gt, boxes


def scan(im: Image.Image, *, from_dpi: int, to_dpi: int, blur: float = 0.6, noise: float = 6.0,
         rotate: float = 0.0, seed: int = 0) -> Image.Image:
    """Simulate a scan: resample, slight blur, sensor noise, tiny skew."""
    import numpy as np
    rnd = np.random.default_rng(seed)
    f = to_dpi / from_dpi
    out = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
    if rotate:
        out = out.rotate(rotate, resample=Image.BICUBIC, fillcolor=250)
    if blur:
        out = out.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        a = np.asarray(out, dtype=np.float32) + rnd.normal(0, noise, (out.height, out.width))
        out = Image.fromarray(np.clip(a, 0, 255).astype("uint8"))
    return out


def build_corpus(out: Path, pages: int = 6, dpi: int = 400, font: str = "porson", seed: int = 1) -> list[dict]:
    """Write NAME.png, NAME.gt.txt, NAME.json (crop boxes) for a synthetic corpus."""
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    items = []
    for i in range(pages):
        ps = PASSAGES[i % len(PASSAGES):] + PASSAGES[: i % len(PASSAGES)]
        master, gt, boxes = render_page(ps[:3], dpi=600, font=font, apparatus=APPARATUS[i % len(APPARATUS)],
                                        page_no=str(17 + i), seed=i)
        page = scan(master, from_dpi=600, to_dpi=dpi, rotate=rnd.uniform(-0.25, 0.25), seed=seed + i)
        name = f"synth_{font}_{i + 1:02d}"
        page.save(out / f"{name}.png")
        (out / f"{name}.gt.txt").write_text("\n".join(gt) + "\n", encoding="utf-8")
        (out / f"{name}.json").write_text(json.dumps({"layout": {"main": boxes["main"], "notes": boxes.get("notes"),
                                                                 "mode": "single", "split": 0.5},
                                                      "source": "synthetic", "dpi": dpi, "font": font}), encoding="utf-8")
        items.append({"name": name, "lines": len(gt)})
    return items


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--dpi", type=int, default=400)
    ap.add_argument("--font", default="porson")
    a = ap.parse_args()
    print(build_corpus(Path(a.out), a.pages, a.dpi, a.font))
