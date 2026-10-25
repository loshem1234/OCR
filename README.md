# Scriptorium OCR

A private OCR workbench for scanned books and editions. You upload a scan, draw what to read, let a hybrid
Tesseract + Gemini pipeline read it, correct it page by page beside the image, and export clean text as
**TXT, Markdown, a re-typeset PDF, DOCX or EPUB**.

It is built for scholarly material. It handles polytonic Greek, Latin, Hebrew, Arabic and Syriac. It can keep
footnotes and apparatus as separate notes, preserve Stephanus/Bekker/line references, and keep two-column and
facing-text pages. Original page numbers are carried through as citation markers like `[p. 42]`.

---

## The workflow

| Tab | What you do there |
| --- | --- |
| **I. Layout & crop** | Draw the **main text box** (gold) and an optional **footnote box** (blue). In **Page by page** mode (the default), every page you adjust keeps its own crop, and pages you haven't touched start from the most recent crop before them. So you crop page 1, press `→`, nudge page 2 if needed, and so on. Thumbnails outline each page's effective crop. *Carry odd & even pages separately* makes left and right pages inherit from their own side. **Same for all pages** mode gives you one all-pages layout (optionally odd/even) with per-page exceptions. Mark pages as *single*, *2 columns* (read left then right) or *parallel* (facing texts kept side by side), and drag the dashed divider to the gutter. You can skip blank pages and plates, or apply a crop to a page range. |
| **II. Process** | **Pages to read:** *Keep only this range* (e.g. 75–313) skips every page outside it, and *Include all pages* undoes that. Skipped pages are never read or exported, and the arrow keys jump over them. Set languages, engine, footnote handling, reference preservation and a free-text note for Gemini. Start OCR and watch progress per page (engine used, why it was routed, confidence, errors). You can re-run failed pages, flagged pages, or everything, optionally forcing an engine. |
| **III. Review** | The page image sits beside an editor. Words the engine was unsure of are **highlighted**. Edits save automatically. You can re-read a single page with Gemini or Tesseract. Keyboard: `Alt+←/→` pages, `Ctrl+S` save, `Ctrl+Enter` mark reviewed and go to the next page. |
| **IV. Export** | Choose the page-marker format and starting page number, whether to continue paragraphs across page breaks, endnotes or per-page notes, whether to keep or remove reference markers, and the page size, font size and title page. A live preview shows the result. Downloads: PDF, Word, EPUB, Markdown, plain text. |

**Library.** The home page lists every document with its status, progress and disk usage. A storage meter shows
how full the volume is and warns at 85%. To free space, select documents and either:
- **Purge images**: deletes the page images and the original upload but keeps all OCR text. You can still edit and export, but not re-crop or re-run OCR.
- **Delete**: removes everything.

### Text conventions in the editor

```
## Heading                     # Book / part title (rare)
Blank line = new paragraph     single line break = verse line
*italic*   **bold**
[^1]       footnote reference (kept when footnotes are kept, stripped when dropped)
⟦17a⟧      reference number (Stephanus, Bekker, line, verse), when "Preserve reference numbers" is on
```

### How the hybrid engine decides (engine = *Hybrid*)

1. If *Use the PDF's text layer* is on and the page has one, the app reads the text inside your crop boxes. This is free and exact.
2. If *Preserve reference numbers* is on, or every selected language is non-Latin (Greek, Hebrew, Arabic, Syriac), the page goes straight to **Gemini**.
3. Otherwise **Tesseract** reads it first. The page is re-read by **Gemini** if Tesseract finds any Greek, Hebrew, Arabic or Syriac, or if its confidence is below `HYBRID_CONF_THRESHOLD` (default 80%).
4. If Gemini fails, the Tesseract result is kept and the page is flagged with a warning. Gemini can fail on quota, or decline to reproduce well-known copyrighted text.

When Gemini reads a page it also reports the **printed page number**, used for `[p. N]` markers, and a list of
**uncertain words**, which are highlighted in Review.

---

## Scholarly Greek profile

> The printed image is the authority. OCR engines are witnesses. AI corrections are proposals.
> Only human-reviewed readings belong in the master text.

The scholarly Greek profile is for printed critical editions in polytonic Greek, such as Deubner/Klein's
Iamblichus, *De vita Pythagorica*. It works behind the same four tabs. Nothing about cropping, page ranges or
queueing changes.

