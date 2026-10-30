// Tab III — Review: page image beside editable text, uncertain words highlighted.
import { api, h, toast, ENGINE_LABEL, debounce, pill } from "./common.js";

let ctx, root, built = false;
let detail = null;        // full page record being edited
let dirty = false;
let stepMode = "all";     // all | unreviewed | flagged
let zoom = 1;
let lastStatus = {};

let imgPane, sheet, img, textPane, bar, saveState;
const eds = {};

export function init(c, pane) {
  ctx = c; root = pane;
  ctx.on("page", () => { if (built && isShown()) loadPage(); });
  ctx.on("pages", onPagesUpdate);
  window.addEventListener("resize", () => built && fitSheet());
  window.addEventListener("beforeunload", (e) => { if (dirty) { save.flush(); e.preventDefault(); } });
}

const isShown = () => root.classList.contains("on");

export function show() {
  if (!built) build();
  if (!ctx.pages.length) return;
  loadPage();
}

function build() {
  built = true;
  saveState = h("span.savestate", "");
  bar = h("div.review-bar");
  img = h("img", { alt: "" });
  img.onload = fitSheet;
  sheet = h("div.sheet", img);
  imgPane = h("div.imgpane", zoomBar(), sheet);
  textPane = h("div.textpane");
  root.append(h("div.review-wrap", bar, h("div.review-body", imgPane, textPane)));
  root.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save.flush(); }
    if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); step(1); }
    if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); reviewedNext(); }
  });
}

// ------------------------------------------------------------------ highlighting editor
const escHtml = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function hlEditor(field, cls = "") {
  const back = h("div.backdrop");
  const ta = h("textarea", { spellcheck: false, dir: "auto" });
  const wrap = h(`div.hl-editor${cls ? "." + cls : ""}`, back, ta);
  let re = null;
  const paint = () => {
    let html = escHtml(ta.value);
    if (re) html = html.replace(re, "<mark>$1</mark>");
    back.innerHTML = html + (ta.value.endsWith("\n") ? " " : "") + "\n";
    ta.style.height = "auto";
    ta.style.height = Math.max(ta.scrollHeight, cls === "notes" ? 110 : 340) + "px";
  };
  ta.addEventListener("input", () => { dirty = true; saveState.textContent = "Unsaved"; saveState.className = "savestate dirty"; paint(); save(); });
  const ed = {
    wrap, ta, field,
    set(value, words) {
      ta.value = value || "";
      const ws = [...new Set((words || []).filter((w) => w && w.length > 1))].sort((a, b) => b.length - a.length).slice(0, 300);
      re = ws.length ? new RegExp(`(?<![\\p{L}\\p{M}])(${ws.map((w) => escRe(escHtml(w))).join("|")})(?![\\p{L}\\p{M}])`, "gu") : null;
      paint();
    },
    get: () => ta.value,
    repaint: paint,
  };
  return ed;
}

// ------------------------------------------------------------------ saving
const save = debounce(async () => {
  if (!detail || !dirty) return;
  const body = {};
  for (const ed of Object.values(eds)) body[ed.field] = ed.get();
  const pp = textPane.querySelector("input.pp");
  if (pp) body.printed_page = pp.value;
  saveState.textContent = "Saving…";
  try {
    const id = detail.id;
    const res = await api(`/api/pages/${id}`, { method: "PATCH", body });
    dirty = false;
    saveState.textContent = "Saved";
    saveState.className = "savestate";
    const p = ctx.pages.find((x) => x.id === id);
    if (p) Object.assign(p, res);
    Object.assign(detail, body, { edited: true });
  } catch (e) {
    saveState.textContent = "Not saved";
    toast(e.message, "error");
  }
}, 800);

// ------------------------------------------------------------------ navigation
function matchesStep(p) {
  if (p.skip) return false;
  if (stepMode === "unreviewed") return !p.reviewed;
  if (stepMode === "flagged") return p.flags > 0 || !!p.warning || p.status === "error";
  return true;
}

