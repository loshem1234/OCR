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
    tesseract_engine.py   line-geometry paragraphing, hyphen rejoining, headings, word confidence
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
tests/               end-to-end smoke tests
```

## Known limits

- Parallel text is supported **within a page**, split at the divider you drag. Loeb-style facing *pages*
  (Greek on the left page, English on the right) export page after page, not paired.
- Tesseract is weak on polytonic Greek. In Hybrid mode those pages go to Gemini; in *Tesseract only* mode,
  expect heavy correction.
- One admin user, by design.