**When it applies.** Automatically, when a document's languages are Ancient Greek, optionally with Latin or
modern Greek. A document is pinned either way with *Processing profile* in the Process tab. The environment
variable `SCHOLARLY_GREEK=never` switches it off everywhere, and `always` forces it on. English-only, Hebrew,
Arabic and mixed-language documents keep the standard pipeline.

### How a page is read

1. **Lossless master.** On import, each page is stored as PNG at the scan's own resolution, between 300 and
   600 dpi. Vector or text-only PDF pages are rendered at 400 dpi. The DPI and format are recorded per page.
   Thumbnails and the browser preview are JPEG, but OCR never reads them. Pages imported *before* the upgrade
   stay JPEG and are flagged. Re-import such a document to get lossless masters.
2. **Your crop** comes from the existing layout system, unchanged. The app warns when a crop edge touches print
   (a line or accent may be clipped) and when the footnote box cuts into the main box.
3. **Two independent readings of the same pixels**, neither of which ever sees the other's output:
   * **A — Tesseract**, Ancient Greek only. It uses the pinned `tessdata_best` model, segmentation mode 6, mild
     contrast, scaled to about 400 dpi. If the Greek model is missing, the reading is recorded as *unavailable*.
     It is never read with English instead.
   * **B — Gemini**, as a strict diplomatic transcriber. It gets lossless PNG strips cut only through fully blank
     rows, so a cut can never pass through a letter or accent. It returns physical printed lines: no joining,
     no Markdown, no hyphen rejoining, no "corrections". Every answer is schema-validated: numbering, one line
     per entry, no JSON fragments or placeholders, and a line count checked against the lines detected in the
     image. A bad answer is retried, then rejected. It is never silently accepted.
4. **Collation** (`app/ocr/collation.py`) compares the two at the word and letter level after NFC
   normalization, so precomposed and decomposed spellings are *not* differences. Each disagreement is
   classified and tied to a line of each witness: accent, breathing, iota subscript, diaeresis, letters,
   punctuation, editorial sign, omission, addition, duplication, whole-line omission, reordering, script mix
   (e.g. a Latin *o* inside a Greek word), and line-end word division.
5. **Proposals.** Optionally (`SCHOLARLY_RECONCILE=on`), for up to `SCHOLARLY_RECONCILE_MAX` disagreements per
   page, Gemini is shown *only the image of that printed line* and both readings, in a randomized order. It is
   asked what the ink shows; grammar is explicitly irrelevant. The result is stored as a **proposal**. It never
   changes the text.
6. **Working text** = the primary witness's lines (Gemini by default; `SCHOLARLY_PRIMARY=tesseract` to change),
   one printed line per line. Words involved in disagreements are highlighted in Review, which lists each
   disagreement with **Use Tesseract / Use Gemini / Use proposal** buttons.
7. **Status, never overstated.** A page is one of:
   * *2 engines agree* — **not** verified: both engines can misread the same mark;
   * *N disagreements*;
   * *1 reading only* — for example Gemini unavailable or over budget; shown as not cross-checked;
   * *✓ reviewed* — only you can set this, by marking the page reviewed.

### What is stored (nothing is overwritten)

| layer | where |
|---|---|
| A. raw Tesseract output (full TSV), lines with word boxes and confidences, config, model, version | `recognitions` (engine `tesseract`) |
| B. raw Gemini responses (every attempt, including rejected ones), validated lines, model, settings, tokens, latency | `recognitions` (engine `gemini`) |
| collation, with every item's category, both readings, line positions and proposal | `pages.collation_data` |
| proposals (raw responses) | `recognitions` (engine `reconcile`) |
| your reviewed text | `pages.body` / `right` / `notes` (line format) |
| previous versions — saved automatically before any re-run or restore | `page_revisions` (Review → **History** → Restore) |
| provenance: crop, image DPI/format, engines, settings, run id | `pages.provenance` |

**Export → Collation (JSON)** downloads all of this for the whole document as an audit file (`?raw=1` adds the
raw responses).

### Unicode policy

Text is stored and compared in **NFC**. NFC is canonical normalization: it never removes a mark or changes a
letter. It does unify spellings Unicode defines as identical:

* oxia letters become tonos letters;
* ano teleia U+0387 becomes U+00B7;
* the Greek question mark U+037E becomes `;`;
* combining koronis U+0343 becomes U+0313.