function step(dir) {
  for (let i = ctx.current + dir; i >= 0 && i < ctx.pages.length; i += dir) {
    if (matchesStep(ctx.pages[i])) { flushThen(() => ctx.goto(i)); return; }
  }
  toast(dir > 0 ? "No more matching pages after this one." : "No matching pages before this one.");
}

function flushThen(fn) { if (dirty) save.flush(); fn(); }

async function reviewedNext() {
  if (!detail) return;
  if (dirty) await save.flush();
  try {
    const res = await api(`/api/pages/${detail.id}`, { method: "PATCH", body: { reviewed: true } });
    Object.assign(ctx.pages[ctx.current], res);
  } catch (e) { return toast(e.message, "error"); }
  step(1);
}

async function rerun(engine) {
  if (!detail) return;
  if (detail.edited && !confirm("Re-reading will replace your edits on this page. Continue?")) return;
  try {
    await api(`/api/pages/${detail.id}/rerun`, { method: "POST", body: { engine } });
    dirty = false;
    toast(`Page ${detail.number} queued for ${ENGINE_LABEL[engine]}.`, "ok");
    await ctx.refreshDoc();
    await ctx.refreshPages();
  } catch (e) { toast(e.message, "error"); }
}

function onPagesUpdate() {
  if (!built || !detail) return;
  const p = ctx.pages.find((x) => x.id === detail.id);
  if (!p) return;
  const prev = lastStatus[p.id];
  lastStatus[p.id] = p.status;
  if (prev && prev !== "done" && p.status === "done" && !dirty) loadPage();
  else renderBar();
}

// ------------------------------------------------------------------ rendering
async function loadPage() {
  const sum = ctx.page;
  if (!sum) return;
  if (dirty) await save.flush();
  try { detail = await api(`/api/pages/${sum.id}`); }
  catch (e) { return toast(e.message, "error"); }
  lastStatus[detail.id] = detail.status;
  dirty = false;
  saveState.textContent = "";
  const src = `/api/pages/${detail.id}/image${detail.spread ? `?v=${detail.spread.split_x}` : ""}`;
  if (!img.src.endsWith(src)) img.src = src; else fitSheet();
  renderBar();
  renderText();
}

function renderBar() {
  const p = ctx.page;
  if (!p || !detail) return;
  const pageIn = h("input", { type: "text", value: String(p.number), style: { width: "60px", textAlign: "center", padding: "4px" } });
  pageIn.onchange = () => flushThen(() => ctx.goto(parseInt(pageIn.value, 10) - 1));
  const mode = h("select", { style: { width: "auto" } },
    ...[["all", "Step through all pages"], ["unreviewed", "Only unreviewed"], ["flagged", "Only flagged"]].map(([v, l]) => h("option", { value: v, selected: v === stepMode }, l)));
  mode.onchange = () => (stepMode = mode.value);
  const busy = ["queued", "running"].includes(p.status);
  bar.replaceChildren(
    h("button.icon.sm", { title: "Previous (Alt+←)", onclick: () => step(-1) }, "◀"),
    h("span.hint", "Page"), pageIn, h("span.hint", `of ${ctx.pages.length}`),
    h("button.icon.sm", { title: "Next (Alt+→)", onclick: () => step(1) }, "▶"),
    mode,
    h("div.info",
      pill(p.status),
      witnessBadge(p),
      p.engine ? h("span.badge.gold", ENGINE_LABEL[p.engine] || p.engine) : null,
      p.confidence !== null ? h("span", `${p.confidence.toFixed(0)}% conf.`) : null,
      p.route_reason && !p.witness_status ? h("span", `· ${p.route_reason}`) : null,
      p.flags ? h("span.badge.gold", { title: p.witness_status ? "Words involved in disagreements or marked uncertain are highlighted" : "Words the engine was unsure of are highlighted" }, `${p.flags} highlighted`) : null),
    h("div", { style: { flex: 1 } }),
    saveState,
    h("button.sm", { disabled: busy || !ctx.config.gemini_enabled || ctx.doc.images_purged, onclick: () => rerun("gemini") }, "Re-read · Gemini"),
    h("button.sm", { disabled: busy || ctx.doc.images_purged, onclick: () => rerun("tesseract") }, "Re-read · Tesseract"),
    h("button.sm", { title: "Earlier versions of this page's text", onclick: toggleHistory }, "History"),
    h(`button.sm${p.reviewed ? "" : ".primary"}`, { title: "Ctrl+Enter", onclick: reviewedNext }, p.reviewed ? "✓ Reviewed · next" : "Mark reviewed & next"),
  );
}

