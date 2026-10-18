// Tab II — Process: document OCR settings, run / re-run, progress and per-page status.
import { api, h, toast, pill, langChips, ENGINES, ENGINE_LABEL, debounce } from "./common.js";

let ctx, root, settingsCard, runCard, pagesCard;
let filter = "all";

export function init(c, pane) {
  ctx = c; root = pane;
  settingsCard = h("div.card");
  runCard = h("div.card");
  pagesCard = h("div.card");
  root.append(h("div.scrollpane", h("div.process-grid", h("div", settingsCard), h("div", runCard, pagesCard))));
  ctx.on("doc", () => { renderRun(); if (!settingsCard.contains(document.activeElement)) renderSettings(); });
  ctx.on("pages", () => { renderRun(); renderPages(); });
}

export function show() { renderSettings(); renderRun(); renderPages(); }

const saveSettings = debounce(async (patch) => {
  try { await ctx.patchDoc({ settings: patch }); }
  catch (e) { toast(e.message, "error"); }
}, 500);

function renderSettings() {
  const s = ctx.doc.settings;
  const cfg = ctx.config;
  const pending = {};
  const set = (k, v) => { pending[k] = v; saveSettings({ ...pending }); };

  const engineSel = h("select", ENGINES.map(([v, l]) => h("option", { value: v, disabled: v === "gemini" && !cfg.gemini_enabled }, l)));
  engineSel.value = s.engine;
  const engHint = h("div.hint", ENGINES.find((e) => e[0] === s.engine)?.[2] || "");
  engineSel.onchange = () => { engHint.textContent = ENGINES.find((e) => e[0] === engineSel.value)[2]; set("engine", engineSel.value); };

  const fn = h("div.seg",
    h(`button${s.footnote_mode === "drop" ? ".on" : ""}`, { type: "button", onclick: (e) => pick(e, "drop") }, "Drop footnotes"),
    h(`button${s.footnote_mode === "separate" ? ".on" : ""}`, { type: "button", onclick: (e) => pick(e, "separate") }, "Keep as notes"));
  function pick(e, v) { [...fn.children].forEach((b) => b.classList.toggle("on", b === e.target)); set("footnote_mode", v); }

  const chk = (k, label, hint, disabled) => {
    const i = h("input", { type: "checkbox", checked: !!s[k], disabled, onchange: () => set(k, i.checked) });
    return h("label.check", i, h("span", label, h("div.hint", hint)));
  };
  const textLayerPages = ctx.pages.filter((p) => p.has_text_layer).length;
  const hint = h("textarea", { rows: 3 });
  hint.value = s.hint || "";
  hint.oninput = () => set("hint", hint.value);

  settingsCard.replaceChildren(
    h("h3", "Reading settings"),
    h("div.field", h("span", "Languages on the page"), langChips(cfg, s.languages, (v) => set("languages", v))),
    h("label.field", h("span", "OCR engine"), engineSel, engHint),
    h("div.field", h("span", "Footnotes & apparatus"), fn,
      h("div.hint", "“Keep as notes” reads the blue footnote box (and footnotes Gemini finds) into a separate notes field, exported as endnotes.")),
    chk("preserve_refs", "Preserve reference numbers", "Stephanus, Bekker, chapter and line numbers become inline markers like ⟦17a⟧. In hybrid mode this sends every page to Gemini, so keep marginal numbers inside the main box."),
    chk("use_text_layer", `Use the PDF's text layer${textLayerPages ? ` (${textLayerPages} pages have one)` : " (none found)"}`, "Free and exact for born-digital PDFs. Old scans often carry poor hidden OCR.", !textLayerPages),
    h("label.field", h("span", "Notes for the transcriber"), hint, h("div.hint", "Passed to Gemini, e.g. edition, script peculiarities, abbreviations to keep.")),
    h("p.hint", cfg.gemini_enabled ? `Gemini model: ${cfg.gemini_model}. Hybrid sends pages below ${cfg.hybrid_threshold}% Tesseract confidence to Gemini.` : "Gemini is not configured on the server — Tesseract only."),
  );
}

