# Benchmark — synthetic polytonic pages (interim)

**Status: the benchmark against the real Deubner/Klein edition is still pending.** No verified transcription of
printed Deubner pages was available while this was built, so the numbers below come from a synthetic corpus.
They were used only to choose Tesseract settings. Do not read them as accuracy figures for the edition.

## Corpus

`tools/synth_greek.py` sets eight octavo pages in **GFS Porson**, a Teubner-style Greek typeface, at 11 pt,
using Noto Serif/DejaVu only for glyphs Porson lacks (⟨ ⟩, ‖). The text has every diacritic type, iota
subscripts, diaeresis, coronis, elision, ano teleia, ⟨ ⟩ [ ] † and Greek question marks, plus a running head
and an apparatus below a rule. Each page was "scanned" from a 600 dpi master to 300/400/600 dpi with slight
blur, sensor noise and ±0.25° skew. That gives 89 ground-truth lines and about 4,800 characters.

What it does **not** model: real type wear, broken sorts, show-through, uneven inking, Deubner's own Greek
fount, marginal numbering, or a real apparatus layout.

## Results — Tesseract (Gemini needs an API key; see "Pending")

All runs use the main-text crop and Ancient Greek only. Rates are measured against ground truth (lower is better).

| configuration | CER | diacritic ER | symbol ER | WER | extra lines |
|---|---|---|---|---|---|
| standard pipeline (pre-upgrade), `grc` fast | 0.190 | 0.224 | 1.85* | 0.494 | 21 |
| PSM 6, gray, **fast**, no strip filter | 0.230 | 0.268 | 2.10* | 0.583 | 33 |
| PSM 6, gray, **best**, no strip filter | 0.224 | 0.183 | 2.17* | 0.580 | 33 |
| PSM 3, gray, best, strip filter | 0.039 | 0.065 | 0.92 | 0.150 | 3 |
| PSM 4, gray, best, strip filter | 0.039 | 0.065 | 0.92 | 0.150 | 3 |
| PSM 6, gray, fast, strip filter | 0.041 | 0.076 | 0.85 | 0.153 | 3 |
| PSM 6, gray, best, strip filter | 0.034 | 0.065 | 0.85 | 0.139 | 2 |
| PSM 6, Otsu, best, strip filter | 0.031 | **0.059** | **0.70** | 0.115 | 2 |
| PSM 6, Sauvola, best, strip filter | 0.052 | 0.082 | 0.70 | 0.155 | 5 |
| PSM 6, gray ×1.5, best, strip filter | 0.030 | 0.089 | 0.96 | 0.145 | 0 |
| **PSM 6, mild contrast, best, strip filter (default)** | **0.020** | 0.061 | 0.73 | **0.103** | **0** |

\* A symbol error rate above 1 means more punctuation and symbol errors than there are symbols in the ground
truth. Junk lines create spurious symbols.

Resolution, with the default configuration:

| scan dpi | CER | diacritic ER |
|---|---|---|
| 300 | 0.042 | 0.061 |
| **400** | **0.020** | **0.061** |
| 600 | 0.028 | 0.081 |

### What drove the choices

* **Accent-strip filter (largest effect).** Without it, Tesseract turns the row of accents and breathings above
  some lines into a separate line of junk (`Ξ .“. 5 , 3 - ς…`). That alone puts the character error rate around
  19–23% in every configuration. The filter keeps every pseudo-line it removes in the raw record.
* **`tessdata_best` over the fast model** gives fewer diacritic errors (0.065 vs 0.076) and fewer character
  errors. It is pinned in the Dockerfile.
* **PSM 6** gave the best results. PSM 3 and 4 were tied for second, slightly behind.
* **Mild contrast at native scale.** Upscaling ×1.5 helped letters a little but hurt diacritics (0.089 vs 0.061),
  so it is off. Otsu had the best diacritic and symbol scores but slightly more letter errors. Sauvola was worst.
* **400 dpi** is best for Tesseract. 600 dpi did not help it: its Greek model expects smaller glyphs. The app
  therefore stores the scan losslessly at its native resolution as the master, gives Tesseract a copy scaled
  to about 400 dpi (`SCHOLARLY_TESS_TARGET_DPI`), and gives Gemini the full-resolution strips.

### Residual Tesseract errors (default configuration)

These are systematic, and they are what the second reading and human review are for:

* Editorial signs: ⟨ ⟩ → ( ), † → `T` or `7`. The symbol error rate is about 0.73, so Tesseract reads
  editorial signs very poorly.
* Ano teleia `·` → `"` or `:`. Commas and full stops get swapped.
* Circumflex read as acute (`ἆ` → `ἄ`), grave read as acute (`ἓν` → `ἕν`), and `ᾴ` → `ᾷ`.
* Missing smooth breathings (12 of 238) and circumflexes (13 of 194).

None of these are "auto-fixed" (for example ( → ⟨): that would be exactly the kind of silent substitution the
profile forbids.

## Pending (needs real data and/or an API key)

1. **The real benchmark.** Put 15–30 Deubner page regions in a folder as `NAME.png` + `NAME.gt.txt` (verified
   by hand, one printed line per line) + optional `NAME.json` crop, and hold some of them out. Then run:
   ```bash
   python tools/benchmark.py CORPUS --engines legacy,tesseract --psm 3,4,6 --preprocess contrast,otsu,gray
   GEMINI_API_KEY=… python tools/benchmark.py CORPUS --engines tesseract,gemini,dual,dual+reconcile
   ```
   To compare Gemini models, thinking levels and image resolutions, repeat the run with different
   `SCHOLARLY_GEMINI_MODEL`, `SCHOLARLY_THINKING_LEVEL`, `SCHOLARLY_MEDIA_RESOLUTION` and
   `SCHOLARLY_SEGMENT_LINES` settings.
2. **Kraken.** Not integrated. Run a Kraken model over the same regions, write `NAME.txt` per page, and compare
   it under identical metrics with `--external kraken=DIR`. Integrate it only if it measurably beats the improved
   Tesseract on the real edition.
3. **Re-check every Tesseract default** above on the real pages. Deubner's fount and paper differ from GFS Porson.
