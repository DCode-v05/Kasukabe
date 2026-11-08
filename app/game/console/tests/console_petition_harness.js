// UI-31's harness: runs the REAL renderPetition out of console.js against
// a DOM small enough to fit in one file, and prints what the console sent.
//
// Parsing console.js for the string "confirmable(" would pass forever
// while the button fired on one click - this project has shipped exactly
// that kind of test twice (CORE-23, EVAL-21), so this clicks the button
// and watches the network instead.
//
// Reads a JSON plan on argv[2]: {"clicks": N, "arenas": "a, b"} and prints
// one JSON line: {"sent": [...], "label": "<button text now>"}.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const plan = JSON.parse(process.argv[2]);
const SOURCE = path.join(__dirname, "..", "static", "console.js");

// ── the smallest DOM that renderPetition actually touches ──────────────

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attrs = {};
    this._text = "";
    this._html = "";
    this.value = "";
    this.listeners = {};
    this.classList = {
      _set: new Set(),
      add: (c) => this.classList._set.add(c),
      remove: (c) => this.classList._set.delete(c),
      contains: (c) => this.classList._set.has(c),
    };
  }
  get textContent() {
    return this._text;
  }
  set textContent(v) {
    this._text = String(v === null || v === undefined ? "" : v);
    this._html = this._text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  get innerHTML() {
    return this._html;
  }
  set innerHTML(v) {
    this._html = String(v);
    this.children = parse(String(v));
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  contains() {
    return true;
  }
  removeChild() {}
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  async click() {
    for (const fn of this.listeners.click || []) await fn();
  }
  querySelector(sel) {
    const m = /^\[([\w-]+)="([^"]*)"\]$/.exec(sel);
    if (!m) throw new Error("harness: unsupported selector " + sel);
    const [, attr, want] = m;
    const walk = (node) => {
      for (const c of node.children) {
        if (c.attrs[attr] === want) return c;
        const hit = walk(c);
        if (hit) return hit;
      }
      return null;
    };
    const found = walk(this);
    if (!found) throw new Error("harness: nothing matches " + sel);
    return found;
  }
}

// A deliberately dumb tag scanner: enough for the flat markup
// renderPetition emits, and it fails loudly rather than guessing.
function parse(html) {
  const out = [];
  const tag = /<(input|select|button|div|span)\b([^>]*?)(\/?)>/g;
  let m;
  while ((m = tag.exec(html)) !== null) {
    const node = new Node(m[1]);
    const attr = /([\w-]+)="([^"]*)"/g;
    let a;
    while ((a = attr.exec(m[2])) !== null) {
      node.attrs[a[1]] = a[2];
      if (a[1] === "value") node.value = a[2];
    }
    // The button's resting label, which `confirmable` has to preserve.
    if (m[1] === "button") {
      const close = html.indexOf("</button>", tag.lastIndex);
      node.textContent = close === -1 ? "" : html.slice(tag.lastIndex, close);
    }
    out.push(node);
  }
  return out;
}

const sent = [];
const sandbox = {
  console,
  JSON,
  Object,
  String,
  Number,
  parseInt,
  parseFloat,
  Promise,
  Set,
  Map,
  Date,
  Math,
  encodeURIComponent,
  fetch: async (url, opts) => {
    sent.push({ url, body: JSON.parse(opts.body) });
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({}), text: async () => "{}" };
  },
  sessionStorage: { getItem: () => "t", setItem: () => {} },
  document: {
    createElement: (tag) => new Node(tag),
    getElementById: () => new Node("div"),
    addEventListener: () => {},
    querySelectorAll: () => [],
  },
  window: {
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (t) => clearTimeout(t),
    setInterval: () => {},
    location: { reload: () => {} },
  },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

let source = fs.readFileSync(SOURCE, "utf8");
// The page's own bootstrap must not run: this harness is testing one
// renderer, not the console's boot.
source = source.replace('document.addEventListener("DOMContentLoaded", init);', "");
vm.runInContext(source + "\n;globalThis.__renderPetition = renderPetition;", sandbox);

// `listItem` and `refreshPresident` reach for page furniture this harness
// has no reason to build; both are replaced with the smallest thing that
// keeps the renderer honest.
vm.runInContext(
  `listItem = function (html) { const d = document.createElement("div"); d.innerHTML = html; return d; };
   refreshPresident = async function () {};
   showBanner = function () {};`,
  sandbox
);

(async () => {
  const item = sandbox.__renderPetition({
    petition_id: "pet-1",
    title: "South strip",
    status: "pending",
    filed_by_agent_id: "a-bram",
    tile_count_requested: 3,
    description: "we need room",
  });
  const approve = item.querySelector('[data-action="approve"]');
  if (plan.arenas !== undefined) item.querySelector('[data-role="arenas"]').value = plan.arenas;
  for (let i = 0; i < plan.clicks; i++) await approve.click();
  process.stdout.write(JSON.stringify({ sent, label: approve.textContent, armed: approve.classList.contains("armed") }));
  process.exit(0);
})();
