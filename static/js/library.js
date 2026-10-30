import { api, h, toast, fmtBytes, fmtDate, pill, topbar, storageMeter, langChips, ENGINES } from "./common.js";

const app = document.getElementById("app");
const meter = storageMeter();
app.append(topbar([meter]));

let config = null;
let docs = [];
const selected = new Set();
let filter = "";

const listEl = h("div.doclist");
const bannerEl = h("div");
const bulkBar = h("div.row");

// ------------------------------------------------------------------ upload card
function uploadCard() {
  const state = {
    files: [], languages: ["eng"], engine: config.gemini_enabled ? "auto" : "tesseract",
    footnote_mode: "drop", preserve_refs: false, use_text_layer: false, combine: true, split_spreads: false,
  };
  const fileInput = h("input", { type: "file", multiple: true, accept: config.accepted.join(","), style: { display: "none" } });
  const fileList = h("div.files");
  const drop = h("div.drop",
    h("div", h("strong", "Drop files here"), " or click to choose"),
    h("div.hint", "PDF, TIFF, JPG/PNG, DjVu, or a ZIP of page images"),
    fileList,
  );
  const setFiles = (fl) => {
    state.files = [...fl];
    fileList.replaceChildren(...state.files.map((f) => h("div", `${f.name} · ${fmtBytes(f.size)}`)));
    combineRow.style.display = state.files.length > 1 ? "" : "none";
    if (!titleIn.value && state.files[0]) titleIn.placeholder = state.files[0].name.replace(/\.[^.]+$/, "");
  };
  drop.onclick = () => fileInput.click();
  fileInput.onchange = () => setFiles(fileInput.files);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); setFiles(e.dataTransfer.files); };

  const titleIn = h("input", { type: "text", placeholder: "Title (defaults to file name)" });
  const authorIn = h("input", { type: "text", placeholder: "Author / editor (optional)" });
  const engineSel = h("select", ENGINES.map(([v, l]) => h("option", { value: v, disabled: v === "gemini" && !config.gemini_enabled }, l)));
  engineSel.value = state.engine;
  const engineHint = h("div.hint");
  const updEngineHint = () => (engineHint.textContent = ENGINES.find((e) => e[0] === engineSel.value)[2]);
  engineSel.onchange = () => { state.engine = engineSel.value; updEngineHint(); };
  updEngineHint();

  const fnSeg = h("div.seg",
    h("button.on", { type: "button", "data-v": "drop" }, "Drop footnotes"),
    h("button", { type: "button", "data-v": "separate" }, "Keep as notes"));
  fnSeg.onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.footnote_mode = b.dataset.v;
    [...fnSeg.children].forEach((x) => x.classList.toggle("on", x === b));
  };
  const check = (key, label, hint) => {
    const inp = h("input", { type: "checkbox", checked: state[key], onchange: () => (state[key] = inp.checked) });
    return h("label.check", inp, h("span", label, hint ? h("div.hint", hint) : null));
  };
  const combineRow = check("combine", "Combine files into one document", "Pages are ordered by file order.");
  combineRow.style.display = "none";
  const hintIn = h("textarea", { rows: 2, placeholder: "Context for Gemini, e.g. ‘Teubner edition of Proclus, In Parmenidem, Greek with Latin preface’" });

  const prog = h("div.upload-progress", h("div.progress.big", h("i", { style: { width: "0%" } })), h("div.hint", "Uploading…"));
  const go = h("button.primary", { type: "button", style: { width: "100%", justifyContent: "center" } }, "Upload & import");
  go.onclick = () => {
    if (!state.files.length) return toast("Choose at least one file first.", "error");
    const fd = new FormData();
    state.files.forEach((f) => fd.append("files", f));
    fd.append("title", titleIn.value);
    fd.append("author", authorIn.value);
    fd.append("languages", state.languages.join(","));
    fd.append("engine", state.engine);
    fd.append("footnote_mode", state.footnote_mode);
    fd.append("preserve_refs", state.preserve_refs);
    fd.append("use_text_layer", state.use_text_layer);
    fd.append("combine", state.combine);
    fd.append("split_spreads", state.split_spreads);
    fd.append("hint", hintIn.value);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/docs");
    prog.classList.add("on");
    go.disabled = true;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = (e.loaded / e.total) * 100;
      prog.querySelector("i").style.width = pct + "%";
      prog.querySelector(".hint").textContent = pct >= 100 ? "Saving on the server…" : `Uploading… ${pct.toFixed(0)}%`;
    };
    xhr.onload = () => {
      go.disabled = false;
      prog.classList.remove("on");
      if (xhr.status === 401) return (location.href = "/login");
      if (xhr.status >= 300) {
        let m = xhr.statusText; try { m = JSON.parse(xhr.responseText).detail; } catch {}
        return toast(m, "error");
      }
      const created = JSON.parse(xhr.responseText);
      toast(`Importing ${created.length} document${created.length > 1 ? "s" : ""}…`, "ok");
      setFiles([]); titleIn.value = ""; authorIn.value = ""; fileInput.value = "";
      if (created.length === 1) location.href = `/doc/${created[0].id}`;
      else load();
    };
    xhr.onerror = () => { go.disabled = false; prog.classList.remove("on"); toast("Upload failed — check your connection.", "error"); };
    xhr.send(fd);
  };

  return h("div",
    h("div.card",
      h("h3", "New document"),
      drop, fileInput,
      h("label.field", h("span", "Title"), titleIn),
      h("label.field", h("span", "Author"), authorIn),
      h("div.field", h("span", "Languages on the page"), langChips(config, state.languages, (v) => (state.languages = v))),
      h("label.field", h("span", "OCR engine"), engineSel, engineHint),
      h("div.field", h("span", "Footnotes & apparatus"), fnSeg),
      check("preserve_refs", "Preserve reference numbers", "Keeps Stephanus, Bekker and line numbers as inline markers like ⟦17a⟧. Sends pages to Gemini."),
      check("use_text_layer", "Use the PDF's own text layer if it has one", "Best for born-digital PDFs. Old scans often carry poor hidden OCR, so leave this off for those."),
      check("split_spreads", "Split two-page scans", "Each landscape scan (two book pages side by side) becomes a left and a right page, cut at the gutter. Portrait scans stay single."),
      combineRow,
      h("label.field", h("span", "Notes for the transcriber (optional)"), hintIn),
      go, prog,
      config.gemini_enabled ? null : h("p.hint", "Gemini isn't configured (GEMINI_API_KEY), so pages will be read with Tesseract only."),
    ),
    h("p.hint", { style: { marginTop: "10px" } }, "You can change all of these later from the document's Process tab."),
  );
}

