// UI-29's harness: how many times does `paint` actually touch the DOM?
//
// Loads the REAL township-v2.js `paint` out of the shipped file (not a
// copy) and drives it, counting innerHTML assignments. Grepping the file
// for "paint(" would pass while every renderer still wrote on every frame.
//
// Prints one JSON line: {"writes": N, "returns": [...]}.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = path.join(
  __dirname, "..", "static", "js", "township", "township-v2.js"
);
const text = fs.readFileSync(SOURCE, "utf8");

// Take `paint` alone. The rest of township-v2.js expects a whole browser,
// a canvas renderer and a live world; lifting the one function under test
// out by its own declaration is the honest way to run it here, and the
// pytest file next door checks that the renderers really call it.
const start = text.indexOf("function paint(el, html) {");
if (start === -1) {
  console.error("UI-29: township-v2.js has no paint() at all");
  process.exit(2);
}
const end = text.indexOf("\n}", start) + 2;
const paintSource = text.slice(start, end);

let writes = 0;
const el = {
  _painted: undefined,
  set innerHTML(v) {
    writes += 1;
    this._html = v;
  },
  get innerHTML() {
    return this._html;
  },
};

const sandbox = { el, console };
vm.createContext(sandbox);
vm.runInContext(paintSource, sandbox);

const returns = [];
// Ten frames of an unchanged panel - what the 400 ms loop does when
// nothing in the town has moved.
for (let i = 0; i < 10; i++) returns.push(vm.runInContext("paint(el, '<div>same</div>')", sandbox));
// Then something changes.
returns.push(vm.runInContext("paint(el, '<div>different</div>')", sandbox));
// And settles again.
for (let i = 0; i < 5; i++) returns.push(vm.runInContext("paint(el, '<div>different</div>')", sandbox));

process.stdout.write(JSON.stringify({ writes, returns, html: el.innerHTML }));
