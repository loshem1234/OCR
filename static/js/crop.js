// Tab I — Layout & crop.
// Two modes:
//  • Page by page (default): editing always gives the current page its own crop; pages without one
//    carry forward the nearest earlier page's crop (same rule the server uses at OCR time).
//  • Same for all pages: one all-pages layout (optionally odd/even), with per-page exceptions.
import { api, h, toast } from "./common.js";

let ctx, root;
let scope = "page";     // global | odd | even | page   (page-by-page mode always uses "page")
let tool = "main";      // main | notes
let zoom = 1;           // multiplier on fit-to-view
let working = null;     // the layout being edited for the current scope
let built = false;

const FULL = { x: 0, y: 0, w: 1, h: 1 };
const MIN = 0.02;
const clone = (o) => (o ? JSON.parse(JSON.stringify(o)) : o);
const defLayout = () => ({ main: null, notes: null, mode: "single", split: 0.5 });

// DOM
let rail, stage, sheet, img, overlay, side, pageInput, pageTotal, placeholder;

export function init(c, pane) {
  ctx = c; root = pane;
  placeholder = h("div.scrollpane", h("div.empty", "Importing pages…"));
  root.append(placeholder);
  ctx.on("doc", maybeBuild);
  ctx.on("pages", () => {
    if (!built) return maybeBuild();
    renderRail();
    if (!pending && !dragging) { loadWorking(); renderAll(); }
  });
  ctx.on("page", () => { if (!built) return; autoParity(); loadWorking(); renderAll(true); });
  window.addEventListener("resize", () => built && layoutSheet());
}

export function show() { if (built) { requestAnimationFrame(() => { layoutSheet(); scrollRail(); }); } }

function maybeBuild() {
  const d = ctx.doc;
  if (built) {
    if (!pending && !dragging && !(side.contains(document.activeElement) && document.activeElement.matches("input[type=text], input[type=number], select"))) renderSide();
    return;
  }
  if (d.status === "ingesting" || !ctx.pages.length) {
    placeholder.replaceChildren(h("div.empty", d.status === "error" ? d.error || "Import failed." : `Importing pages… ${d.ingest_done} of ${d.page_count || "?"}`));
    return;
  }
  build();
}

function build() {
  built = true;
  scope = perPage() ? "page" : "global";
  placeholder.remove();
  rail = h("div.rail");
  pageInput = h("input", { type: "text" });
  pageInput.onchange = () => ctx.goto(parseInt(pageInput.value, 10) - 1);
  pageTotal = h("span");
  img = h("img", { alt: "", draggable: false });
  img.onload = layoutSheet;
  overlay = h("div.overlay");
  sheet = h("div.sheet", img, overlay);
  stage = h("div.stage", sheet);
  const zoomBtns = h("div.seg",
    h("button", { title: "Zoom out", onclick: () => setZoom(zoom / 1.25) }, "−"),
    h("button", { title: "Fit", onclick: () => setZoom(1) }, "Fit"),
    h("button", { title: "Zoom in", onclick: () => setZoom(zoom * 1.25) }, "+"));
  const bar = h("div.stagebar",
    h("div.pageno",
      h("button.icon.sm", { onclick: () => ctx.step(-1), title: "Previous page (←) — skips skipped pages" }, "◀"),
      "Page", pageInput, pageTotal,
      h("button.icon.sm", { onclick: () => ctx.step(1), title: "Next page (→) — skips skipped pages" }, "▶")),
    h("div", { style: { flex: 1 } }),
    h("span.hint", "Drag to draw · drag a box to move · handles resize · Shift-drag redraws · ← → pages"),
    zoomBtns);
  side = h("div.sidepanel");
  root.append(rail, h("div.stagewrap", bar, stage), side);
  bindPointer();
  renderRail();
  loadWorking();
  renderAll(true);
}

function setZoom(z) { zoom = Math.max(0.4, Math.min(5, z)); layoutSheet(); }

// ------------------------------------------------------------------ layout resolution (mirrors app/layout.py)
const perPage = () => ctx.doc.layouts.carry_forward !== false;

function baseFor(index) {
  const L = ctx.doc.layouts;
  if (L.use_odd_even) {
    const k = (index + 1) % 2 === 1 ? "odd" : "even";
    if (L[k]) return { layout: L[k], source: k, from: 0 };
  }
  return { layout: L.global, source: "global", from: 0 };
}

