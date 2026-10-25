# Transcription-path audit (Greek fidelity)

Every step that text or page images pass through, what it could do to a Greek character, and what the
scholarly Greek profile does about it. "Standard" = the original pipeline, which is unchanged for
non-Greek documents unless noted.

| # | Step (file) | Risk found | Status |
|---|---|---|---|
| 1 | Upload (`api.create_docs`) | none — bytes streamed to disk unchanged | — |
| 2 | Rasterization (`ingest.iter_pdf`) | fixed 300 DPI regardless of the scan's own resolution: a 400–600 dpi scan was **down-sampled** | Scholarly: renders at the embedded scan's native DPI (clamped 300–600), vector pages at 400; DPI recorded per page |
| 3 | Size cap (`ingest.MAX_SIDE` = 6000 px) | a 600 dpi page exceeds it and was **down-sampled** | Scholarly cap 9000 px (`SCHOLARLY_MAX_SIDE`); any capping is recorded in `image_meta.capped` |
| 4 | Storage (`ingest._save_page`) | JPEG q92 — lossy, blurs hairline accents | Scholarly: lossless PNG master; JPEG only for thumbnails and browser previews (never OCR input). Pages imported before the upgrade stay JPEG and are flagged |
| 5 | Grayscale conversion | removes colour only (red rubrics etc. become grey) — no mark is removed | kept; documented |
| 6 | Crop (`layout.main_minus_notes`) | where the footnote box overlaps the main box the overlap is cut off the main text — a carelessly drawn notes box **removes the last Greek line** | Kept (it is how footnote separation works) but now warned on the page; crop edges that touch ink are also warned ("a line or accent may be clipped") |
| 7 | Language model (`tesseract_engine.lang_string`) | silently falls back to **English** when the requested model is missing | Scholarly: `strict_lang_string` raises; the witness is recorded as *unavailable*, never read with another language. Standard path unchanged |
| 8 | Tesseract model | Debian ships `tessdata_fast` grc | Docker image adds the pinned, checksum-verified `tessdata_best` grc; benchmarked better on diacritics |
| 9 | Tesseract preprocessing (`_prep`) | upscaling + 1% autocontrast | Measured: mild contrast at native scale is best; upscaling *hurt* diacritics (see BENCHMARK_SYNTHETIC.md) |
| 10 | Tesseract page segmentation | PSM 3/6 read the row of accents above a line as a separate **junk line** (`Ξ .“. 5 , 3`), which then polluted the text | Scholarly: geometric filter sets such pseudo-lines aside (kept in the raw record). CER 19% → 2–3% on the synthetic corpus |
| 11 | Paragraph building (`_paragraphs`, `join_lines`) | line-end hyphens removed and lines joined **before review** | Scholarly: text stored one printed line per line; joining happens only at export |
| 12 | Heading guess (`## ` prefix) | formatting injected into text | Scholarly: headings only when Gemini reports a heading line; never inferred from type size |
| 13 | Low-confidence list | ignored one-letter words (ὁ, ἡ, ὦ) | fixed for all documents |
| 14 | Gemini image (`gemini_engine._encode`) | resized to 3072 px and re-encoded JPEG q90 | Scholarly: lossless PNG strips cut only through blank rows, so small type is not shrunk |
| 15 | Gemini prompt | asked to rejoin hyphenation, build paragraphs, add Markdown, "write long s as s" | Scholarly prompt: diplomatic transcription of physical lines only; explicit bans on correction, normalization, inference |
| 16 | Gemini parsing (`_parse`) | missing `body` was accepted as empty text; any JSON accepted | Scholarly: strict schema validation (numbering, one line per entry, no artifacts, line count vs. image); rejected answers retried, then the witness is marked invalid |
| 17 | Gemini single pass (`router`) | Greek-only pages got **one** reading | Scholarly: two independent readings + collation |
| 18 | Capability fallback (`_feature_state`) | one global dict mutated by worker threads — a 400 from one model changed requests for all | per-model, lock-protected |
| 19 | Normalization (`textutil.clean_text`) | NFC: canonical only, but maps U+0387 → U+00B7, U+037E → `;`, oxia → tonos | Policy documented; raw engine output stored separately; export option restores U+0387/U+037E |
| 20 | User edits (`api.patch_page`) | stored as typed (could be NFD) | NFC on save — one storage normalization everywhere |
| 21 | Re-run (`router` / worker) | overwrote the page text, including human corrections | Snapshot to `page_revisions` before any overwrite (both profiles); restore endpoint |
| 22 | Export inline parsing (`export/inline.py`) | `*text*` and `**text**` became italic/bold — a printed `*` or `***` lacuna sign **disappeared** | Off for scholarly documents; Markdown export escapes `*` and `_` |
| 23 | TXT headings (`render_text`) | `str.upper()` on level-1 headings — turns `ᾳ` into `ΑΙ` and drops accents | never upper-cases Greek |
| 24 | Footnote-marker stripping (`assemble._filter`) | removes `[^n]` when footnotes are dropped — only matches `[^…]`, never `[` `]` editorial brackets | verified; kept |
| 25 | Reference markers ⟦…⟧ | removed only when the user picks "Remove" | unchanged |
| 26 | Page-boundary join (`assemble`) | joins words across pages; moves the page marker after the joined word | verified by test; unchanged |
| 27 | HTML/PDF/DOCX/EPUB | HTML-escaped correctly; fonts cover polytonic Greek | verified |
| 28 | Purge images | removes masters; OCR text and witnesses kept | unchanged; previews also purged |

Not found: any NFKC/NFKD use, accent stripping, transliteration, monotonic conversion, or truncation of
page text (truncation exists only on warnings/metadata fields, never on text).