No NFKC/NFKD, accent stripping, monotonic conversion or transliteration is used anywhere. Export → *Greek
punctuation code points* writes U+0387 and U+037E after Greek letters, for publishers who need them. Bear in
mind that any later NFC normalization will turn them back.

### Exports

Line-format pages are joined into prose **only at export**: *Join printed lines into paragraphs*, with
hyphenated line ends rejoined. Turn joining off for a diplomatic, line-by-line export. `%verse` blocks keep
their line breaks. In this profile `*` and `_` are literal text, never formatting. Apparatus is excluded when
footnotes are set to "Drop". TXT output is plain UTF-8 NFC with no OCR metadata; untick page markers if you
don't want them.

### Configuration

| variable | default | meaning |
|---|---|---|
| `SCHOLARLY_GREEK` | `auto` | `auto` (Ancient Greek documents), `always`, `never` |
| `SCHOLARLY_TESSDATA_DIR` | `/usr/share/tesseract-ocr/5/tessdata_best` if present | where the Greek model lives |
| `SCHOLARLY_TESS_LANGS` | `grc` | `+`-separated; all must be installed, or the reading is marked unavailable |
| `SCHOLARLY_TESS_PSM` | `6` | Tesseract page segmentation mode |
| `SCHOLARLY_TESS_PREPROCESS` | `contrast` | `gray`, `contrast`, `otsu`, `sauvola`, `legacy` |
| `SCHOLARLY_TESS_TARGET_DPI` | `400` | Tesseract's working resolution (the master is not changed) |
| `SCHOLARLY_RENDER_DPI` | `400` | rendering resolution for vector PDF pages |
| `SCHOLARLY_MIN_DPI` / `SCHOLARLY_MAX_DPI` | `300` / `600` | clamp for native scan resolution |
| `SCHOLARLY_MAX_SIDE` | `9000` | pixel cap for masters (recorded if hit) |
| `SCHOLARLY_GEMINI_MODEL` | `GEMINI_MODEL` | model for transcription and proposals |
| `SCHOLARLY_SEGMENT_LINES` | `10` | printed lines per Gemini strip |
| `SCHOLARLY_MEDIA_RESOLUTION` | `MEDIA_RESOLUTION_HIGH` | per-image resolution (`…_ULTRA_HIGH` is available; benchmark it) |
| `SCHOLARLY_THINKING_LEVEL` | `GEMINI_THINKING_LEVEL` | Gemini thinking level for transcription |
| `SCHOLARLY_PRIMARY` | `gemini` | which witness becomes the working text |
| `SCHOLARLY_RECONCILE` / `SCHOLARLY_RECONCILE_MAX` | `on` / `12` | proposals per page |
| `SCHOLARLY_INVALID_RETRIES` | `2` | retries for a rejected Gemini answer |
| `GEMINI_DOC_TOKEN_BUDGET` | `30000000` | per-document token cap; past it, pages get a Tesseract reading only, with a warning |
| `GEMINI_SEED` | `0` | best-effort reproducibility (empty string to omit) |

**Recommended production settings for Deubner** — the defaults, plus:

```
SCHOLARLY_GREEK=auto
GEMINI_MODEL=gemini-3.6-flash          # or a Pro model, once the real benchmark shows it reads better
SCHOLARLY_RECONCILE=on
OCR_CONCURRENCY=3
GEMINI_DOC_TOKEN_BUDGET=30000000
```

Rough cost: each page is 3–4 strip calls (≈1.7 k tokens in, ≈0.9 k out each, including the page image), plus
up to 12 short proposal calls. That is about 20–27 k tokens per page, or 10–14 M tokens for a 500-page book:
roughly $15–25 at late-2026 Flash prices, and about double after Google's January 2027 price change. These
are estimates. The real figure for every page is recorded, and the Process tab shows the running total.

### Benchmarks

`tools/benchmark.py` measures, against **verified** transcriptions:

* character error rate;
* grapheme-cluster error rate;
* per-diacritic errors (acute, grave, circumflex, both breathings, iota subscript, diaeresis);
* accent and breathing substitutions;
* editorial-symbol errors;
* word error rate;
* missing, extra and duplicated lines;
* time and tokens per page.

It covers the standard pipeline, Tesseract variants, Gemini, dual reading, and dual reading with proposals,
plus any external engine via `--external`. Interim results on synthetic pages, and the commands for the real
Deubner benchmark (still pending), are in [docs/BENCHMARK_SYNTHETIC.md](docs/BENCHMARK_SYNTHETIC.md). The
transcription-path audit is in [docs/AUDIT.md](docs/AUDIT.md).

