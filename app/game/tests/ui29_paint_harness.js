const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = path.join(
  __dirname, "..", "static", "js", "township", "township-v2.js"
);
const text = fs.readFileSync(SOURCE, "utf8");

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
for (let i = 0; i < 10; i++) returns.push(vm.runInContext("paint(el, '<div>same</div>')", sandbox));
returns.push(vm.runInContext("paint(el, '<div>different</div>')", sandbox));
for (let i = 0; i < 5; i++) returns.push(vm.runInContext("paint(el, '<div>different</div>')", sandbox));

process.stdout.write(JSON.stringify({ writes, returns, html: el.innerHTML }));