function renderText() {
  const d = detail;
  const separate = ctx.doc.settings.footnote_mode === "separate";
  const facing = d.layout?.mode === "facing" || !!d.right;
  const lines = d.text_format === "lines";
  for (const k of Object.keys(eds)) delete eds[k];

  const parts = [];
  historyBox = h("div");
  parts.push(historyBox);
  if (d.error) parts.push(h("div.banner", `OCR error: ${d.error}`));
  if (d.warning) parts.push(h("div.banner", { style: { background: "rgba(216,162,74,.1)", borderColor: "rgba(216,162,74,.4)", color: "#efd7a8" } }, d.warning));
  if (["queued", "running"].includes(d.status)) parts.push(h("div.hint", h("span.spin"), " This page is being read — it will refresh when done."));
  if (d.status === "new" && !d.body) parts.push(h("div.empty", "This page hasn't been read yet. Run OCR from the Process tab, or type the text here."));

  if (facing) {
    eds.body = hlEditor("body");
    eds.right = hlEditor("right");
    parts.push(h("div",
      h("div.ed-label", h("span.label", "Parallel text")),
      h("div.facing-eds", h("div", h("div.hint", "Left column"), eds.body.wrap), h("div", h("div.hint", "Right column"), eds.right.wrap))));
  } else {
    eds.body = hlEditor("body");
    parts.push(h("div", h("div.ed-label", h("span.label", lines ? "Main text — one printed line per line" : "Main text"),
      lines ? h("span.hint", "lines are joined into prose only when exporting") : null), eds.body.wrap));
  }
  const dis = disagreementPanel(d);
  if (dis) parts.push(dis);
  if (separate || d.notes) {
    eds.notes = hlEditor("notes", "notes");
    parts.push(h("div", h("div.ed-label", h("span.label", "Footnotes / apparatus"), separate ? null : h("span.hint", "footnotes are set to be dropped on export")), eds.notes.wrap));
  }
  const pp = h("input.pp", { type: "text", value: d.printed_page || "", placeholder: "—", style: { width: "90px" } });
  pp.oninput = () => { dirty = true; save(); };
  parts.push(h("div.row", h("span.label", "Printed page no."), pp, h("span.hint", "Used for [p. N] markers when set (Gemini fills this in).")));
  parts.push(lines ? h("div.cheats",
    "Type exactly what is printed. ", h("code", "## Heading"), " · blank line = new paragraph · a ", h("code", "%verse"),
    " line keeps the following lines as verse · ", h("code", "⟦17a⟧"), " reference · ", h("code", "*"), " is a literal asterisk",
    h("br"), h("kbd", "Alt"), "+", h("kbd", "←/→"), " pages · ", h("kbd", "Ctrl"), "+", h("kbd", "S"), " save · ",
    h("kbd", "Ctrl"), "+", h("kbd", "Enter"), " reviewed & next") : h("div.cheats",
    h("code", "## Heading"), " · ", h("code", "*italic*"), " · ", h("code", "**bold**"), " · ", h("code", "[^1]"), " note ref · ",
    h("code", "⟦17a⟧"), " reference · blank line = new paragraph · single line break = verse line",
    h("br"), h("kbd", "Alt"), "+", h("kbd", "←/→"), " pages · ", h("kbd", "Ctrl"), "+", h("kbd", "S"), " save · ",
    h("kbd", "Ctrl"), "+", h("kbd", "Enter"), " reviewed & next"));
  textPane.replaceChildren(...parts);
  textPane.scrollTop = 0;
  eds.body.set(d.body, d.low_conf);
  eds.right?.set(d.right, d.low_conf);
  eds.notes?.set(d.notes, d.low_conf);
}