// ------------------------------------------------------------------ list
function progressOf(d) {
  if (d.status === "ingesting") return { pct: d.page_count ? (d.ingest_done / d.page_count) * 100 : 5, label: `Importing ${d.ingest_done}/${d.page_count || "?"}` };
  const p = d.progress, total = d.page_count - (d.skipped || 0);
  if (!total) return { pct: 0, label: "" };
  return { pct: (p.done / total) * 100, err: (p.error / total) * 100, label: `${p.done}/${total} read${p.error ? ` · ${p.error} errors` : ""}` };
}

function row(d) {
  const cb = h("input", { type: "checkbox", checked: selected.has(d.id) });
  cb.onchange = () => { cb.checked ? selected.add(d.id) : selected.delete(d.id); renderBulk(); r.classList.toggle("sel", cb.checked); };
  const pr = progressOf(d);
  const r = h(`div.docrow${selected.has(d.id) ? ".sel" : ""}`,
    cb,
    h("a.thumb", { href: `/doc/${d.id}`, style: { backgroundImage: d.page_count && d.status !== "error" ? `url(/api/docs/${d.id}/thumb)` : "" } }),
    h("div",
      h("a.title", { href: `/doc/${d.id}` }, d.title),
      h("div.meta", [d.author, d.sources.map((s) => s.name).join(", "), fmtDate(d.created_at)].filter(Boolean).join(" · ")),
      d.error ? h("div.meta", { style: { color: "var(--danger)" } }, d.error) : null,
      d.images_purged ? h("div.meta", h("span.badge", "IMAGES PURGED"), " text kept") : null,
    ),
    h("div.dstat", pill(d.status), h("div.progress", { style: { marginTop: "7px" } },
      h("i", { style: { width: pr.pct + "%" } }), pr.err ? h("i.err", { style: { width: pr.err + "%" } }) : null),
      h("div.hint", { style: { fontSize: "11px", marginTop: "3px" } }, pr.label)),
    h("div.num", d.page_count || "–", h("small", "pages")),
    h("div.num", fmtBytes(d.size_bytes), h("small", "on disk")),
  );
  return r;
}