function renderRun() {
  const d = ctx.doc;
  const total = Math.max(0, d.page_count - (d.skipped || 0));
  const p = d.progress;
  const busy = d.status === "processing";
  const unread = ctx.pages.filter((x) => !x.skip && (x.status === "new" || x.status === "error")).length;
  const pct = total ? (100 * p.done) / total : 0;

  const scopeSel = h("select",
    h("option", { value: "unprocessed" }, `Unread & failed pages (${unread})`),
    h("option", { value: "all" }, "All pages"),
    h("option", { value: "errors" }, `Failed pages (${p.error})`),
    h("option", { value: "flagged" }, "Pages with uncertain words or warnings"));
  const engSel = h("select",
    h("option", { value: "" }, "Engine: document setting"),
    h("option", { value: "tesseract" }, "Force Tesseract"),
    h("option", { value: "gemini", disabled: !ctx.config.gemini_enabled }, "Force Gemini"),
    h("option", { value: "embedded" }, "Force PDF text layer"));
  const incEdited = h("input", { type: "checkbox" });

  const run = async () => {
    try {
      const r = await api(`/api/docs/${ctx.id}/run`, { method: "POST", body: { scope: scopeSel.value, engine: engSel.value, include_edited: incEdited.checked } });
      toast(r.queued ? `Queued ${r.queued} page(s).` : "Nothing to do for that selection.", r.queued ? "ok" : "");
      await ctx.refreshDoc(); await ctx.refreshPages();
    } catch (e) { toast(e.message, "error"); }
  };

  const tokens = d.tokens_in + d.tokens_out;
  runCard.replaceChildren(
    h("div.row", h("h3", { style: { flex: 1, margin: 0 } }, "Run OCR"), pill(d.status)),
    h("div.stats",
      h("div.stat", h("b", `${p.done}/${total}`), h("span", "pages read")),
      h("div.stat", h("b", p.queued + p.running), h("span", "in queue")),
      h("div.stat", h("b", { style: { color: p.error ? "var(--danger)" : "" } }, p.error), h("span", "errors")),
      h("div.stat", h("b", d.gemini_pages), h("span", tokens ? `Gemini · ${(tokens / 1000).toFixed(1)}k tokens` : "via Gemini"))),
    h("div.progress.big", h("i", { style: { width: pct + "%" } }), p.error ? h("i.err", { style: { width: (100 * p.error) / (total || 1) + "%" } }) : null),
    h("div", { style: { height: "14px" } }),
    d.status === "ingesting" ? h("p.hint", "Waiting for the import to finish…") :
    d.images_purged ? h("p.hint", "Page images were purged for this document, so OCR can't be re-run. The text can still be edited and exported.") :
    h("div",
      h("div.row", scopeSel, engSel),
      h("label.check", incEdited, h("span", "Also re-read pages I've edited", h("div.hint", "Off by default so your corrections are never overwritten."))),
      h("div.row", { style: { marginTop: "6px" } },
        h("button.primary", { onclick: run, disabled: busy && false }, unread === total && total ? "Start OCR" : "Run"),
        busy ? h("button", { onclick: () => post("pause") }, "Pause") : null,
        d.status === "paused" ? h("button.primary", { onclick: () => post("resume") }, "Resume") : null,
        busy || d.status === "paused" ? h("button.danger", { onclick: () => post("cancel") }, "Cancel queue") : null,
        d.status === "review" ? h("button", { onclick: () => ctx.showTab("review") }, "Go to Review →") : null)),
  );
}

async function post(what) {
  try { await api(`/api/docs/${ctx.id}/${what}`, { method: "POST" }); await ctx.refreshDoc(); await ctx.refreshPages(); }
  catch (e) { toast(e.message, "error"); }
}

function renderPages() {
  if (!ctx.pages.length) { pagesCard.replaceChildren(h("h3", "Pages"), h("div.empty", "No pages yet.")); return; }
  const f = {
    all: () => true,
    errors: (p) => p.status === "error" || p.warning,
    gemini: (p) => p.engine === "gemini",
    flagged: (p) => p.flags > 0,
    low: (p) => p.confidence !== null && p.confidence < 85,
    skipped: (p) => p.skip,
  }[filter];
  const rows = ctx.pages.filter(f).map((p) => h("tr", { onclick: () => ctx.goto(p.index, "review") },
    h("td", p.number),
    h("td", p.skip ? h("span.badge", "SKIPPED") : pill(p.status)),
    h("td", ENGINE_LABEL[p.engine] || "–"),
    h("td.hint", p.route_reason || ""),
    h("td", p.confidence !== null ? `${p.confidence.toFixed(0)}%` : "–"),
    h("td", p.flags ? h("span.badge.gold", p.flags) : ""),
    h("td", p.edited ? h("span.badge.blue", "EDITED") : "", " ", p.reviewed ? h("span.badge.green", "✓") : ""),
    h("td.err", { title: p.error || p.warning }, p.error || p.warning || ""),
  ));
  const filt = h("select", ...[["all", "All pages"], ["errors", "Errors & warnings"], ["gemini", "Read by Gemini"], ["flagged", "Uncertain words"], ["low", "Confidence < 85%"], ["skipped", "Skipped"]]
    .map(([v, l]) => h("option", { value: v, selected: v === filter }, l)));
  filt.style.width = "auto";
  filt.onchange = () => { filter = filt.value; renderPages(); };
  const wrap = pagesCard.querySelector(".tablewrap");
  const scroll = wrap ? wrap.scrollTop : 0;
  pagesCard.replaceChildren(
    h("div.row", { style: { marginBottom: "10px" } }, h("h3", { style: { flex: 1, margin: 0 } }, "Pages"), filt),
    h("div.tablewrap", h("table.pages",
      h("thead", h("tr", ...["#", "Status", "Engine", "Why", "Conf.", "Flags", "", "Notes"].map((t) => h("th", t)))),
      h("tbody", rows.length ? rows : [h("tr", h("td", { colspan: 8, class: "hint" }, "No pages match."))]))),
    h("p.hint", "Click a page to open it in Review."),
  );
  pagesCard.querySelector(".tablewrap").scrollTop = scroll;
}