function resolveFor(p) {
  if (p.override) return { layout: p.override, source: "page", from: p.number };
  const L = ctx.doc.layouts;
  if (perPage()) {
    for (let i = p.index - 1; i >= 0; i--) {
      const q = ctx.pages[i];
      if (L.use_odd_even && q.index % 2 !== p.index % 2) continue;
      if (q.override) return { layout: q.override, source: "inherited", from: q.number };
    }
  }
  return baseFor(p.index);
}

// All pages in one pass (for the thumbnail outlines).
function resolveAll() {
  const L = ctx.doc.layouts;
  const last = { any: null, 0: null, 1: null };
  return ctx.pages.map((p) => {
    if (p.override) { last.any = last[p.index % 2] = p.override; return p.override; }
    const prev = perPage() ? (L.use_odd_even ? last[p.index % 2] : last.any) : null;
    return prev || baseFor(p.index).layout;
  });
}

function autoParity() {
  if (perPage()) scope = "page";
  else if (scope === "odd" || scope === "even") scope = (ctx.current + 1) % 2 === 1 ? "odd" : "even";
}

function loadWorking() {
  const L = ctx.doc.layouts;
  const p = ctx.page;
  if (!p) return;
  if (perPage()) scope = "page";
  if (scope === "global") working = clone(L.global) || defLayout();
  else if (scope === "odd" || scope === "even") working = clone(L[scope] || L.global) || defLayout();
  else working = clone(p.override || resolveFor(p).layout) || defLayout();
}

// ------------------------------------------------------------------ saving
let saveTimer = null;
let pending = false;
let dragging = false;
function save() {
  clearTimeout(saveTimer);
  pending = true;
  const snapshot = clone(working), sc = scope, page = ctx.page;
  saveTimer = setTimeout(async () => {
    pending = false;
    try {
      if (sc === "page") {
        const res = await api(`/api/pages/${page.id}`, { method: "PATCH", body: { override: snapshot } });
        Object.assign(page, res);
      } else {
        const body = { layouts: { [sc]: snapshot } };
        if (sc !== "global") body.layouts.use_odd_even = true;
        await ctx.patchDoc(body);
      }
      renderSide();
    } catch (e) { toast(e.message, "error"); }
  }, 250);
  // Optimistic: the next page should inherit this crop even before the save round-trips.
  if (sc === "page") { page.override = snapshot; renderRail(); }
}

// ------------------------------------------------------------------ rendering
function renderAll(newPage = false) {
  const p = ctx.page;
  if (!p) return;
  pageInput.value = p.number;
  pageTotal.textContent = `of ${ctx.pages.length}`;
  if (newPage || !img.src) {
    const src = `/api/pages/${p.id}/image`;
    if (!img.src.endsWith(src)) img.src = src;
  }
  [...rail.children].forEach((t, i) => t.classList.toggle("on", i === ctx.current));
  scrollRail();
  layoutSheet();
  renderSide();
}

function scrollRail() {
  const t = rail?.children[ctx.current];
  if (t) t.scrollIntoView({ block: "nearest" });
}

const thumbCache = new Map(); // page id -> <img>, so re-rendering the rail never re-requests thumbnails

function renderRail() {
  const eff = resolveAll();
  const keepScroll = rail.scrollTop;
  const frag = ctx.pages.map((p, i) => {
    const badges = [];
    if (p.override) badges.push(h("span.badge.blue", { title: "Has its own crop" }, "OWN"));
    if (p.skip) badges.push(h("span.badge.red", "SKIP"));
    if (p.has_text_layer) badges.push(h("span.badge", { title: "PDF text layer present" }, "TXT"));
    let im = thumbCache.get(p.id);
    if (!im) { im = h("img", { src: `/api/pages/${p.id}/thumb`, loading: "lazy", alt: "" }); thumbCache.set(p.id, im); }
    const m = eff[i]?.main;
    const crop = m ? h("div.crop", { style: { left: pct(m.x), top: pct(m.y), width: pct(m.w), height: pct(m.h) } }) : null;
    return h(`div.t${i === ctx.current ? ".on" : ""}${p.skip ? ".skip" : ""}`, { onclick: () => ctx.goto(i) },
      h("div.tw", im, crop), h("div.bd", badges), h("div.no", p.number));
  });
  rail.replaceChildren(...frag);
  rail.scrollTop = keepScroll;
}

