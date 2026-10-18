// Tab IV — Export: options, live preview, downloads in TXT / Markdown / PDF / DOCX / EPUB.
import { api, h, toast, debounce } from "./common.js";

let ctx, root, built = false;
let opts = null;
let previewEl, statsEl, optsCard;

const DEFAULTS = {
  page_markers: true, marker_format: "[p. {n}]", first_number: 1, use_printed: true, join_pages: true,
  include_notes: true, notes_placement: "end", refs: "inline", page_size: "letter", font_size: 11, title_page: true,
};
const FORMATS = [
  ["pdf", "PDF", "Re-typeset text PDF with running head and page numbers"],
  ["docx", "Word", "Editable .docx with headings and parallel-text tables"],
  ["epub", "EPUB", "E-reader book with embedded Greek/Hebrew/Arabic fonts"],
  ["md", "Markdown", "Headings, italics and markers as plain markdown"],
  ["txt", "Plain text", "Raw UTF-8 text with page markers"],
];

export function init(c, pane) {
  ctx = c; root = pane;
  ctx.on("doc", () => { if (built && !opts) loadOpts(); });
}

export function show() {
  if (!built) build();
  refreshPreview();
}

function loadOpts() { opts = { ...DEFAULTS, ...(ctx.doc.export_options || {}) }; }

function query() {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) q.set(k, typeof v === "boolean" ? (v ? "1" : "0") : String(v));
  return q.toString();
}

function build() {
  built = true;
  loadOpts();
  previewEl = h("div.preview");
  statsEl = h("span.hint");
  optsCard = h("div.card");
  const fmtGrid = h("div.formats", FORMATS.map(([f, name, desc]) => {
    const b = h("button.fmt", { onclick: () => download(f, b) }, h("b", name), h("span", desc));
    return b;
  }));
  root.append(h("div.scrollpane", h("div.export-grid",
    h("div", optsCard),
    h("div", h("div.row", { style: { marginBottom: "10px" } }, h("h3", { style: { margin: 0, flex: 1 } }, "Download"), statsEl), fmtGrid,
      h("div.row", { style: { margin: "6px 0 8px" } }, h("span.label", "Preview"), h("span.hint", "Markdown view of the assembled text")),
      previewEl))));
  renderOpts();
}

const refreshPreview = debounce(async () => {
  try {
    const r = await api(`/api/docs/${ctx.id}/preview?${query()}`);
    previewEl.innerHTML = r.markdown
      .replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]))
      .replace(/⟦([^⟧]*)⟧/g, '<span class="ref">⟦$1⟧</span>')
      + (r.truncated ? "\n\n… (preview truncated)" : "");
    if (opts.page_markers) {
      const marker = opts.marker_format.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace("\\{n\\}", "[^\\s\\]]+");
      try { previewEl.innerHTML = previewEl.innerHTML.replace(new RegExp(marker, "g"), (m) => `<span class="pg">${m}</span>`); } catch {}
    }
    statsEl.textContent = `${r.chars.toLocaleString()} characters`;
    if (!r.chars || r.markdown.split("\n").length < 3) previewEl.innerHTML = '<span style="color:#888;font-style:italic">Nothing to export yet — run OCR first.</span>';
  } catch (e) { previewEl.textContent = e.message; }
}, 350);

async function download(fmt, btn) {
  btn.classList.add("busy");
  const label = btn.querySelector("span").textContent;
  btn.querySelector("span").replaceChildren(h("span.spin"), " Generating…");
  try {
    const r = await fetch(`/api/docs/${ctx.id}/export/${fmt}?${query()}`, { credentials: "same-origin" });
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail; } catch {} throw new Error(m); }
    const blob = await r.blob();
    const cd = r.headers.get("content-disposition") || "";
    const m = cd.match(/filename\*=utf-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
    const name = m ? decodeURIComponent(m[1]) : `document.${fmt}`;
    const a = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  } catch (e) { toast(`Export failed: ${e.message}`, "error"); }
  btn.classList.remove("busy");
  btn.querySelector("span").textContent = label;
}

function renderOpts() {
  const set = (k, v) => { opts[k] = v; refreshPreview(); };
  const chk = (k, label, hint) => {
    const i = h("input", { type: "checkbox", checked: !!opts[k], onchange: () => { set(k, i.checked); renderOpts(); } });
    return h("label.check", i, h("span", label, hint ? h("div.hint", hint) : null));
  };
  const text = (k, attrs = {}) => {
    const i = h("input", { type: attrs.type || "text", value: String(opts[k]), ...attrs });
    i.oninput = () => set(k, attrs.type === "number" ? Number(i.value || 0) : i.value);
    return i;
  };
  const select = (k, options) => {
    const s = h("select", options.map(([v, l]) => h("option", { value: v, selected: String(opts[k]) === String(v) }, l)));
    s.onchange = () => set(k, s.value);
    return s;
  };
  const separate = ctx.doc.settings.footnote_mode === "separate";

  optsCard.replaceChildren(...[
    h("h3", "Export options"),
    h("span.label", "Page references"),
    chk("page_markers", "Insert original page markers", "Keeps citations to the printed edition."),
    opts.page_markers ? h("div", { style: { paddingLeft: "24px" } },
      h("label.field", h("span", "Marker format"), text("marker_format"), h("div.hint", "{n} becomes the page number, e.g. [p. {n}] or |{n}|")),
      h("div.row", { style: { marginBottom: "10px" } }, h("span.hint", "PDF page 1 is printed page"), text("first_number", { type: "number" })),
      chk("use_printed", "Prefer page numbers read off the page", "Gemini reports the printed number; edit any page's number in Review.")) : null,
    chk("join_pages", "Continue paragraphs across page breaks", "Rejoins words hyphenated across pages; the marker stays where the break was."),
    h("hr"),
    h("span.label", "Notes & references"),
    separate
      ? h("div", chk("include_notes", "Include footnotes"),
        opts.include_notes ? h("label.field", { style: { paddingLeft: "24px" } }, h("span", "Place notes"), select("notes_placement", [["end", "As endnotes at the end"], ["page", "After each page's text"]])) : null)
      : h("p.hint", "Footnotes are set to be dropped for this document (Process tab)."),
    h("label.field", h("span", "Reference markers ⟦17a⟧"), select("refs", [["inline", "Keep inline"], ["hide", "Remove"]])),
    h("hr"),
    h("span.label", "PDF & Word layout"),
    h("div.row", { style: { margin: "6px 0 10px" } },
      h("label.field", { style: { flex: 1, margin: 0 } }, h("span", "Page size"), select("page_size", [["letter", "US Letter"], ["a4", "A4"], ["a5", "A5"], ["6x9", "6 × 9 in"]])),
      h("label.field", { style: { margin: 0 } }, h("span", "Font pt"), text("font_size", { type: "number", min: 7, max: 18, step: 0.5 }))),
    chk("title_page", "Title page"),
  ].filter(Boolean));
}