function fitSheet() {
  if (!detail || !imgPane) return;
  const w = (imgPane.clientWidth - 28) * zoom;
  const s = w / detail.width;
  sheet.style.width = `${Math.round(detail.width * s)}px`;
  sheet.style.height = `${Math.round(detail.height * s)}px`;
  sheet.querySelectorAll(".ovl").forEach((e) => e.remove());
  const L = detail.layout || {};
  const add = (b, cls) => {
    if (!b) return;
    sheet.append(h(`div.ovl${cls}`, { style: { left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` } }));
  };
  add(L.main, ".dim");
  if (ctx.doc.settings.footnote_mode === "separate") add(L.notes, ".notes");
}

function zoomBar() {
  return h("div", { style: { position: "sticky", top: "8px", height: 0, display: "flex", justifyContent: "flex-end", paddingRight: "10px", zIndex: 3 } },
    h("div.seg", { style: { height: "fit-content" } },
      h("button", { onclick: () => { zoom = Math.max(0.5, zoom / 1.25); fitSheet(); } }, "−"),
      h("button", { onclick: () => { zoom = 1; fitSheet(); } }, "Fit"),
      h("button", { onclick: () => { zoom = Math.min(4, zoom * 1.25); fitSheet(); } }, "+")));
}


// ------------------------------------------------------------------ scholarly profile: witnesses, disagreements, history
let historyBox = null;

const WITNESS = {
  "engines-agree": [".green", "2 engines agree", "Both engines read every word the same way. That is agreement, not verification — check against the image."],
  "engines-disagree": [".gold", null, "The two independent readings differ; see the list under the text."],
  "single-witness": [".red", "1 reading only", "Only one engine produced a reading — not cross-checked."],
  "human-reviewed": [".green", "✓ reviewed", "You marked this page as checked against the image."],
};

function witnessBadge(p) {
  const w = WITNESS[p.witness_status];
  if (!w) return null;
  const label = w[1] || `${p.disagreements} disagreement${p.disagreements === 1 ? "" : "s"}`;
  return h(`span.badge${w[0]}`, { title: w[2] }, label);
}

const CAT_LABEL = {
  accent: "accent", breathing: "breathing", "iota-subscript": "iota subscript", diaeresis: "diaeresis",
  diacritics: "diacritics", letter: "letters", punctuation: "punctuation", "editorial-sign": "editorial sign",
  omission: "only Tesseract", addition: "only Gemini", "line-omission": "line only in Tesseract",
  "line-addition": "line only in Gemini", duplication: "duplicated", reordering: "order", "script-artifact": "script mix",
  "other-diacritic": "diacritic", case: "capitalization",
};

function disagreementPanel(d) {
  const regions = d.collation?.regions || {};
  const rows = [];
  for (const [reg, c] of Object.entries(regions)) {
    for (const it of c.items || []) if (it.severity === "substantive") rows.push({ ...it, reg, prim: (d.collation.primary || {})[reg] });
  }
  if (!rows.length && !Object.keys(regions).length) return null;
  const head = h("div.ed-label", h("span.label", `Engine disagreements (${rows.length})`),
    h("span.hint", "Tesseract and Gemini read the page independently. Choose what the image shows."));
  if (!rows.length) {
    return h("div", head, h("div.hint", "No substantive disagreements. Two engines agreeing is not proof — both can misread the same mark."));
  }
  const list = h("div.dis-list");
  for (const it of rows.slice(0, 200)) {
    const a = it.a?.text, b = it.b?.text, prop = it.proposal && it.proposal.status === "proposed" ? it.proposal : null;
    const line = (it.prim === "gemini" ? it.b : it.a) || it.a || it.b;
    const btns = [];
    if (a && b) {
      btns.push(h("button.sm", { onclick: (e) => apply(it, a, e.target) }, "Use Tesseract"));
      btns.push(h("button.sm", { onclick: (e) => apply(it, b, e.target) }, "Use Gemini"));
      if (prop && prop.reading && prop.reading !== a && prop.reading !== b) {
        btns.push(h("button.sm", { onclick: (e) => apply(it, prop.reading, e.target) }, "Use proposal"));
      }
    }
    list.append(h("div.dis-row",
      h("div.dis-meta", h("span.badge", CAT_LABEL[it.category] || it.category), line ? h("span.hint", ` ${it.reg} · line ${line.line + 1}`) : null),
      h("div.dis-read", h("span.who", "Tesseract"), h("span.txt", a ?? "—")),
      h("div.dis-read", h("span.who", "Gemini"), h("span.txt", b ?? "—")),
      prop ? h("div.dis-read.prop", h("span.who", "Proposal"), h("span.txt", prop.reading || "—"),
        h("span.hint", ` ${prop.choice === "a" ? "matches Tesseract" : prop.choice === "b" ? "matches Gemini" : prop.choice}${prop.evidence ? " — " + prop.evidence : ""}`)) : null,
      btns.length ? h("div.row", { style: { marginTop: "4px" } }, ...btns) : h("div.hint", "Fix by hand in the text above if needed."),
    ));
  }
  return h("div", head, list);
}

// Replace the primary witness's reading on its line in the editor with the chosen reading.
function apply(it, reading, btn) {
  const side = it.prim === "gemini" ? "b" : "a";
  const cur = it[side];
  const ed = it.reg === "right" ? eds.right : it.reg === "notes" ? eds.notes : eds.body;
  if (!cur || !ed) return toast("Edit this one by hand.", "error");
  const all = ed.get().split("\n");
  // map witness line index → editor line (skip blank and %verse lines; regions before this one come first)
  let offset = 0;
  if (it.reg === "right" && detail.layout?.mode === "columns") {
    offset = (detail.collation.regions.left?.lines?.[detail.collation.primary.left === "gemini" ? "b" : "a"]) || 0;
  }
  const idx = [];
  all.forEach((l, i) => { if (l.trim() && l.trim() !== "%verse") idx.push(i); });
  const target = idx[offset + cur.line];
  if (target === undefined || !all[target].includes(cur.text)) {
    return toast("That line has changed since OCR — edit it by hand.", "error");
  }
  all[target] = all[target].replace(cur.text, reading);
  ed.ta.value = all.join("\n");
  ed.ta.dispatchEvent(new Event("input"));
  cur.text = reading;
  btn.closest(".dis-row").classList.add("done");
  toast("Applied — saved automatically.", "ok");
}

async function toggleHistory() {
  if (!historyBox || !detail) return;
  if (historyBox.childElementCount) { historyBox.replaceChildren(); return; }
  try {
    const revs = await api(`/api/pages/${detail.id}/revisions`);
    if (!revs.length) { historyBox.replaceChildren(h("div.hint", "No earlier versions of this page.")); return; }
    historyBox.replaceChildren(h("div.card", { style: { padding: "10px 12px" } },
      h("div.ed-label", h("span.label", "Earlier versions"), h("span.hint", "Restoring keeps the current text as a version too.")),
      ...revs.map((r) => h("div.row", { style: { padding: "4px 0", borderTop: "1px solid var(--line)" } },
        h("span.hint", new Date(r.created_at).toLocaleString()), h("span.badge", r.reason || "snapshot"),
        r.edited ? h("span.badge.blue", "EDITED") : null, r.reviewed ? h("span.badge.green", "✓") : null,
        h("span.hint", { style: { flex: 1, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" } }, r.preview),
        h("button.sm", { onclick: () => restore(r.id) }, "Restore")))));
  } catch (e) { toast(e.message, "error"); }
}

async function restore(revId) {
  if (dirty) await save.flush();
  try {
    await api(`/api/pages/${detail.id}/revisions/${revId}/restore`, { method: "POST" });
    toast("Earlier version restored.", "ok");
    await ctx.refreshPages();
    loadPage();
  } catch (e) { toast(e.message, "error"); }
}
