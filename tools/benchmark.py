"""Reproducible OCR accuracy benchmark against verified transcriptions.

Corpus layout (one triple per page region):
    NAME.png | NAME.jpg | NAME.tif   the page image (scan as received)
    NAME.gt.txt                      VERIFIED transcription of the main text, one printed line per line,
                                     NFC, never produced by the engine under test
    NAME.json   (optional)           {"layout": {"main": {x,y,w,h}, "notes": ..., "mode": "single"}}
                                     crop in 0..1 page coordinates; whole page if absent
    NAME.pdf + NAME.page (optional)  for DPI experiments: render that PDF page instead of NAME.png

Examples
    python tools/benchmark.py CORPUS --engines tesseract --psm 3,4,6 --preprocess gray,legacy,sauvola
    python tools/benchmark.py CORPUS --engines tesseract --tessdata fast=/usr/share/tesseract-ocr/5/tessdata,best=/opt/tessdata_best
    python tools/benchmark.py CORPUS --engines legacy,tesseract,gemini,dual,dual+reconcile
    python tools/benchmark.py CORPUS --engines tesseract --dpi 300,400,600      (PDF sources only)
    python tools/benchmark.py CORPUS --external kraken=/path/to/kraken_txt_dir   (NAME.txt per page)

Writes OUT/results.json (every page × config) and OUT/report.md (summary table).
Metrics: see app/ocr/metrics.py. Nothing here is tuned on the corpus; hold some pages out if you tune.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app.layout import crop_box, main_minus_notes  # noqa: E402
from app.ocr.metrics import Score, score  # noqa: E402

IMG_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")


def load_corpus(d: Path) -> list[dict]:
    items = []
    for gt in sorted(d.glob("*.gt.txt")):
        name = gt.name[: -len(".gt.txt")]
        img = next((d / f"{name}{e}" for e in IMG_EXT if (d / f"{name}{e}").exists()), None)
        pdf = d / f"{name}.pdf"
        meta = json.loads((d / f"{name}.json").read_text("utf-8")) if (d / f"{name}.json").exists() else {}
        if not img and not pdf.exists():
            print(f"skip {name}: no image", file=sys.stderr)
            continue
        items.append({"name": name, "gt": gt.read_text("utf-8"), "image": img, "pdf": pdf if pdf.exists() else None,
                      "pdf_page": int((d / f"{name}.page").read_text().strip()) if (d / f"{name}.page").exists() else 0,
                      "layout": meta.get("layout") or {"main": None, "notes": None, "mode": "single", "split": 0.5}})
    return items


def page_image(item: dict, dpi: int | None) -> Image.Image:
    if dpi and item["pdf"]:
        import pymupdf
        with pymupdf.open(item["pdf"]) as d:
            pg = d[item["pdf_page"]]
            pix = pg.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            return Image.frombytes("L", (pix.width, pix.height), pix.samples)
    return Image.open(item["image"]).convert("L")


def main_region(im: Image.Image, layout: dict) -> Image.Image:
    return crop_box(im, main_minus_notes(layout.get("main"), layout.get("notes")))


# ------------------------------------------------------------------ runners: (image region) -> text

def run_tesseract(region, cfg):
    from app.ocr.tesseract_engine import recognize_lines
    r = recognize_lines(region, cfg["langs"], psm=cfg["psm"], tessdata_dir=cfg["tessdata"],
                        preprocess_mode=cfg["preprocess"], scale=cfg["scale"], drop_mark_strips=cfg["strip_filter"])
    return "\n".join(l["text"] for l in r["lines"]), {}


def run_legacy(region, cfg):
    """The standard (pre-upgrade) Tesseract path, for a baseline."""
    from app.ocr.tesseract_engine import ocr_region
    r = ocr_region(region, cfg["langs"])
    return r.body.replace("## ", ""), {}


def run_gemini(region, cfg):
    from app.ocr import scholarly
    r = scholarly.gemini_witness(region, cfg["doc_settings"], region_name="main")
    return "\n".join(l["text"] for l in r.get("lines") or []), {"tokens": r.get("tokens_in", 0) + r.get("tokens_out", 0),
                                                               "status": r.get("status")}


def run_dual(region, cfg, reconcile=False):
    from app.ocr import scholarly
    out = scholarly.dual_read(region, cfg["doc_settings"], reconcile=reconcile)
    return out["working_text"], {"tokens": out["tokens"], "agreement": out["collation"].get("agreement"),
                                 "disagreements": out["collation"].get("substantive")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus")
    ap.add_argument("--out", default="")
    ap.add_argument("--engines", default="tesseract")
    ap.add_argument("--langs", default="grc")
    ap.add_argument("--psm", default="6")
    ap.add_argument("--preprocess", default="gray")
    ap.add_argument("--scale", default="1.0")
    ap.add_argument("--strip-filter", default="on", help="on,off — drop accent-strip pseudo-lines")
    ap.add_argument("--tessdata", default="", help="name=dir[,name=dir] (default: system tessdata)")
    ap.add_argument("--dpi", default="", help="render PDF sources at these DPIs (comma list)")
    ap.add_argument("--external", action="append", default=[], help="name=dir with NAME.txt transcriptions")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    corpus = load_corpus(Path(a.corpus))[: a.limit or None]
    if not corpus:
        sys.exit("no pages with .gt.txt found")
    out = Path(a.out or (Path(a.corpus) / "benchmark_out"))
    out.mkdir(parents=True, exist_ok=True)
    tessdata = [tuple(x.split("=", 1)) for x in a.tessdata.split(",") if x] or [("system", "")]
    dpis = [int(x) for x in a.dpi.split(",") if x] or [None]
    langs = [l for l in a.langs.split(",") if l]

    configs = []
    for eng in [e for e in a.engines.split(",") if e]:
        if eng == "tesseract":
            for psm, pre, sc, (tn, td), sf, dpi in itertools.product(
                    [int(x) for x in a.psm.split(",")], a.preprocess.split(","), [float(x) for x in a.scale.split(",")],
                    tessdata, a.strip_filter.split(","), dpis):
                configs.append({"engine": "tesseract", "psm": psm, "preprocess": pre, "scale": sc, "tessdata_name": tn,
                                "tessdata": td, "strip_filter": sf == "on", "dpi": dpi, "langs": langs})
        elif eng in ("legacy", "gemini", "dual", "dual+reconcile"):
            for dpi in dpis:
                configs.append({"engine": eng, "dpi": dpi, "langs": langs,
                                "doc_settings": {"languages": langs, "engine": "auto", "footnote_mode": "drop",
                                                 "preserve_refs": False, "hint": ""}})
    for ext in a.external:
        name, d = ext.split("=", 1)
        configs.append({"engine": f"external:{name}", "dir": d})

    results = []
    for cfg in configs:
        label = cfg["engine"] + "".join(f" {k}={cfg[k]}" for k in ("psm", "preprocess", "scale", "tessdata_name",
                                                                   "strip_filter", "dpi") if k in cfg and cfg[k] is not None)
        total, pages, lat, fails, tokens = Score(), [], 0.0, 0, 0
        for item in corpus:
            t0 = time.time()
            meta = {}
            try:
                if cfg["engine"].startswith("external:"):
                    f = Path(cfg["dir"]) / f"{item['name']}.txt"
                    text = f.read_text("utf-8") if f.exists() else ""
                    if not f.exists():
                        fails += 1
                else:
                    im = page_image(item, cfg.get("dpi"))
                    region = main_region(im, item["layout"])
                    runner = {"tesseract": run_tesseract, "legacy": run_legacy, "gemini": run_gemini,
                              "dual": run_dual, "dual+reconcile": lambda r, c: run_dual(r, c, True)}[cfg["engine"]]
                    text, meta = runner(region, cfg)
            except Exception as e:  # a failing page counts as an empty transcription
                text, meta = "", {"error": str(e)[:200]}
                fails += 1
            dt = time.time() - t0
            lat += dt
            tokens += int(meta.get("tokens", 0) or 0)
            s = score(item["gt"], text)
            total.add(s)
            pages.append({"page": item["name"], "seconds": round(dt, 2), **meta, **s.summary(), "hypothesis": text})
        summ = total.summary()
        results.append({"config": label, "settings": {k: v for k, v in cfg.items() if k != "doc_settings"},
                        "summary": summ, "seconds_per_page": round(lat / len(corpus), 2), "failures": fails,
                        "tokens": tokens, "pages": pages})
        print(f"{label:70s} CER {summ['CER']}  diacER {summ['diacritic_ER']}  WER {summ['WER']}  "
              f"missing {summ['missing_lines']} extra {summ['extra_lines']}  {lat / len(corpus):.1f}s/pg", flush=True)

    (out / "results.json").write_text(json.dumps({"corpus": a.corpus, "pages": len(corpus), "results": results},
                                                 ensure_ascii=False, indent=1), "utf-8")
    rows = sorted(results, key=lambda r: (r["summary"]["CER"] if r["summary"]["CER"] is not None else 9))
    md = [f"# OCR benchmark — {len(corpus)} page regions from `{a.corpus}`", "",
          "| config | CER | grapheme ER | diacritic ER | accent subs | breathing subs | symbol ER | WER | missing lines | extra lines | s/page | failures |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["summary"]
        md.append(f"| {r['config']} | {s['CER']} | {s['grapheme_ER']} | {s['diacritic_ER']} | {s['accent_substitutions']} | "
                  f"{s['breathing_substitutions']} | {s['symbol_ER']} | {s['WER']} | {s['missing_lines']} | {s['extra_lines']} | "
                  f"{r['seconds_per_page']} | {r['failures']} |")
    md += ["", "Lower is better for every rate. Rates are on NFC text with whitespace collapsed; line counts come",
           "from an order-preserving line alignment (≥60% similarity). See app/ocr/metrics.py."]
    (out / "report.md").write_text("\n".join(md) + "\n", "utf-8")
    print(f"\nwrote {out / 'report.md'}")


if __name__ == "__main__":
    main()
