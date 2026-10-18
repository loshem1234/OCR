// Shared helpers for every page.

export async function api(path, opts = {}) {
  const init = { credentials: "same-origin", ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.body !== undefined && !(opts.body instanceof FormData) && typeof opts.body !== "string") {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (r.status === 401 && !path.endsWith("/login")) {
    location.href = "/login";
    throw new Error("Signed out");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r;
}

// Tiny element builder: h("div.card#x", {onclick}, "text", child)
export function h(spec, attrs, ...children) {
  const m = spec.match(/^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i);
  const el = document.createElement(m[1] || "div");
  (m[2].match(/[.#][\w-]+/g) || []).forEach((p) => (p[0] === "." ? el.classList.add(p.slice(1)) : (el.id = p.slice(1))));
  if (attrs !== undefined && (attrs === null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function toast(msg, kind = "") {
  let box = document.getElementById("toasts");
  if (!box) { box = h("div#toasts"); document.body.append(box); }
  const t = h(`div.toast${kind ? "." + kind : ""}`, msg);
  box.append(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3500);
}

export function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i >= 2 ? 1 : 0)} ${u[i]}`;
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function debounce(fn, ms = 400) {
  let t;
  const wrapped = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  wrapped.flush = (...a) => { clearTimeout(t); return fn(...a); };
  return wrapped;
}

export const STATUS = {
  ingesting: "Importing", ready: "Ready to crop", processing: "Reading", paused: "Paused",
  review: "Ready for review", error: "Error", new: "Not read", queued: "Queued", running: "Reading", done: "Done",
};

export function pill(status) {
  return h(`span.pill.${status}`, STATUS[status] || status);
}

export const ENGINE_LABEL = { tesseract: "Tesseract", gemini: "Gemini", embedded: "Text layer", manual: "Manual" };

export const EMBLEM = `<svg viewBox="0 0 64 64" fill="none" stroke="#c9a45c" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="32" cy="32" r="29" stroke-width="2" opacity=".55"/><path d="M12 21c6-2.5 13-2.5 18 1.5V46c-5-4-12-4-18-1.5z"/><path d="M52 21c-6-2.5-13-2.5-18 1.5V46c5-4 12-4 18-1.5z" opacity=".55"/><path d="M16 27.5c3.5-1 7-1 10 .5M16 33c3.5-1 7-1 10 .5M16 38.5c3.5-1 7-1 10 .5" stroke-width="1.8"/><circle cx="43" cy="33" r="6.5" fill="#0d0b08"/><path d="M47.8 37.8 53 43"/></svg>`;

export function topbar(extra = []) {
  const bar = h("header.topbar",
    h("a.brand", { href: "/", html: EMBLEM + '<span class="name">Scriptorium <em>OCR</em></span>' }),
    h("div.spacer"),
    ...extra,
    h("button.ghost.sm", { onclick: async () => { await api("/api/logout", { method: "POST" }); location.href = "/login"; } }, "Sign out"),
  );
  return bar;
}

export function storageMeter() {
  const el = h("div.meter", { title: "Storage on the data volume" }, h("span", "Storage"), h("div.bar", h("i")), h("span.v", "…"));
  el.refresh = async () => {
    try {
      const s = await api("/api/storage");
      el.querySelector("i").style.width = Math.min(100, s.percent) + "%";
      el.querySelector(".v").textContent = `${fmtBytes(s.used_bytes)} / ${fmtBytes(s.capacity_bytes)}`;
      el.classList.toggle("warn", !!s.warning);
      el.data = s;
      return s;
    } catch { return null; }
  };
  el.refresh();
  return el;
}

export function langChips(config, selected, onChange) {
  const box = h("div.chips");
  const sel = new Set(selected);
  for (const l of config.languages) {
    const c = h(`span.chip${sel.has(l.code) ? ".on" : ""}${l.installed || config.gemini_enabled ? "" : ".off"}`,
      { title: l.installed ? l.label : `${l.label} (Tesseract model not installed — Gemini only)` }, l.label);
    c.onclick = () => {
      if (!l.installed && !config.gemini_enabled) return;
      if (sel.has(l.code)) { if (sel.size > 1) sel.delete(l.code); } else sel.add(l.code);
      c.classList.toggle("on", sel.has(l.code));
      onChange([...sel]);
    };
    box.append(c);
  }
  box.value = () => [...sel];
  return box;
}

export const ENGINES = [
  ["auto", "Hybrid (recommended)", "Tesseract first; Gemini for Greek, Hebrew, Arabic, Syriac and low-confidence pages."],
  ["tesseract", "Tesseract only", "Free and local. Weak on polytonic Greek and complex layouts."],
  ["gemini", "Gemini only", "Most accurate on everything. Every page is billed."],
  ["embedded", "PDF text layer only", "For born-digital PDFs: reads the existing text inside your crop boxes."],
];