### Migration and rollback

* **Upgrade.** Deploy as usual. On start-up the app creates two new tables (`recognitions`, `page_revisions`)
  and *adds* six nullable/defaulted columns to `pages` (`text_format`, `profile`, `witness_status`,
  `collation_data`, `provenance`, `image_meta`). Nothing is dropped or renamed. Existing pages read as
  `text_format = 'prose'` and export exactly as before. Tested against a Postgres database created by the
  previous release.
* **Rollback.** Redeploy the previous commit. It ignores the extra tables and columns. Pages read with the
  scholarly profile keep their line-format text; the old exporter prints it with the printed line breaks kept
  instead of joining them into prose. Re-export after upgrading again. To remove the additions entirely (optional):
  `DROP TABLE recognitions, page_revisions; ALTER TABLE pages DROP COLUMN text_format, DROP COLUMN profile, DROP COLUMN witness_status, DROP COLUMN collation_data, DROP COLUMN provenance, DROP COLUMN image_meta;`

### Limits (what still needs your eyes)

* **No engine output is verified until you review it against the image.** Engines agreeing proves nothing about
  shared misreadings, such as a faint breathing both engines miss.
* Tesseract is weak on editorial signs (⟨ ⟩, †) and the ano teleia, so expect those disagreements on every
  page. Gemini's accuracy on Deubner is **unmeasured**: the real benchmark is pending.
* Gemini's per-line positions are approximate (taken from detected text bands), so proposals for lines Tesseract
  lacks use a wider crop.
* Facing text is supported within a page, not across Loeb-style facing pages.
* Kraken is not integrated; it can be compared through the benchmark's `--external` option.

---

## Deploy on Railway

1. **Create a GitHub repo** and push this folder:
   ```bash
   git init && git add . && git commit -m "Scriptorium OCR"
   git branch -M main
   git remote add origin git@github.com:<you>/scriptorium-ocr.git
   git push -u origin main
   ```
2. In Railway: **New Project → Deploy from GitHub repo** and pick the repo. Railway detects the `Dockerfile` (`railway.json` tells it to use it).
3. **Add a database:** in the project, **+ New → Database → PostgreSQL**.
4. **Add a volume** to the web service: *Settings → Volumes → New Volume*, mount path **`/data`**. Page images, originals and the export scratch space live here. 5–10 GB is comfortable for a few dozen 500-page books, at roughly 0.3–0.6 MB per page.
5. **Variables** on the web service:
   | Variable | Value |
   | --- | --- |
   | `ADMIN_PASSWORD` | your login password |
   | `SECRET_KEY` | a long random string (`python -c "import secrets;print(secrets.token_hex(32))"`) |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (Railway reference variable) |
   | `GEMINI_API_KEY` | your Google AI Studio key |
   | `STORAGE_LIMIT_GB` | the size of the volume you created, e.g. `10`, so the meter is accurate |
6. *Settings → Networking → Generate Domain*, open it, and sign in.

Keep **one replica**. The OCR queue runs inside the web process and resumes from the database after restarts and
redeploys. Pages that were mid-read go back into the queue.

### All settings

| Variable | Default | Notes |
| --- | --- | --- |
| `ADMIN_PASSWORD` | – | Required. |
| `SECRET_KEY` | random per boot | Set it, or every deploy signs you out. |
| `DATABASE_URL` | SQLite in `DATA_DIR` | `postgres://…` URLs are accepted as-is. |
| `DATA_DIR` | `/data` (Docker) | Mount the Railway volume here. |
| `GEMINI_API_KEY` | – | Without it the app runs Tesseract only. `GOOGLE_API_KEY` also works. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Any Gemini vision model; use a Pro model for the hardest prints. |
| `GEMINI_THINKING_LEVEL` | `low` | Gemini 3.x. Blank to omit. |
| `GEMINI_THINKING_BUDGET` | – | For 2.5-series models (e.g. `0` on Flash). Overrides the level when set. |
| `GEMINI_MEDIA_RESOLUTION` | `MEDIA_RESOLUTION_HIGH` | Higher input resolution, better on small Greek accents. |
| `GEMINI_MAX_IMAGE_SIDE` | `3072` | Longest side (px) of images sent to Gemini. |
| `OCR_CONCURRENCY` | `3` | Pages read in parallel. Raise to 6–8 if your Gemini quota allows. |
| `HYBRID_CONF_THRESHOLD` | `80` | Tesseract confidence below which Hybrid escalates to Gemini. |
| `LOW_CONF_WORD` | `60` | Tesseract word confidence below which a word is highlighted. |
| `RENDER_DPI` | `300` | PDF page rendering resolution. |
| `PAGE_IMAGE_FORMAT` | `jpeg` | `png` is lossless but roughly 3× larger on disk. |
| `JPEG_QUALITY` | `92` | |
| `MAX_UPLOAD_MB` | `1500` | Per file. |
| `STORAGE_LIMIT_GB` | `0` | `0` uses the disk's reported size. |
| `COOKIE_SECURE` | `auto` | `auto` marks the cookie Secure on Railway. |

