import { api, h, toast, pill, topbar, storageMeter, debounce } from "./common.js";
import * as layoutTab from "./crop.js";
import * as processTab from "./process.js";
import * as reviewTab from "./review.js";
import * as exportTab from "./export.js";

const id = location.pathname.split("/").filter(Boolean).pop();
const app = document.getElementById("app");
const listeners = {};

const ctx = {
  id, config: null, doc: null, pages: [], current: 0,
  on(evt, fn) { (listeners[evt] ||= []).push(fn); },
  emit(evt, ...a) { (listeners[evt] || []).forEach((fn) => { try { fn(...a); } catch (e) { console.error(e); } }); },
  async refreshDoc() { ctx.doc = await api(`/api/docs/${id}`); ctx.emit("doc"); return ctx.doc; },
  async refreshPages() { ctx.pages = await api(`/api/docs/${id}/pages`); ctx.emit("pages"); return ctx.pages; },
  goto(index, tab) {
    if (!ctx.pages.length) return;
    ctx.current = Math.max(0, Math.min(ctx.pages.length - 1, index));
    ctx.emit("page");
    if (tab) showTab(tab);
  },
  get page() { return ctx.pages[ctx.current]; },
  async patchDoc(body) {
    ctx.doc = await api(`/api/docs/${id}`, { method: "PATCH", body });
    ctx.emit("doc");
    return ctx.doc;
  },
  busy() { return ["ingesting", "processing"].includes(ctx.doc?.status); },
};
window.__ctx = ctx; // handy for debugging in the console

// ------------------------------------------------------------------ header
const titleIn = h("input.title", { type: "text", spellcheck: false });
const authorIn = h("input.author", { type: "text", placeholder: "Author / editor", spellcheck: false });
const saveMeta = debounce(async () => {
  try { await ctx.patchDoc({ title: titleIn.value, author: authorIn.value }); document.title = `${ctx.doc.title} · Scriptorium OCR`; }
  catch (e) { toast(e.message, "error"); }
}, 600);
titleIn.oninput = saveMeta;
authorIn.oninput = saveMeta;
const statusEl = h("span");
const progEl = h("div.prog");
const actionsEl = h("div.row");

function renderHeader() {
  const d = ctx.doc;
  if (document.activeElement !== titleIn) titleIn.value = d.title;
  if (document.activeElement !== authorIn) authorIn.value = d.author || "";
  document.title = `${d.title} · Scriptorium OCR`;
  statusEl.replaceChildren(pill(d.status));
  const total = Math.max(0, d.page_count - (d.skipped || 0));
  let pct = 0, errPct = 0, label = "";
  if (d.status === "ingesting") { pct = d.page_count ? (100 * d.ingest_done) / d.page_count : 3; label = `Importing page ${d.ingest_done} of ${d.page_count || "?"}`; }
  else if (total) { pct = (100 * d.progress.done) / total; errPct = (100 * d.progress.error) / total; label = `${d.progress.done} of ${total} pages read` + (d.progress.error ? ` · ${d.progress.error} errors` : ""); }
  progEl.replaceChildren(h("div.progress", h("i", { style: { width: pct + "%" } }), errPct ? h("i.err", { style: { width: errPct + "%" } }) : null), h("div", { style: { marginTop: "4px" } }, label));
  const btns = [];
  if (d.status === "processing") btns.push(h("button.sm", { onclick: () => act("pause") }, "Pause"));
  if (d.status === "paused") btns.push(h("button.sm.primary", { onclick: () => act("resume") }, "Resume"), h("button.sm", { onclick: () => act("cancel") }, "Cancel queue"));
  if (d.sources.length === 1 && !d.images_purged) btns.push(h("a.btn.sm", { href: `/api/docs/${id}/source` }, "Original"));
  actionsEl.replaceChildren(...btns);
  if (d.status === "error" && d.error && !errShown) { errShown = true; toast(d.error, "error"); }
}
let errShown = false;

async function act(what) {
  try { await api(`/api/docs/${id}/${what}`, { method: "POST" }); await ctx.refreshDoc(); await ctx.refreshPages(); }
  catch (e) { toast(e.message, "error"); }
}

// ------------------------------------------------------------------ tabs
const TABS = [
  ["layout", "Layout & crop", layoutTab],
  ["process", "Process", processTab],
  ["review", "Review", reviewTab],
  ["export", "Export", exportTab],
];
const tabBar = h("nav.tabs");
const panes = {};
let activeTab = null;

function showTab(key) {
  if (activeTab === key) return;
  activeTab = key;
  [...tabBar.children].forEach((b) => b.classList.toggle("on", b.dataset.k === key));
  Object.entries(panes).forEach(([k, p]) => p.classList.toggle("on", k === key));
  const mod = TABS.find((t) => t[0] === key)[2];
  mod.show?.();
  history.replaceState(null, "", `#${key}`);
}
ctx.showTab = showTab;

// ------------------------------------------------------------------ boot
let pollTimer = null;
async function poll() {
  clearTimeout(pollTimer);
  try {
    const wasBusy = ctx.busy();
    await ctx.refreshDoc();
    if (ctx.busy() || wasBusy) await ctx.refreshPages();
  } catch (e) { /* transient */ }
  pollTimer = setTimeout(poll, ctx.busy() ? 2000 : 8000);
}

(async () => {
  try {
    [ctx.config, ctx.doc, ctx.pages] = await Promise.all([api("/api/config"), api(`/api/docs/${id}`), api(`/api/docs/${id}/pages`)]);
  } catch (e) {
    app.append(topbar(), h("div.empty", e.message));
    return;
  }
  const meter = storageMeter();
  app.append(
    topbar([h("a.btn.sm.ghost", { href: "/" }, "← Library"), meter]),
    h("div.dochead", h("div.titles", titleIn, authorIn), statusEl, progEl, actionsEl),
    tabBar,
  );
  TABS.forEach(([k, label, mod], i) => {
    tabBar.append(h("button", { "data-k": k, onclick: () => showTab(k) }, h("span.n", `${["I", "II", "III", "IV"][i]}.`), " ", label));
    panes[k] = h("section.tabpane");
    app.append(panes[k]);
    mod.init(ctx, panes[k]);
  });
  ctx.on("doc", renderHeader);
  renderHeader();
  ctx.emit("doc");
  ctx.emit("pages");
  const initial = location.hash.slice(1);
  const d = ctx.doc;
  showTab(TABS.some((t) => t[0] === initial) ? initial : ["review"].includes(d.status) ? "review" : d.status === "processing" || d.status === "paused" ? "process" : "layout");
  poll();

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea, select")) return;
    if (e.key === "ArrowRight" || e.key === "PageDown") { ctx.goto(ctx.current + 1); e.preventDefault(); }
    if (e.key === "ArrowLeft" || e.key === "PageUp") { ctx.goto(ctx.current - 1); e.preventDefault(); }
  });
})();