function layoutSheet() {
  const p = ctx.page;
  if (!p || !stage) return;
  const sw = stage.clientWidth - 36, sh = stage.clientHeight - 36;
  const fit = Math.min(sw / p.width, sh / p.height);
  const s = fit * zoom;
  sheet.style.width = `${Math.round(p.width * s)}px`;
  sheet.style.height = `${Math.round(p.height * s)}px`;
  renderBoxes();
}

function pct(v) { return `${(v * 100).toFixed(3)}%`; }

function boxEl(kind, b) {
  const active = tool === kind;
  const el = h(`div.box.${kind}${active ? "" : ".inactive"}`, { "data-box": kind },
    h(`span.tag${kind === "notes" && b.y < 0.06 ? "" : ""}${kind === "notes" ? ".below" : ""}`, kind === "main" ? "Main text" : "Footnotes"));
  Object.assign(el.style, { left: pct(b.x), top: pct(b.y), width: pct(b.w), height: pct(b.h) });
  if (active) for (const hd of ["nw", "n", "ne", "e", "se", "s", "sw", "w"]) el.append(h("div.handle", { "data-handle": hd, "data-box": kind }));
  return el;
}

function renderBoxes() {
  if (!working) return;
  const els = [];
  const main = working.main || FULL;
  els.push(boxEl("main", main));
  if (working.mode !== "single") {
    const x = main.x + main.w * working.split;
    els.push(h("div.divider", { "data-divider": "1", style: { left: pct(x), top: pct(main.y), height: pct(main.h), bottom: "auto" } },
      h("span", working.mode === "facing" ? "parallel text split" : "column split")));
  }
  if (working.notes) els.push(boxEl("notes", working.notes));
  overlay.replaceChildren(...els);
}

// ------------------------------------------------------------------ pointer interaction
function bindPointer() {
  let action = null;
  const pt = (e) => {
    const r = overlay.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  };
  overlay.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const p = pt(e), t = e.target;
    const boxKind = t.dataset.box;
    if (t.dataset.handle && boxKind === tool) action = { type: "resize", box: tool, handle: t.dataset.handle, orig: { ...(working[tool] || FULL) } };
    else if (t.dataset.divider) action = { type: "split" };
    // A box that still covers the whole page can't be moved, so dragging on it draws a new one. Shift always draws.
    else if (boxKind === tool && t.classList.contains("box") && working[tool] && !e.shiftKey) action = { type: "move", box: tool, start: p, orig: { ...working[tool] } };
    else action = { type: "draw", box: tool, start: p, moved: false };
    dragging = true;
    overlay.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  overlay.addEventListener("pointermove", (e) => {
    if (!action) return;
    const p = pt(e);
    if (action.type === "draw") {
      const s = action.start;
      if (Math.abs(p.x - s.x) < 0.005 && Math.abs(p.y - s.y) < 0.005 && !action.moved) return;
      action.moved = true;
      working[action.box] = { x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) };
    } else if (action.type === "move") {
      const o = action.orig;
      const x = Math.max(0, Math.min(1 - o.w, o.x + p.x - action.start.x));
      const y = Math.max(0, Math.min(1 - o.h, o.y + p.y - action.start.y));
      working[action.box] = { ...o, x, y };
    } else if (action.type === "resize") {
      const o = action.orig;
      let l = o.x, t = o.y, r = o.x + o.w, b = o.y + o.h;
      const hd = action.handle;
      if (hd.includes("w")) l = Math.min(p.x, r - MIN);
      if (hd.includes("e")) r = Math.max(p.x, l + MIN);
      if (hd.includes("n")) t = Math.min(p.y, b - MIN);
      if (hd.includes("s")) b = Math.max(p.y, t + MIN);
      working[action.box] = { x: l, y: t, w: r - l, h: b - t };
    } else if (action.type === "split") {
      const m = working.main || FULL;
      working.split = Math.max(0.05, Math.min(0.95, (p.x - m.x) / m.w));
    }
    renderBoxes();
  });
  const end = () => {
    if (!action) return;
    const a = action;
    action = null;
    dragging = false;
    if (a.type === "draw" && !a.moved) return;
    const b = working[a.box];
    if (b && (b.w < MIN || b.h < MIN)) working[a.box] = a.type === "draw" ? null : a.orig;
    renderBoxes();
    save();
  };
  overlay.addEventListener("pointerup", end);
  overlay.addEventListener("pointercancel", end);
}