function renderBulk() {
  const n = selected.size;
  const visible = docs.filter(matches);
  const all = h("input", { type: "checkbox", checked: n > 0 && visible.every((d) => selected.has(d.id)), title: "Select all" });
  all.onchange = () => { visible.forEach((d) => (all.checked ? selected.add(d.id) : selected.delete(d.id))); render(); };
  const totalSel = docs.filter((d) => selected.has(d.id)).reduce((a, d) => a + d.size_bytes, 0);
  bulkBar.replaceChildren(
    all,
    h("span.hint", n ? `${n} selected · ${fmtBytes(totalSel)}` : "Select documents to free space"),
    h("button.sm", { disabled: !n, title: "Delete page images and originals but keep the OCR text and exports", onclick: () => bulk("purge") }, "Purge images"),
    h("button.sm.danger", { disabled: !n, onclick: () => bulk("delete") }, "Delete"),
  );
}

async function bulk(action) {
  const ids = [...selected];
  const msg = action === "delete"
    ? `Permanently delete ${ids.length} document(s) and all their text? This cannot be undone.`
    : `Purge page images for ${ids.length} document(s)? The OCR text stays and can still be edited and exported, but you won't be able to re-crop or re-run OCR.`;
  if (!confirm(msg)) return;
  try {
    const r = await api("/api/docs/bulk", { method: "POST", body: { action, ids } });
    toast(`${action === "delete" ? "Deleted" : "Purged"} ${r.ids.length} document(s).`, "ok");
    selected.clear();
    await load();
  } catch (e) { toast(e.message, "error"); }
}

const matches = (d) => !filter || (d.title + " " + d.author).toLowerCase().includes(filter);

function render() {
  const visible = docs.filter(matches);
  listEl.replaceChildren(...(visible.length ? visible.map(row) : [h("div.empty", docs.length ? "Nothing matches." : "The library is empty. Upload a scan to begin.")]));
  renderBulk();
  const s = meter.data;
  bannerEl.replaceChildren(...(s && s.warning ? [h("div.banner",
    `Storage is ${s.percent}% full (${fmtBytes(s.free_bytes)} free). Purge images from finished documents, or delete ones you no longer need.`)] : []));
}

let timer = null;
async function load() {
  try {
    docs = await api("/api/docs");
    await meter.refresh();
    render();
  } catch (e) { toast(e.message, "error"); }
  clearTimeout(timer);
  if (docs.some((d) => ["ingesting", "processing"].includes(d.status))) timer = setTimeout(load, 3000);
}

(async () => {
  config = await api("/api/config");
  const search = h("input", { type: "text", placeholder: "Search titles…" });
  search.oninput = () => { filter = search.value.trim().toLowerCase(); render(); };
  app.append(h("main.library",
    h("aside", uploadCard()),
    h("section", h("div.lib-head", h("h2", "Library"), search), bannerEl, h("div.card", { style: { padding: "10px 14px", marginBottom: "10px" } }, bulkBar), listEl),
  ));
  load();
})();
