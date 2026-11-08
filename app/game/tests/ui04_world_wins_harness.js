const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = path.join(
  __dirname, "..", "static", "js", "township", "township-v2.js"
);
const text = fs.readFileSync(SOURCE, "utf8");

function lift(name) {
  const decl = "function " + name + "(";
  const start = text.indexOf(decl);
  if (start === -1) throw new Error("UI-04: township-v2.js has no " + name);
  let i = text.indexOf("{", start), depth = 0;
  let inS = null, inLine = false, inBlock = false, esc = false;
  for (; i < text.length; i++) {
    const c = text[i], n = text[i + 1];
    if (inLine) { if (c === "\n") inLine = false; continue; }
    if (inBlock) { if (c === "*" && n === "/") { inBlock = false; i++; } continue; }
    if (inS) {
      if (esc) { esc = false; continue; }
      if (c === "\\") { esc = true; continue; }
      if (c === inS) inS = null;
      continue;
    }
    if (c === "/" && n === "/") { inLine = true; i++; continue; }
    if (c === "/" && n === "*") { inBlock = true; i++; continue; }
    if (c === "'" || c === '"' || c === "`") { inS = c; continue; }
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return text.slice(start, i + 1); }
  }
  throw new Error("UI-04: unterminated " + name);
}

const LIFTED = [
  "isBound", "worldIsBound", "seatHolders", "seatBook", "seatRoleOf",
  "titleOf", "balanceNow", "costSpent", "stepBirths", "liveSkillLine",
  "liveSkillChips",
].map(lift).join("\n\n");

function agent(id, occ) {
  return {
    id: id, n: "Resident " + id, occ: occ, sex: "f", age: 30, br: "working",
    ms: "married", sp: "Resident a0002", bal: 500, usd: 200, wage: 125,
    upkeep: 55, dayZone: "tradeoffices", skillBase: "General Ability",
    skillUnlocked: "Policing", skillUnlockDay: 3, rel: [], _costDecay: 40,
  };
}

const scaffold = `
  var AGENTS = [agent("a0001", "government_clerk"), agent("a0002", "day_laborer")];
  var AGENT_BY_ID = Object.create(null);
  AGENTS.forEach(function (a) { AGENT_BY_ID[a.id] = a; });
  var PM_SALARY_SIM = 300, MINISTER_SALARY_SIM = 200, HEAD_TAX_PER_DAY = 30;
  var BIRTH_MIN_AGE = 18, BIRTH_MAX_AGE = 45, DAILY_BIRTH_CHANCE = 1.0;
  var OCC_LABEL = function (o) { return o.replace(/_/g, " "); };
  function hash(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return h; }
  function seeded01() { return 0; }         // every roll succeeds
  function isAlive() { return true; }
  function esc(s) { return String(s); }
  function balanceOn() { return 999999; }   // the MODEL's answer, unmistakable
  var madeNewborns = 0;
  function makeNewborn() { madeNewborns++; }
`;

const sandbox = { console, agent };
vm.createContext(sandbox);
vm.runInContext(scaffold + "\n" + LIFTED, sandbox);

function run(liveJson) {
  vm.runInContext("var LIVE = " + JSON.stringify(liveJson) + ";", sandbox);
  vm.runInContext("madeNewborns = 0; _seatBook = null; _seatBookFor = -1; _seatBookGov = undefined;", sandbox);
  vm.runInContext("stepBirths(1);", sandbox);
  return {
    seats: vm.runInContext(
      "seatHolders().map(function (s) { return s.role + ':' + s.agent.id; })", sandbox),
    boundTitle: vm.runInContext("titleOf(AGENT_BY_ID.a0001)", sandbox),
    unboundBalance: vm.runInContext("balanceNow(AGENT_BY_ID.a0002, 24)", sandbox),
    boundBalance: vm.runInContext("balanceNow(AGENT_BY_ID.a0001, 24)", sandbox),
    boundSpend: vm.runInContext("costSpent(AGENT_BY_ID.a0001, 24)", sandbox),
    skillLine: vm.runInContext("liveSkillLine('a0001')", sandbox),
    newborns: vm.runInContext("madeNewborns", sandbox),
  };
}

const BOUND = {
  connected: true, bound: 1, enabled: true,
  zone: { a0001: "government" },
  balance: { a0001: 0 },
  usdCents: { a0001: 40 },
  skills: { a0001: [{ name: "Baking", level: 2 }] },
  government: { prime_minister: { agent_id: "a0002", name: "Resident a0002" },
                ministers: [], terms_served: {} },
};
const BOUND_NO_GOVERNMENT = Object.assign({}, BOUND, { government: null });
const UNBOUND = { connected: false, bound: 0, enabled: true, zone: {},
                  balance: {}, usdCents: {}, skills: {}, government: null };

process.stdout.write(JSON.stringify({
  bound: run(BOUND),
  boundNoGovernment: run(BOUND_NO_GOVERNMENT),
  unbound: run(UNBOUND),
}));