// ------------------------------------------------------------------ side panel
function seg(options, value, onPick, vertical = false) {
  const el = h(`div.seg${vertical ? ".vertical" : ""}`);
  for (const [v, label, title] of options) {
    el.append(h(`button${v === value ? ".on" : ""}`, { type: "button", title, onclick: () => onPick(v) }, label));
  }
  return el;
}

function renderSide() {
  if (!side || !ctx.page || !working) return;
  const p = ctx.page;
  const L = ctx.doc.layouts;
  const parity = (p.index + 1) % 2 === 1 ? "odd" : "even";
  const res = resolveFor(p);
  const pp = perPage();
  const srcName = { global: "the all-pages layout", odd: "the odd-page layout", even: "the even-page layout", page: "its own layout",
    inherited: `page ${res.from}'s crop` }[res.source];

  const modeSeg = seg([["page", "Page by page", "Each page keeps its own crop; new pages start from the last one you set"],
    ["all", "Same for all pages", "One crop for every page (odd/even optional)"]], pp ? "page" : "all", async (v) => {
    if ((v === "page") === pp) return;
    await ctx.patchDoc({ layouts: { carry_forward: v === "page" } });
    scope = v === "page" ? "page" : "global";
    loadWorking(); renderRail(); renderAll();
  });

  const scopeOpts = [["global", "All pages", "Edit the layout every page uses by default"]];
  if (L.use_odd_even) scopeOpts.push([parity, parity === "odd" ? "Odd pages" : "Even pages", `Edit the layout for ${parity} pages`]);
  scopeOpts.push(["page", `Page ${p.number} only`, "Give this page its own layout"]);

  const oddEven = h("input", { type: "checkbox", checked: L.use_odd_even });
  oddEven.onchange = async () => {
    const body = { layouts: { use_odd_even: oddEven.checked } };
    if (pp) { await ctx.patchDoc(body); loadWorking(); renderRail(); renderAll(); return; }
    if (oddEven.checked) {
      if (!L.odd) body.layouts.odd = clone(L.global);
      if (!L.even) body.layouts.even = clone(L.global);
    }
    await ctx.patchDoc(body);
    if (oddEven.checked) scope = parity; else if (scope !== "page") scope = "global";
    loadWorking(); renderAll();
  };

  let notice;
  if (pp) {
    const msg = res.source === "page"
      ? ["Page ", p.number, " has ", h("b", "its own crop"), ". Later pages without their own crop follow it."]
      : res.source === "inherited"
        ? ["Page ", p.number, " is using ", h("b", srcName), ". Adjust the box to give this page its own — or just move on with ", h("kbd", "→"), "."]
        : ["No earlier page has a crop yet. Draw one here and the pages after it will start from it."];
    notice = h(`div.source-note${res.source === "page" ? ".override" : ""}`, ...msg,
      res.source === "page" && p.index > 0 ? h("div", { style: { marginTop: "8px" } }, h("button.sm", { onclick: clearOverride }, "Reset to previous page's crop")) : null);
  } else {
    notice = h(`div.source-note${res.source === "page" ? ".override" : ""}`,
      "This page uses ", h("b", srcName), ".",
      res.source === "page" && scope !== "page" ? h("div", { style: { marginTop: "6px" } }, "Changes to other layouts won't affect it until you clear its own layout.") : null,
      res.source === "page" ? h("div", { style: { marginTop: "8px" } }, h("button.sm", { onclick: clearOverride }, "Clear page layout")) : null);
  }

  const skip = h("input", { type: "checkbox", checked: p.skip });
  skip.onchange = async () => {
    try { Object.assign(p, await api(`/api/pages/${p.id}`, { method: "PATCH", body: { skip: skip.checked } })); renderRail(); }
    catch (e) { toast(e.message, "error"); }
  };

  const ra = h("input", { type: "text", value: String(p.number) });
  const rb = h("input", { type: "text", value: String(Math.min(ctx.pages.length, p.number + 9)) });
  const rangeAct = async (body, msg) => {
    try {
      const r = await api(`/api/docs/${ctx.id}/pages/range`, { method: "POST", body: { start: +ra.value, end: +rb.value, ...body } });
      toast(`${msg} on ${r.updated} page(s).`, "ok");
      await ctx.refreshPages();
    } catch (e) { toast(e.message, "error"); }
  };

  side.replaceChildren(...[
    h("section", h("span.label", "Crop mode"), modeSeg,
      pp ? h("label.check", { style: { marginTop: "10px" } }, oddEven, h("span", "Carry odd & even pages separately", h("div.hint", "For book scans where left and right pages have different margins: each page starts from the last crop of the same side.")))
         : null),
    pp ? null : h("section", h("span.label", "Editing layout for"), seg(scopeOpts, scope, (v) => { scope = v; loadWorking(); renderAll(); }, true),
      h("label.check", { style: { marginTop: "10px" } }, oddEven, h("span", "Different layouts for odd & even pages", h("div.hint", "For book scans where the margins alternate.")))),
    h("section", notice),
    h("section", h("span.label", "Draw"),
      seg([["main", "Main text box", "The region to read"], ["notes", "Footnote box", "Optional region read separately as notes"]], tool, (v) => { tool = v; renderBoxes(); renderSide(); }),
      h("div.hint", { style: { marginTop: "6px" } }, tool === "main"
        ? "Everything outside the gold box (margins, running heads, page numbers) is ignored."
        : ctx.doc.settings.footnote_mode === "separate"
          ? "Text in the blue box is read into the page's notes. Where it overlaps the main box, it is cut out of the main text."
          : "Footnotes are set to be dropped (Process tab), so this box will be ignored."),
      h("div.row", { style: { marginTop: "8px" } },
        h("button.sm", { onclick: () => { working.main = null; renderBoxes(); save(); } }, "Full page"),
        working.notes ? h("button.sm", { onclick: () => { working.notes = null; renderBoxes(); save(); } }, "Remove footnote box") : null)),
    h("section", h("span.label", "Text layout"),
      seg([["single", "Single", "One column of text"], ["columns", "2 columns", "Read the left column, then the right"], ["facing", "Parallel", "Facing texts (e.g. Greek | English) kept side by side"]],
        working.mode, (v) => { working.mode = v; renderBoxes(); renderSide(); save(); }),
      working.mode !== "single" ? h("div.hint", { style: { marginTop: "6px" } }, "Drag the dashed line to where the columns divide.") : null),
    h("section", h("span.label", `Page ${p.number}`),
      h("label.check", skip, h("span", "Skip this page", h("div.hint", "Blank pages, plates and adverts. Skipped pages aren't read or exported.")))),
    h("section", h("span.label", "Page range"),
      h("div.hint", { style: { marginBottom: "8px" } }, "Reading: ", ctx.rangeSummary()),
      h("div.range", "Pages", ra, "to", rb),
      h("button.sm.primary", { style: { marginTop: "8px", width: "100%", justifyContent: "center" },
        title: "Skip every page outside this range and include every page inside it",
        onclick: async () => {
          try { const n = await ctx.isolate(+ra.value, +rb.value); toast(`Keeping ${n} pages; everything else is skipped.`, "ok"); }
          catch (e) { toast(e.message, "error"); }
        } }, "Keep only these pages"),
      h("div.row", { style: { marginTop: "8px" } },
        h("button.sm", { title: "Copy the crop shown to every page in the range as its own crop", onclick: () => rangeAct({ layout: clone(working) }, "Crop applied") }, "Apply shown crop"),
        h("button.sm", { onclick: () => rangeAct({ layout: null }, "Own crops cleared") }, "Clear own crops"),
        h("button.sm", { onclick: () => rangeAct({ skip: true }, "Skipped") }, "Skip"),
        h("button.sm", { onclick: () => rangeAct({ skip: false }, "Un-skipped") }, "Un-skip"),
        ctx.pages.some((x) => x.skip) ? h("button.sm", { title: "Un-skip every page", onclick: async () => {
          try { await ctx.isolate(1, ctx.pages.length); toast("All pages included.", "ok"); } catch (e) { toast(e.message, "error"); }
        } }, "Include all") : null)),
    h("hr"),
    h("button.primary", { style: { width: "100%", justifyContent: "center" }, onclick: () => ctx.showTab("process") }, "Continue to Process →"),
  ].filter(Boolean));
}

async function clearOverride() {
  const p = ctx.page;
  try {
    Object.assign(p, await api(`/api/pages/${p.id}`, { method: "PATCH", body: { clear_override: true } }));
    p.override = null;
    if (scope === "page" && !perPage()) scope = ctx.doc.layouts.use_odd_even ? ((p.index + 1) % 2 ? "odd" : "even") : "global";
    loadWorking(); renderRail(); renderAll();
  } catch (e) { toast(e.message, "error"); }
}