If Gemini rejects a thinking or media-resolution option for your chosen model, the app drops that option and
retries automatically.

---

## Run locally

With Docker:
```bash
docker build -t scriptorium-ocr .
docker run -p 8000:8000 -v "$PWD/data:/data" -e ADMIN_PASSWORD=secret -e SECRET_KEY=dev -e GEMINI_API_KEY=... scriptorium-ocr
```

Without Docker, you need Tesseract with language packs (`tesseract-ocr-grc`, `-lat`, `-heb`, …), Pango (for
WeasyPrint), `djvulibre-bin` for DjVu, and Noto fonts:
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
ADMIN_PASSWORD=secret DATA_DIR=./data uvicorn app.main:app --reload
```

Tests (upload → crop → OCR → review → every export, plus the Gemini path with a fake client):
```bash
pip install pytest httpx
pytest -q                                              # SQLite
TEST_DATABASE_URL=postgres://user:pw@localhost/db pytest -q   # against Postgres
```

---

## Inputs

PDF (scanned or born-digital), PNG, JPEG, TIFF (multi-page), BMP, WebP, GIF, JPEG 2000, **DjVu**, and **ZIP/CBZ**
archives of page images, sorted by file name in natural order. If you upload several files you can combine them
into one document.

## Project layout

```
app/
  main.py            FastAPI app, auth middleware, page routes
  api.py             JSON API (documents, pages, layouts, runs, exports, storage)
  config.py          environment settings
  db.py              SQLAlchemy models: Document, Page
  ingest.py          PDF/image/DjVu/ZIP → page images + thumbnails
  layout.py          crop-layout resolution (page > odd/even > all) and region cutting
  worker.py          in-process, restart-safe OCR queue
  ocr/
    scholarly.py          scholarly Greek profile: independent dual reading, proposals, provenance
    collation.py          Unicode-aware comparison of two witnesses (categories, line positions)
    greek.py              NFC policy, grapheme clusters, diacritic names, artifact detection
    segment.py            text-line bands, blank-row strips, crop-edge checks, preprocessing variants
    metrics.py            CER / grapheme / diacritic / symbol / WER / completeness metrics
    tesseract_engine.py   line-geometry paragraphing, hyphen rejoining, headings, word confidence;
                          strict line-level recognizer for the scholarly profile
    gemini_engine.py      structured-JSON transcription prompt, retries, config fallback
    embedded_engine.py    PDF text layer clipped to crop boxes
    router.py             hybrid routing and result storage
  export/
    assemble.py      pages → continuous text, page markers, cross-page joins, notes
    render_text.py   TXT + Markdown
    render_html.py   HTML/CSS for PDF (WeasyPrint) and EPUB
    render_docx.py   Word
    render_epub.py   EPUB 3 with embedded Noto fonts
static/              vanilla-JS frontend (no build step)
tools/benchmark.py   accuracy benchmark against verified transcriptions
tools/synth_greek.py synthetic polytonic test pages with exact ground truth
docker/fetch_models.py  pinned, checksum-verified tessdata_best Greek model (Docker build)
docs/                audit of the transcription path; interim benchmark
tests/               end-to-end smoke tests
```

## Known limits

- Parallel text is supported **within a page**, split at the divider you drag. Loeb-style facing *pages*
  (Greek on the left page, English on the right) export page after page, not paired.
- Tesseract is weak on polytonic Greek. In Hybrid mode those pages go to Gemini; in *Tesseract only* mode,
  expect heavy correction.
- One admin user, by design.
