/* Kasukabe township viewer: simulation clock, resident behaviour, live
 * conversation, the town economy, and every panel on the page.
 *
 * Rendering lives in village.js (isometric canvas). This file owns what the
 * residents DO: where each of the 250 is at any hour, who talks to whom,
 * what each one earns and owes, and what the panels show.
 *
 * Honesty line, repeated on the page itself: population, families,
 * occupations, personas, balances and the fiscal constants are real, read
 * from world/genesis.json and world/ledger/*. The daily schedule, the
 * conversations, and the spend ticker are a deterministic client-side model -
 * a visualisation of the environment, not a socket into the Python engine.
 */

const OCC_LABEL = o => o.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

// ---- real fiscal constants (world/ledger/*, world/life/constants.py) ----
const HEAD_TAX_BASE_SIM = 1000;   // world/ledger/head_tax.py
const TAX_RATE_BPS = 300;         // illustrative; genesis default 0, government amends
const HEAD_TAX_PER_DAY = HEAD_TAX_BASE_SIM * TAX_RATE_BPS / 10000;
const GENESIS_TREASURY_USD = 500; // world/life/constants.py
const POP = AGENTS.length;   // 250 genesis residents + the institution staff

// Daily sim-currency wage by occupation, and daily household upkeep. DESIGN.md
// section 3's "everything costs: living is not free" - these are the numbers
// that make a resident's balance move day to day.
const WAGE = {
  construction_company_owner: 220, grocery_store_owner: 200, cafe_owner: 195,
  pharmacist: 185, college_instructor: 170, office_administrator: 150,
  government_clerk: 145, trader: 140, artisan: 135, miner: 130, farmer: 125,
  forester: 120, construction_worker: 115, grocery_clerk: 100, barista: 95,
  day_laborer: 85, college_student: 40, student: 0, child: 0, retired: 60,
  doctor: 210, nurse: 130, police_officer: 140, firefighter: 135, baker: 120,
  chef: 150, waiter: 95, school_teacher: 160, librarian: 115, banker: 175,
  innkeeper: 125, street_sweeper: 80, shopkeeper: 110,
};
const UPKEEP_PER_DAY = 55;

const SKILL_BASE = {
  artisan: 'Crafting', barista: 'Brewing', cafe_owner: 'Hospitality', child: 'Play',
  college_instructor: 'Teaching', college_student: 'Research',
  construction_company_owner: 'Site Management', construction_worker: 'Construction',
  day_laborer: 'General Labor', farmer: 'Farming', forester: 'Foresting',
  government_clerk: 'Civic Administration', grocery_clerk: 'Retail',
  grocery_store_owner: 'Retail Management', miner: 'Mining',
  office_administrator: 'Administration', pharmacist: 'Pharmacy',
  retired: 'Mentorship', student: 'Studying', trader: 'Negotiation',
  doctor: 'Medicine', nurse: 'Nursing', police_officer: 'Policing',
  firefighter: 'Firefighting', baker: 'Baking', chef: 'Cooking',
  waiter: 'Table Service', school_teacher: 'Teaching', librarian: 'Records',
  banker: 'Banking', innkeeper: 'Innkeeping', street_sweeper: 'Street Cleaning',
  shopkeeper: 'Shopkeeping',
};
const SKILL_POOL = {
  artisan: ['Fine Detailing', 'Tool Repair', 'Pattern Design'],
  trader: ['Price Forecasting', 'Bulk Bargaining', 'Route Trading'],
  day_laborer: ['Load Hauling', 'Quick Hire'], farmer: ['Crop Rotation', 'Irrigation'],
  miner: ['Vein Reading', 'Deep Digging'], forester: ['Timber Grading', 'Trail Clearing'],
  college_instructor: ['Curriculum Design', 'Mentoring'],
  college_student: ['Speed Reading', 'Thesis Writing'],
  office_administrator: ['Records Auditing', 'Scheduling'],
  government_clerk: ['Tax Auditing', 'Petition Review'],
  construction_worker: ['Scaffold Rigging', 'Masonry'],
  construction_company_owner: ['Site Bidding', 'Crew Leadership'],
  grocery_store_owner: ['Inventory Forecasting'], grocery_clerk: ['Fast Checkout'],
  pharmacist: ['Compounding'], cafe_owner: ['Menu Design'], barista: ['Latte Art'],
  retired: ['Storytelling', 'Genealogy'], student: ['Note-Taking'], child: ['Imaginative Play'],
  doctor: ['Diagnosis', 'Surgery'], nurse: ['Triage', 'Ward Rounds'],
  police_officer: ['Interviewing', 'Case Filing'], firefighter: ['Ladder Work', 'Rescue'],
  baker: ['Sourdough', 'Pastry'], chef: ['Menu Design', 'Butchery'],
  waiter: ['Upselling', 'Silver Service'], school_teacher: ['Lesson Planning', 'Reading Groups'],
  librarian: ['Cataloguing', 'Archive Search'], banker: ['Credit Assessment', 'Bookkeeping'],
  innkeeper: ['Housekeeping', 'Brewing'], street_sweeper: ['Route Planning'],
  shopkeeper: ['Stock Rotation', 'Haggling'],
};
const OCC_FACE = {
  artisan: ['🔨', '#F4A259'], barista: ['☕', '#B07D56'], cafe_owner: ['🍰', '#E28E7C'],
  child: ['🧒', '#8ECAE6'], college_instructor: ['📚', '#6A8EAE'], college_student: ['🎒', '#8FB8DE'],
  construction_company_owner: ['📐', '#D98E32'], construction_worker: ['👷', '#E8A33D'],
  day_laborer: ['🧰', '#B5A48B'], farmer: ['🌾', '#7FB069'], forester: ['🌲', '#4F8F5B'],
  government_clerk: ['🏛️', '#C9A227'], grocery_clerk: ['🥕', '#E2833E'],
  grocery_store_owner: ['🛒', '#D96C4F'], miner: ['⛏️', '#8D7B68'],
  office_administrator: ['🗂️', '#7A93AC'], pharmacist: ['💊', '#68A691'],
  retired: ['🌻', '#C08552'], student: ['✏️', '#9BC4E2'], trader: ['⚖️', '#CE7A5C'],
  doctor: ['🩺', '#4E9FD1'], nurse: ['💉', '#6FBFD8'], police_officer: ['🚓', '#3F5E96'],
  firefighter: ['🚒', '#D6503C'], baker: ['🥖', '#D3A05A'], chef: ['👨‍🍳', '#B4586C'],
  waiter: ['🍽️', '#C08196'], school_teacher: ['🏫', '#6FA45A'], librarian: ['📚', '#8A7458'],
  banker: ['🏦', '#9B8A45'], innkeeper: ['🛏️', '#A6784E'], street_sweeper: ['🧹', '#8FA0AE'],
  shopkeeper: ['🏪', '#CE7A5C'],
};
function occFace(o) { return OCC_FACE[o] || ['🙂', '#9AA7B4']; }

// What each occupation wears. shirt/legs are the body colours; `coat` paints a
// white coat or apron over the shirt; `hat` picks the headwear drawn by
// village.js's drawHat. Everyone used to wear one of eight random shirts,
// which made a doctor and a miner indistinguishable on the map.
const OCC_OUTFIT = {
  doctor:            { shirt: '#EAF2F6', legs: '#3B5566', coat: '#FFFFFF', hat: 'scrub-cap' },
  nurse:             { shirt: '#BFE4DC', legs: '#3B5566', coat: '#FFFFFF', hat: 'scrub-cap' },
  pharmacist:        { shirt: '#DFF2EA', legs: '#3B5566', coat: '#FFFFFF' },
  police_officer:    { shirt: '#2F4B7C', legs: '#22385E', hat: 'cap-police' },
  firefighter:       { shirt: '#C6362A', legs: '#3C4654', hat: 'helmet-fire' },
  construction_worker:{ shirt: '#E8A33D', legs: '#6B5A3A', hat: 'helmet-work' },
  construction_company_owner: { shirt: '#D98E32', legs: '#4A4238', hat: 'helmet-work' },
  miner:             { shirt: '#7A6A57', legs: '#4A4238', hat: 'helmet-mine' },
  farmer:            { shirt: '#7FB069', legs: '#7A5230', hat: 'straw' },
  forester:          { shirt: '#3F7A46', legs: '#5A4A32', hat: 'bandana' },
  chef:              { shirt: '#F4F1E8', legs: '#3C4654', coat: '#FFFFFF', hat: 'toque' },
  baker:             { shirt: '#F2E2C6', legs: '#8C6239', coat: '#FFF8EC', hat: 'toque' },
  waiter:            { shirt: '#FFFFFF', legs: '#2E3440' },
  barista:           { shirt: '#B07D56', legs: '#3C4654', coat: '#E8DBC2' },
  cafe_owner:        { shirt: '#E28E7C', legs: '#5A4A32', coat: '#F4E4D4' },
  school_teacher:    { shirt: '#6E9E4A', legs: '#3B4A56', hat: 'mortar' },
  college_instructor:{ shirt: '#6A8EAE', legs: '#2E3440', hat: 'mortar' },
  librarian:         { shirt: '#8A7458', legs: '#4A4238' },
  banker:            { shirt: '#2E3440', legs: '#22262E', coat: '#3E4756' },
  government_clerk:  { shirt: '#C9A227', legs: '#4A4238' },
  office_administrator: { shirt: '#7A93AC', legs: '#2E3440' },
  trader:            { shirt: '#CE7A5C', legs: '#5A4A32' },
  artisan:           { shirt: '#F4A259', legs: '#6B5A3A', hat: 'bandana' },
  shopkeeper:        { shirt: '#CE7A5C', legs: '#4A4238', coat: '#E8DBC2' },
  grocery_clerk:     { shirt: '#E2833E', legs: '#4A4238', coat: '#F2E2C6' },
  grocery_store_owner:{ shirt: '#D96C4F', legs: '#4A4238', coat: '#F2E2C6' },
  innkeeper:         { shirt: '#A6784E', legs: '#5A4A32', coat: '#E8DBC2' },
  street_sweeper:    { shirt: '#8FA0AE', legs: '#5A6472', hat: 'helmet-work' },
  day_laborer:       { shirt: '#B5A48B', legs: '#6B5A3A', hat: 'bandana' },
  college_student:   { shirt: '#8FB8DE', legs: '#3B4A56', hat: 'beanie' },
  student:           { shirt: '#9BC4E2', legs: '#3B4A56' },
  child:             { shirt: '#8ECAE6', legs: '#4A6072' },
  retired:           { shirt: '#C08552', legs: '#5A5248', hat: 'straw' },
};

// What a resident is visibly holding while working. Drives the little tool and
// the working animation on the map, so a farmer at the field reads as farming
// rather than as a person standing in a field.
const WORK_TOOL = {
  farmer: 'hoe', miner: 'pick', forester: 'axe', construction_worker: 'hammer',
  construction_company_owner: 'plan', artisan: 'hammer', baker: 'tray', chef: 'pan',
  waiter: 'tray', barista: 'cup', cafe_owner: 'cup', grocery_clerk: 'box',
  grocery_store_owner: 'box', shopkeeper: 'box', trader: 'coin', banker: 'coin',
  day_laborer: 'box', doctor: 'cross', nurse: 'cross', pharmacist: 'cross',
  police_officer: 'badge', firefighter: 'hose', school_teacher: 'book',
  college_instructor: 'book', librarian: 'book', government_clerk: 'quill',
  office_administrator: 'quill', innkeeper: 'tray', street_sweeper: 'broom',
  college_student: 'book', student: 'book',
};

const VERB_AT_WORK = {
  artisan: 'Crafting goods', barista: 'Pulling espresso', cafe_owner: 'Running the cafe',
  college_instructor: 'Teaching a class', college_student: 'In lecture',
  construction_company_owner: 'Managing the site', construction_worker: 'Constructing',
  day_laborer: 'Picking up day work', farmer: 'Farming the fields',
  forester: 'Foresting timber', government_clerk: 'Filing paperwork & collecting tax',
  grocery_clerk: 'Stocking shelves', grocery_store_owner: 'Running the grocery',
  miner: 'Mining ore', office_administrator: 'Administering records',
  pharmacist: 'Dispensing medicine', trader: 'Trading at the stalls', student: 'In class',
  doctor: 'Treating patients', nurse: 'On ward rounds', police_officer: 'On patrol',
  firefighter: 'Drilling at the station', baker: 'Baking bread', chef: 'Cooking service',
  waiter: 'Waiting tables', school_teacher: 'Teaching the children',
  librarian: 'Shelving records', banker: 'Handling deposits',
  innkeeper: 'Keeping the rooms', street_sweeper: 'Sweeping the streets',
  shopkeeper: 'Minding the shop',
};
// What someone is doing when they are AT a place but not doing their own job
// there. Every zone needs an entry or the fallback ("Out and about") shows up
// for whole districts - which it was doing for the school, library, bank, inn,
// police, fire, restaurant, bakery, hospital and the new outer zones.
const ZONE_ACTIVITY = {
  grocery: 'Shopping', cafe: 'Having coffee', pharmacy: 'Picking up medicine',
  park: 'Relaxing in the park', townsquare: 'Socialising',
  college: 'Visiting the college', government: 'Filing a petition',
  marketrow: 'Browsing the market', tradeoffices: 'Doing business',
  construction: 'Watching construction', farmland: 'Walking the fields',
  oreoutcrop: 'At the outcrop', forest: 'Walking the forest',
  housing: 'At home', housingEast: 'At home',
  school: 'At the school', library: 'Reading at the library',
  bank: 'At the bank', inn: 'At the inn', hospital: 'At the hospital',
  police: 'At the police station', fire: 'At the fire station',
  restaurant: 'Eating at the restaurant', bakery: 'At the bakery',
  pasture: 'Out at the pasture', orchard: 'Walking the orchard',
  docks: 'Down at the docks',
};

// ---- deterministic seeding ----------------------------------------------
// FNV-1a, not djb2. djb2's final step is h*33 + c, so two salts differing only
// in their last character ('jx' vs 'jy') come out one apart - which made every
// resident's x and y offsets identical to four decimal places, standing them
// all on their zone's diagonal. In isometric projection a diagonal is a
// vertical line, so the town rendered as columns of stacked people. FNV-1a's
// multiply-after-xor spreads that one-bit difference across the whole word.
/** Nudge a hex colour lighter or darker, so a uniform still has some
 * person-to-person variation. */
function shiftColour(hex, f) {
  if (!hex || hex[0] !== '#') return hex;
  const n = parseInt(hex.slice(1), 16);
  const cl = (v) => Math.max(0, Math.min(255, Math.round(v * f)));
  return `rgb(${cl((n >> 16) & 255)},${cl((n >> 8) & 255)},${cl(n & 255)})`;
}

function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  // Final avalanche (murmur3's fmix32). Without it, two salts differing only in
  // their last character still come out a *constant* distance apart, because
  // the loop's last step is a single multiply - so jy stayed jx plus a fixed
  // offset, and the residents sat on a diagonal all the same. Mixing the high
  // bits down is what actually decorrelates them.
  h ^= h >>> 16; h = Math.imul(h, 2246822507);
  h ^= h >>> 13; h = Math.imul(h, 3266489909);
  h ^= h >>> 16;
  return h >>> 0;
}
function seeded01(str, salt) { return (hash(str + '|' + salt) % 100000) / 100000; }

// ---- per-resident derived state -----------------------------------------
const HAIR = ['#3B2A20', '#6B4A2B', '#2B2B33', '#8C6239', '#A6603C', '#4A3B32'];
const SHIRT = ['#E4694E', '#4E82C4', '#59A86B', '#D6A128', '#8E6BC4', '#DA7BA8', '#3FA9A0', '#C4643E'];

function initAgentDerived(a) {
  // Keep the genesis kinship so resetHealth() can restore it: births append to
  // `rel` and bump `nk`, and a replay has to undo exactly that.
  if (!a._relBase) { a._relBase = (a.rel || []).slice(); a._nkBase = a.nk || 0; }
  if (a.dz) a.dayZone = a.dz;
  else if (a.br === 'elderly') a.dayZone = seeded01(a.id, 'day') < 0.5 ? 'park' : 'library';
  else a.dayZone = 'tradeoffices';
  const p = seeded01(a.id, 'evening');
  a.eveningZone = p < 0.34 ? 'park' : (p < 0.67 ? 'townsquare' : (a.br === 'elderly' ? 'inn' : 'cafe'));

  a.skillBase = SKILL_BASE[a.occ] || 'General Ability';
  const pool = SKILL_POOL[a.occ] || ['Improvisation'];
  a.skillUnlocked = pool[Math.floor(seeded01(a.id, 'skillpick') * pool.length)];
  a.skillUnlockDay = 2 + Math.floor(seeded01(a.id, 'unlockday') * 5);
  a._loggedSkill = false;

  // Dress by trade. Shades vary slightly per person so a row of ten farmers
  // is not ten identical sprites.
  const fit = OCC_OUTFIT[a.occ] || { shirt: SHIRT[Math.floor(seeded01(a.id, 'shirt') * SHIRT.length)] };
  a.outfit = fit;
  a.shirt = shiftColour(fit.shirt, 0.92 + seeded01(a.id, 'tone') * 0.16);
  a.hair = HAIR[Math.floor(seeded01(a.id, 'hair') * HAIR.length)];

  a.wage = WAGE[a.occ] || 0;
  a.upkeep = a.br === 'minor' ? 0 : UPKEEP_PER_DAY;   // minors are covered by their household
  // A8: where this resident's own age clock starts. Genesis minors start at
  // the age they were seeded with, at hour zero; a resident born during the
  // run starts at zero, on the day they were born. Adults never age
  // (DESIGN.md section 4, "adults static") and get no clock at all.
  if (a.br === 'minor') {
    a._bornMinorAge = a.bornDay !== undefined ? 0 : a.age;
    a._bornAtHour = a.bornDay !== undefined ? a.bornDay * 24 : 0;
  }
  a._costDecay = 40 + seeded01(a.id, 'decay') * 70;

  // Stable position offsets inside a zone, spread across most of its area so a
  // busy workplace reads as a crowd rather than a single clump.
  a._jx = 0.06 + seeded01(a.id, 'jx') * 0.88;
  a._jy = 0.06 + seeded01(a.id, 'jy') * 0.88;
  // Home is household-shared, with a small per-person offset so family members
  // stand together at their own house instead of exactly on top of each other.
  a._eastSide = seeded01(a.hh, 'side') > 0.55;
  const hjx = 0.06 + seeded01(a.hh, 'hjx') * 0.88;
  const hjy = 0.06 + seeded01(a.hh, 'hjy') * 0.88;
  a._hjx = Math.min(0.96, Math.max(0.04, hjx + (seeded01(a.id, 'hjx2') - 0.5) * 0.05));
  a._hjy = Math.min(0.96, Math.max(0.04, hjy + (seeded01(a.id, 'hjy2') - 0.5) * 0.05));
}
AGENTS.forEach(initAgentDerived);

const AGENT_BY_ID = {};
AGENTS.forEach(a => AGENT_BY_ID[a.id] = a);

// ---- who lives where -----------------------------------------------------
// Every household is given an actual address. Before this, night positions
// came from a random point anywhere inside the housing zone, which is why
// residents were shown asleep on the grass between buildings. Homes are
// matched to what a household's earners make: the best-paid families get the
// villas, then the houses, and single people and low-wage households take a
// flat in an apartment block.
function assignHomes() {
  const households = {};
  for (const a of AGENTS) (households[a.hh] = households[a.hh] || []).push(a);

  const ranked = Object.entries(households).map(([hh, members]) => ({
    hh, members,
    // household income = its best earner, which is what decides what a family
    // can afford to live in
    income: Math.max(0, ...members.map(m => WAGE[m.occ] || 0)),
    size: members.length,
  })).sort((x, y) => (y.income - x.income) || (y.size - x.size));

  // Slots best-first: modern houses, then 3 BHK, 2 BHK, and finally the
  // 1 BHK flats. Ordering by tier descending matters - an earlier version
  // hardcoded three tiers, so when the fourth (modern) was added it never
  // entered the pool and the best-paid households ended up in 2 BHKs.
  const homes = plans.filter(p => p.home).sort((a, b) => b.tier - a.tier);
  const slots = [];
  for (const h of homes) {
    const n = h.capacity || 1;
    for (let i = 0; i < n; i++) slots.push({ plan: h, idx: i });
  }

  ranked.forEach((hd, i) => {
    const slot = slots[Math.min(i, slots.length - 1)];
    for (const m of hd.members) {
      m._home = slot.plan;
      m._flat = slot.idx || 0;
      // a stable spot inside the home's own footprint, never outside it
      const inset = 1.2;
      const jx = inset + seeded01(m.id, 'homex') * Math.max(0.4, slot.plan.w - inset * 2);
      const jy = inset + seeded01(m.id, 'homey') * Math.max(0.4, slot.plan.h - inset * 2);
      m._homeTx = slot.plan.x + jx;
      m._homeTy = slot.plan.y + jy;
    }
  });
}


// ---- health, ailment and death ------------------------------------------
// Every constant here is copied from world/life/constants.py, not invented.
// DESIGN.md section 5 pins the incidence rates and the 85+ hazard threshold;
// the hazards marked ASSUMPTION there are carried across with the same values.
const AILMENT_DAILY_INCIDENCE = { short: 0.04, medium: 0.01, long: 0.0025, terminal: 0.0008 };
const AILMENT_DURATION = { short: 1, medium: [3, 7], long: 20 };
const TERMINAL_DEATH_WINDOW_DAYS = [3, 10];
const CHRONIC_UNTREATED_DAILY_HAZARD = { medium: 0.005, long: 0.01 };
const ELDERLY_HAZARD_AGE_YEARS = 85;
const ELDERLY_DAILY_HAZARD = 0.004;
const WORK_CAPACITY_BY_AILMENT = { short: 0.5, medium: 0.3, long: 0.15, terminal: 0.0, healthy: 1.0 };
const PHARMACY_SHORT_TREATMENT_COST_SIM = 500;
const REVIVAL_COST_SIM = 10000;

// The hospital is why building it matters. world/life/constants.py:
// "At genesis the pharmacy only covers short ailments ... so every medium/long
// ailment is untreated by construction until the town builds a hospital."
const PHARMACY_TREATS = ['short'];
const HOSPITAL_TREATS = ['short', 'medium', 'long'];

// ---- what the town builds, and when -------------------------------------
// DESIGN.md section 1 lists the genesis buildings and says outright that the
// hospital and police station are "deliberately absent - the town must build
// its institutions." Everything below is therefore a construction project with
// a completion day, not a building that exists on day one. The days are a
// plausible build order, flagged as modelled on the page.
const BUILD_PROJECTS = [
  // NOTE: day 0 for all of these is a deliberate divergence from DESIGN.md
  // section 1, which leaves the hospital and police station out of genesis so
  // "the town must build its institutions." Every building now stands from the
  // first tick and is staffed. Costs are still charged to the treasury on day 0.
  { zone: 'bakery',     name: 'Bakery',          icon: '🥖', day: 0,  build: 'bakery',     kind: 'business',
    note: 'A resident registers a bakery — grain → flour → bread, the first real production chain.' },
  { zone: 'restaurant', name: 'Restaurant',      icon: '🍽️', day: 0,  build: 'restaurant', kind: 'business',
    note: 'Cooked meals for sale; a second evening destination beside the cafe.' },
  { zone: 'school',     name: 'Town School',     icon: '🏫', day: 0,  build: 'school',     kind: 'civic',
    note: 'Somewhere for the 38 minors to go that is not the college.' },
  { zone: 'hospital',   name: 'Hospital',        icon: '🏥', day: 0, build: 'hospital',   kind: 'institution',
    note: 'DESIGN.md leaves this out of genesis on purpose. Until it opens the pharmacy treats only short ailments, so every medium and long illness runs untreated at a real daily death hazard. Opening it makes them treatable.' },
  { zone: 'police',     name: 'Police Station',  icon: '🚓', day: 0, build: 'police',     kind: 'institution',
    note: 'Also deliberately absent at genesis. Complaints desk → case file → warrant → fine or jail.' },
  { zone: 'library',    name: 'Library',         icon: '📚', day: 0, build: 'library',    kind: 'civic',
    note: 'Public records the police can actually search — they are not omniscient.' },
  { zone: 'fire',       name: 'Fire Station',    icon: '🚒', day: 0, build: 'fire',       kind: 'institution',
    note: 'Protects the built stock the town has spent its treasury on.' },
  { zone: 'bank',       name: 'Town Bank',       icon: '🏦', day: 0, build: 'bank',       kind: 'business',
    note: 'Deposits and credit at the government-set margin.' },
  { zone: 'inn',        name: 'The Inn',         icon: '🛏️', day: 0, build: 'inn',        kind: 'business',
    note: 'Beds for anyone between households.' },
];
// A project's `day` is the earliest the town WANTS it. When it actually opens
// depends on whether the treasury can pay for it: DESIGN.md's ledger "refuses
// an over-large debit" and balances may never go negative, so the town cannot
// put up a 140,000-sim hospital out of a 10,000-sim treasury. This walks the
// days once, accruing each day's net, and records the first day each project
// is both wanted and affordable.
const FISCAL_HORIZON_DAYS = 1200;
let _fiscal = null;
function fiscal() {
  if (_fiscal) return _fiscal;
  const balance = new Array(FISCAL_HORIZON_DAYS + 1);
  const cumIn = new Array(FISCAL_HORIZON_DAYS + 1);
  const cumOut = new Array(FISCAL_HORIZON_DAYS + 1);
  let bal = 0, inSum = 0, outSum = 0;
  const aliveNow = AGENTS.filter(isAlive).length || POP;
  for (let d = 0; d <= FISCAL_HORIZON_DAYS; d++) {
    const net = dailyNetBeforeBuilding(d);
    const income = aliveNow * HEAD_TAX_PER_DAY;
    inSum += income; outSum += income - net;
    bal += net;
    balance[d] = bal;
    cumIn[d] = inSum; cumOut[d] = outSum;
  }
  _fiscal = { balance, cumIn, cumOut };
  return _fiscal;
}
// Every institution is genesis infrastructure now, so nothing is ever pending.
function openedOn() { return 0; }
function isBuilt(zoneId, day) {
  const proj = BUILD_PROJECTS.find(b => b.zone === zoneId);
  return !proj || day >= openedOn(zoneId);
}
function hospitalOpen(day) { return day >= openedOn('hospital'); }
function treatableOn(day) { return hospitalOpen(day) ? HOSPITAL_TREATS : PHARMACY_TREATS; }

// A resident's health is simulated day by day and cached, because each day
// depends on the one before it (an ailment that started on day 4 is still
// running on day 6). Recomputing the whole history every frame would be far
// too slow, so the sim only ever steps forward; scrubbing the clock backwards
// replays from day 0, which is cheap enough at 250 residents.
// `log` is a capped feed of recent events; `causes` is the cumulative tally,
// which must be kept separately or old deaths vanish as the feed scrolls.
const healthSim = { day: -1, states: {}, log: [], deaths: 0, recoveries: 0, causes: {} };

function resetHealth() {
  // Remove anyone born during the previous run before replaying, or a
  // scrub-and-replay would stack duplicate newborns onto the population.
  for (let i = AGENTS.length - 1; i >= 0; i--) {
    if (AGENTS[i].bornDay !== undefined) { delete AGENT_BY_ID[AGENTS[i].id]; AGENTS.splice(i, 1); }
  }
  AGENTS.forEach(a => { if (a._relBase) { a.rel = a._relBase.slice(); a.nk = a._nkBase; } });
  birthCount = 0;
  birthLog.length = 0;
  _fiscal = null;
  healthSim.day = -1;
  healthSim.states = {};
  healthSim.log = [];
  healthSim.deaths = 0;
  healthSim.recoveries = 0;
  healthSim.causes = {};
  for (const a of AGENTS) {
    healthSim.states[a.id] = { status: 'healthy', ailment: null, since: 0, until: 0, deathDay: null, cause: null, alive: true };
  }
}

function recordDeath(d, a, h) {
  h.deathDay = d;
  healthSim.deaths++;
  healthSim.causes[h.cause] = (healthSim.causes[h.cause] || 0) + 1;
  healthSim.log.unshift({ day: d, id: a.id, name: a.n, kind: 'death', cause: h.cause });
}

function stepHealthDay(d) {
  const treats = treatableOn(d);
  for (const a of AGENTS) {
    const h = healthSim.states[a.id];
    if (!h.alive) continue;
    const roll = (salt) => seeded01(a.id, 'h' + d + salt);

    // 1. A running ailment either resolves, kills, or continues.
    if (h.ailment) {
      const treated = treats.includes(h.ailment);
      if (h.ailment === 'terminal') {
        if (d >= h.deathDay) { h.alive = false; h.status = 'deceased'; h.cause = 'terminal illness';
          recordDeath(d, a, h); continue; }
      } else if (!treated && CHRONIC_UNTREATED_DAILY_HAZARD[h.ailment] &&
                 roll('chronic') < CHRONIC_UNTREATED_DAILY_HAZARD[h.ailment]) {
        h.alive = false; h.status = 'deceased'; h.cause = 'untreated ' + h.ailment + ' illness';
        recordDeath(d, a, h); continue;
      }
      if (d >= h.until) {
        healthSim.log.unshift({ day: d, id: a.id, name: a.n, kind: 'recovery', cause: h.ailment });
        healthSim.recoveries++;
        h.ailment = null; h.status = 'healthy'; h.deathDay = null;
      }
    }

    // 2. The 85+ hazard applies whether or not they are ill.
    if (h.alive && a.age + d / 365 >= ELDERLY_HAZARD_AGE_YEARS && roll('eld') < ELDERLY_DAILY_HAZARD) {
      h.alive = false; h.status = 'deceased'; h.cause = 'old age (85+ hazard)';
      recordDeath(d, a, h);
      continue;
    }

    // 3. A healthy resident may fall ill, at the real incidence rates.
    if (h.alive && !h.ailment) {
      for (const kind of ['terminal', 'long', 'medium', 'short']) {
        if (roll(kind) < AILMENT_DAILY_INCIDENCE[kind]) {
          h.ailment = kind; h.status = kind; h.since = d;
          if (kind === 'terminal') {
            const [lo, hi] = TERMINAL_DEATH_WINDOW_DAYS;
            h.deathDay = d + lo + Math.floor(roll('tw') * (hi - lo + 1));
            h.until = h.deathDay;
          } else if (kind === 'medium') {
            const [lo, hi] = AILMENT_DURATION.medium;
            h.until = d + lo + Math.floor(roll('mw') * (hi - lo + 1));
          } else {
            h.until = d + (kind === 'short' ? AILMENT_DURATION.short : AILMENT_DURATION.long);
          }
          healthSim.log.unshift({ day: d, id: a.id, name: a.n, kind: 'illness', cause: kind });
          break;
        }
      }
    }
  }
  stepBirths(d);
  healthSim.log = healthSim.log.slice(0, 80);
}

function advanceHealthTo(day) {
  if (day < healthSim.day) resetHealth();
  if (healthSim.day < 0) resetHealth();
  for (let d = healthSim.day + 1; d <= day; d++) stepHealthDay(d);
  healthSim.day = Math.max(healthSim.day, day);
}

function healthOf(a) { return healthSim.states[a.id] || { status: 'healthy', alive: true, ailment: null }; }
function isAlive(a) { return healthOf(a).alive; }
function workCapacity(a) {
  const h = healthOf(a);
  if (!h.alive) return 0;
  return WORK_CAPACITY_BY_AILMENT[h.ailment || 'healthy'];
}
const HEALTH_LABEL = {
  healthy: 'Healthy', short: 'Short illness', medium: 'Medium illness',
  long: 'Long illness', terminal: 'Terminally ill', deceased: 'Deceased',
};

// ---- shift work ----------------------------------------------------------
// A hospital, a police station and a fire station do not close at 17:00. The
// staff of those services are split across three shifts so the buildings are
// manned round the clock, and - the part that was wrong before - a night-shift
// nurse sleeps through the MORNING, not at midnight while she is on ward.
const SHIFT_SERVICES = {
  doctor: 'hospital', nurse: 'hospital',
  police_officer: 'police', firefighter: 'fire',
  innkeeper: 'inn',
};
const SHIFTS = {
  day:     { on: [7, 15],  sleep: [22, 6] },   // 07-15 on, sleeps 22-06
  evening: { on: [15, 23], sleep: [0, 8] },    // 15-23 on, sleeps 00-08
  night:   { on: [23, 7],  sleep: [8, 16] },   // 23-07 on, sleeps 08-16
};
const SHIFT_NAMES = ['day', 'evening', 'night'];

/** Hour ranges wrap midnight (a night shift runs 23:00-07:00), so a plain
 * start <= h < end test is wrong for exactly the shift that matters most. */
function inWindow(h, [start, end]) {
  return start <= end ? (h >= start && h < end) : (h >= start || h < end);
}
function onShift(a, h) { return a.shift && inWindow(h, SHIFTS[a.shift].on); }
function shiftAsleep(a, h) { return a.shift && inWindow(h, SHIFTS[a.shift].sleep); }

/** Spread each service's staff evenly across the three shifts, so no shift is
 * left without cover. Assigned per workplace, not globally, or the hospital
 * could end up with every doctor on days and the police with none at night. */
function assignShifts() {
  const byService = {};
  for (const a of AGENTS) {
    const svc = SHIFT_SERVICES[a.occ];
    if (!svc) continue;
    (byService[svc + '|' + a.occ] = byService[svc + '|' + a.occ] || []).push(a);
  }
  for (const key of Object.keys(byService)) {
    const staff = byService[key].sort((x, y) => x.id.localeCompare(y.id));
    staff.forEach((a, i) => { a.shift = SHIFT_NAMES[i % 3]; });
  }
}

// ---- movement -----------------------------------------------------------
function zoneAnchor(zoneId, a, useHome) {
  // Home is split across the two residential neighbourhoods so 250 people are
  // not stacked on one block; which one a household lives in is fixed by its
  // household id, so relatives always share a neighbourhood.
  // Home is a real address inside a real building, not a point somewhere in
  // the housing district.
  if (zoneId === 'housing' && a._home) return { tx: a._homeTx, ty: a._homeTy };
  let z = ZONES[zoneId] || ZONES.townsquare;
  if (zoneId === 'housing') z = a._eastSide ? ZONES.housingEast : ZONES.housing;
  const jx = useHome ? a._hjx : a._jx;
  const jy = useHome ? a._hjy : a._jy;
  return { tx: z.x + z.w * jx, ty: z.y + z.h * jy };
}
/** Where a resident stands inside a zone at this moment.
 *
 * Each person gets `TASK_POINTS` fixed spots scattered across the zone - a
 * field has rows, a market has stalls, a ward has beds - and works through
 * them in a loop, walking between one and the next. The spots are seeded per
 * agent so they are stable across a replay, and the walk is interpolated so
 * you see someone crossing the yard rather than teleporting.
 */
const TASK_POINTS = 4;
const TASK_MINUTES = 55;          // how long one task takes before moving on
// Over half of each cycle is spent on the move, so people are seen crossing
// the room and the yard rather than blinking between two standing spots.
const WALK_FRACTION = 0.55;

/** A walking pace: a short push off, a long constant stride, a short slowing
 * at the end. A plain ease-in-out accelerates the whole way and reads as
 * gliding once the distance is more than a couple of tiles. */
function paceEase(e) {
  const A = 0.15;                            // share spent speeding up / slowing
  if (e < A) return (e * e) / (2 * A * (1 - A));
  if (e > 1 - A) { const d = 1 - e; return 1 - (d * d) / (2 * A * (1 - A)); }
  return (e - A / 2) / (1 - A);
}

function taskSpot(a, zoneId, i) {
  const z = ZONES[zoneId] || ZONES.townsquare;
  const jx = 0.08 + seeded01(a.id + zoneId + i, 'tx') * 0.84;
  const jy = 0.08 + seeded01(a.id + zoneId + i, 'ty') * 0.84;
  return { tx: z.x + z.w * jx, ty: z.y + z.h * jy };
}

function taskPos(a, zoneId, hourFrac, salt) {
  const period = TASK_MINUTES / 60;
  const phaseT = (hourFrac + seeded01(a.id + salt, 'off') * period) / period;
  const idx = Math.floor(phaseT);
  const frac = phaseT - idx;
  const from = taskSpot(a, zoneId, idx % TASK_POINTS);
  const to = taskSpot(a, zoneId, (idx + 1) % TASK_POINTS);
  // Stand at the spot just walked TO, not the one walked FROM. Returning
  // `from` here teleported everyone back to where they started the moment the
  // walk finished, so each cycle was a walk out followed by a snap back.
  if (frac > WALK_FRACTION) return { tx: to.tx, ty: to.ty, walking: false };
  const e = frac / WALK_FRACTION;            // 0..1 across the walk
  const ease = paceEase(e);
  return { tx: from.tx + (to.tx - from.tx) * ease, ty: from.ty + (to.ty - from.ty) * ease, walking: true };
}

function phaseAt(hour) {
  if (hour >= 6 && hour < 8) return 'commute1';
  if (hour >= 8 && hour < 17) return 'day';
  if (hour >= 17 && hour < 18) return 'commute2';
  if (hour >= 18 && hour < 20) return 'evening';
  if (hour >= 20 && hour < 22) return 'commute3';
  return 'night';
}
const PHASE_LABEL = {
  night: 'town asleep', commute1: 'heading out for the day', day: 'at work / school',
  commute2: 'heading out for the evening', evening: 'evening — cafe, park, inn, square',
  commute3: 'heading home',
};
/** Walk from A to B the way a person would: out to the street, along it, then
 * in to the door. A straight line between two zone anchors sends people
 * clipping diagonally through houses and across the river, which is the main
 * thing that made the commute look wrong.
 *
 * The route is a three-leg dog-leg through a corridor point chosen on the
 * road grid, and `f` walks it at a constant rate, so the pace stays even
 * around the corner instead of speeding up on the diagonal.
 */
function walkRoute(p1, p2, f) {
  // corridor point: turn along the x axis first, then the y - which is what
  // the street layout actually offers
  const via = { tx: p2.tx, ty: p1.ty };
  const legA = Math.abs(via.tx - p1.tx);
  const legB = Math.abs(p2.ty - via.ty);
  const total = legA + legB;
  if (total < 0.001) return { tx: p2.tx, ty: p2.ty };
  const d = f * total;
  if (d <= legA) {
    const g = legA < 0.001 ? 1 : d / legA;
    return { tx: p1.tx + (via.tx - p1.tx) * g, ty: p1.ty };
  }
  const g = legB < 0.001 ? 1 : (d - legA) / legB;
  return { tx: via.tx, ty: via.ty + (p2.ty - via.ty) * g };
}

function agentPosition(a, hourFrac) {
  const dayStart = Math.floor(hourFrac / 24) * 24;
  const day = Math.floor(hourFrac / 24);
  const hod = hourFrac - dayStart;
  const phase = phaseAt(Math.floor(hod));
  const h = healthOf(a);
  // The dead stay where they were buried; nothing below applies to them.
  if (!h.alive) return { pos: zoneAnchor('housing', a, true), zone: 'housing', moving: false, dead: true };
  // Anyone too ill to work spends the day being treated: at the pharmacy while
  // that is all the town has, at the hospital once it opens.
  if (h.ailment && WORK_CAPACITY_BY_AILMENT[h.ailment] <= 0.3 && phase === 'day') {
    const ward = hospitalOpen(day) && isBuilt('hospital', day) ? 'hospital' : 'pharmacy';
    return { pos: taskPos(a, ward, hourFrac, 'ward'), zone: ward, moving: false, ill: true };
  }
  // Shift workers ignore the town's ordinary day entirely: they are at work
  // when their shift says so, asleep in their own bed when it says so, and off
  // duty the rest of the time.
  if (a.shift) {
    if (onShift(a, hod)) {
      const t = taskPos(a, a.dayZone, hourFrac, 'shift');
      return { pos: t, zone: a.dayZone, moving: t.walking, onShift: true };
    }
    if (shiftAsleep(a, hod)) {
      return { pos: zoneAnchor('housing', a, true), zone: 'housing', moving: false, offDuty: true };
    }
    const t = taskPos(a, a.eveningZone, hourFrac, 'off');
    return { pos: t, zone: a.eveningZone, moving: t.walking, offDuty: true };
  }

  if (phase === 'night') return { pos: zoneAnchor('housing', a, true), zone: 'housing', moving: false };
  // A working day is not nine hours of standing still. Each resident walks a
  // small circuit of task points inside their workplace, so the town reads as
  // people doing jobs rather than as pins dropped on buildings.
  if (phase === 'day') {
    const t = taskPos(a, a.dayZone, hourFrac, 'work');
    return { pos: t, zone: a.dayZone, moving: t.walking };
  }
  if (phase === 'evening') {
    const t = taskPos(a, a.eveningZone, hourFrac, 'eve');
    return { pos: t, zone: a.eveningZone, moving: t.walking };
  }
  let from, to, base, len;
  if (phase === 'commute1') { from = 'housing'; to = a.dayZone; base = 6; len = 2; }
  else if (phase === 'commute2') { from = a.dayZone; to = a.eveningZone; base = 17; len = 1; }
  else { from = a.eveningZone; to = 'housing'; base = 20; len = 2; }
  const p1 = zoneAnchor(from, a, from === 'housing');
  const p2 = zoneAnchor(to, a, to === 'housing');
  const tt = Math.min(1, Math.max(0, (hod - base) / len));
  const pos = walkRoute(p1, p2, paceEase(tt));
  return { pos, zone: tt < 0.5 ? from : to, moving: true };
}
function activityFor(a, zone, hod) {
  if (a.shift) {
    if (onShift(a, hod)) return (VERB_AT_WORK[a.occ] || 'On duty') + ' · ' + a.shift + ' shift';
    if (shiftAsleep(a, hod)) return 'Resting after the ' + a.shift + ' shift';
    return (ZONE_ACTIVITY[zone] || 'Off duty') + ' · off duty';
  }
  const phase = phaseAt(Math.floor(hod));
  if (zone === 'housing') return phase === 'night' ? 'Asleep at home' : 'At home';
  if (phase === 'commute1' || phase === 'commute2' || phase === 'commute3') return 'Walking to ' + (ZONES[zone] ? ZONES[zone].label : 'town');
  if (phase === 'day' && zone === a.dayZone && VERB_AT_WORK[a.occ]) return VERB_AT_WORK[a.occ];
  return ZONE_ACTIVITY[zone] || 'Out and about';
}

// ---- economy ------------------------------------------------------------
// A resident's balance is a pure function of the day, so scrubbing the clock
// backwards gives exactly the same figures as playing forwards.
function balanceOn(a, day) {
  const net = a.wage - a.upkeep - (a.br === 'minor' ? 0 : HEAD_TAX_PER_DAY);
  const drift = (seeded01(a.id, 'drift') - 0.45) * 12 * day;
  return Math.max(0, Math.round(a.bal + net * day + drift));
}
function balanceNow(a, hourFrac) {
  const d = Math.floor(hourFrac / 24);
  const f = (hourFrac - d * 24) / 24;
  return Math.round(balanceOn(a, d) + (balanceOn(a, d + 1) - balanceOn(a, d)) * f);
}
function costSpent(a, hourFrac) {
  return (a.usd / 100) * (1 - Math.exp(-hourFrac / a._costDecay));
}

// ---- A8: coming of age (The 250 section 26) ------------------------------
// "At 18 (MINOR_AGE_LIMIT_YEARS), a former minor crosses into the working-age
// population... the parent-billing relationship for their own thinking should
// end... What they do next is not scripted."
//
// The engine half of this is world/life/household.py's come_of_age(); this is
// the viewer's model of the same rule, running on the same two constants.
// Minors age at DESIGN.md section 4's rate - 1 year per game-hour - which is
// deliberately fast, so birth -> childhood -> working age is something a short
// run can actually show instead of a mechanic nobody ever sees fire.
const YEARS_AGED_PER_GAME_HOUR = 1;
const MINOR_AGE_LIMIT_YEARS = 18;

// The three futures section 26 names. Weights are a modelling assumption, not
// a number from the design - what is NOT an assumption is that the choice
// exists and has consequences for the household and the bill.
const COMING_OF_AGE_WEIGHTS = [
  ['stay_and_contribute', 0.45],
  ['move_out_alone', 0.35],
  ['pair_off', 0.20],
];
const COMING_OF_AGE_LABEL = {
  stay_and_contribute: 'Stayed home, now contributing to the household bill',
  move_out_alone: 'Moved out and formed a single-person household',
  pair_off: 'Paired off and started a household together',
};

// Section 26: "this is where the town's occupational roster actually gets
// filled over time, not just at genesis." A new adult takes up whichever
// trade the town is shortest of, so a run that loses farmers grows farmers.
const ENTRY_TRADES = [
  'farmer', 'miner', 'forester', 'day_laborer', 'artisan', 'trader',
  'shopkeeper', 'construction_worker', 'office_administrator', 'waiter',
];

/** The hour of the run at which this minor turns 18, or null if they never
 *  were one. A pure function of their genesis age, so scrubbing the clock
 *  backwards un-ages them exactly the way it un-spends their money. */
function comingOfAgeHour(a) {
  if (a._bornMinorAge === undefined) return null;
  return Math.max(0, (MINOR_AGE_LIMIT_YEARS - a._bornMinorAge) / YEARS_AGED_PER_GAME_HOUR);
}

/** How old this resident is right now. Adults are static (DESIGN.md section
 *  4: "adults static"), minors age until they hit the limit and stop. */
function ageAt(a, hourFrac) {
  const coa = comingOfAgeHour(a);
  if (coa === null) return a.age;
  const born = a._bornAtHour || 0;
  if (hourFrac <= born) return a._bornMinorAge;
  return Math.min(
    MINOR_AGE_LIMIT_YEARS,
    a._bornMinorAge + (hourFrac - born) * YEARS_AGED_PER_GAME_HOUR
  );
}
function isAdultAt(a, hourFrac) {
  const coa = comingOfAgeHour(a);
  if (coa === null) return a.br !== 'minor';
  return hourFrac >= (a._bornAtHour || 0) + coa;
}
/** Working-age bracket as of now, which is what the census counts. */
function bracketAt(a, hourFrac) {
  if (a.br !== 'minor' && a._bornMinorAge === undefined) return a.br;
  return isAdultAt(a, hourFrac) ? 'working' : 'minor';
}

function comingOfAgeChoice(a) {
  let r = seeded01(a.id, 'coming-of-age');
  for (const [choice, share] of COMING_OF_AGE_WEIGHTS) {
    if (r < share) return choice;
    r -= share;
  }
  return COMING_OF_AGE_WEIGHTS[COMING_OF_AGE_WEIGHTS.length - 1][0];
}
function entryTrade(a) {
  return ENTRY_TRADES[Math.floor(seeded01(a.id, 'entry-trade') * ENTRY_TRADES.length)];
}

/** Everyone who crossed eighteen on this day, for the news feed and census. */
function cameOfAgeOn(day) {
  return AGENTS.filter(a => {
    const coa = comingOfAgeHour(a);
    if (coa === null || !isAlive(a)) return false;
    const at = (a._bornAtHour || 0) + coa;
    return Math.floor(at / 24) === day;
  });
}

/** Who pays for this resident's own thinking, right now.
 *
 * DESIGN.md section 4's parent-debit rule is scoped to minors; the moment
 * they cross eighteen the bill is their own. This is the viewer's mirror of
 * Ledger.end_parent_billing() - and, like it, the thing that was missing:
 * before this, a grown child's spend still read as their parent's forever.
 */
function billPayerFor(a, hourFrac) {
  if (bracketAt(a, hourFrac) !== 'minor') return a;
  const sponsorName = (a.pn && a.pn.length) ? a.pn[0] : null;
  const sponsor = sponsorName ? AGENTS.find(x => x.n === sponsorName) : null;
  return sponsor || a;
}

// ---- A9: property and wills (The 250 section 31) -------------------------
// "The town's land divides into 1x1 units, each addressable by its own
// simulation coordinate... Every unit has exactly one owner record at any
// given time." The engine half is world/tools/primitives/property.py; this is
// the viewer's model of the same two classes and the same will override.
const GOVERNMENT_OWNER_ID = 'treasury';
const CIVIC_ZONES = [
  'government', 'school', 'college', 'library', 'hospital', 'police', 'fire',
  'townsquare', 'park', 'construction', 'docks',
  // The temple is a civic place: DESIGN section 7 gives it weddings,
  // prayers and donations, and `pray`/`marry` are gated on it. It was
  // missing from this list and from the map itself.
  'temple',
];

/** Every 1x1 unit one household holds: the footprint of its own address.
 *  A building is a contiguous SET of owned units, not a new kind of object
 *  (section 31.1), so this is derived from the home's own rect. */
function tilesOfPlan(p) {
  const out = [];
  const depth = p.d !== undefined ? p.d : p.h;
  for (let y = Math.floor(p.y); y < Math.ceil(p.y + depth); y++)
    for (let x = Math.floor(p.x); x < Math.ceil(p.x + p.w); x++) out.push([x, y]);
  return out;
}

let _estate = null;
/** The town's land register: who holds what, in both of section 31.2's
 *  classes. Built once - the map does not move. */
function landRegister() {
  if (_estate) return _estate;
  const byOwner = {};
  let government = 0, privateTiles = 0;
  for (const p of plans) {
    const tiles = tilesOfPlan(p);
    const civic = CIVIC_ZONES.includes(p.zone || p.zid);
    if (p.home && !civic) {
      // A dwelling is held by the household that lives in it.
      const owner = (p._ownerId = p._ownerId || ('hh:' + Math.round(p.x) + ',' + Math.round(p.y)));
      (byOwner[owner] = byOwner[owner] || []).push(...tiles);
      privateTiles += tiles.length;
    } else {
      (byOwner[GOVERNMENT_OWNER_ID] = byOwner[GOVERNMENT_OWNER_ID] || []).push(...tiles);
      government += tiles.length;
    }
  }
  _estate = { byOwner, government, private: privateTiles };
  return _estate;
}

/** The land one resident holds, through the home they were assigned. */
function tilesOwnedBy(a) {
  if (!a._home) return [];
  landRegister();
  return tilesOfPlan(a._home);
}

/** Section 31.3: the default is the nearest living relative; a will
 *  overrides it. Spouse first, then eldest child, then a parent, then a
 *  sibling - and the town escheats an estate with nobody left to take it,
 *  exactly as Ledger.settle_inheritance escheats money to the treasury. */
function defaultHeirOf(a) {
  const living = id => {
    const h = AGENT_BY_ID[id];
    return h && isAlive(h) ? h : null;
  };
  const byName = name => AGENTS.find(x => x.n === name && isAlive(x)) || null;
  if (a.sp) { const s = byName(a.sp); if (s) return s; }
  const kin = (a.rel || []);
  for (const want of ['son', 'daughter', 'child', 'father', 'mother', 'brother', 'sister']) {
    const hit = kin.find(r => r.r === want && living(r.id));
    if (hit) return AGENT_BY_ID[hit.id];
  }
  for (const r of kin) { const h = living(r.id); if (h) return h; }
  return null;
}

/** A resident's will. Most people have none, and the default stands; the
 *  ones who set one are the section 31.3 case - "exactly as Kenji specified
 *  if he'd set one while he was still alive". */
function willOf(a) {
  if (a._will !== undefined) return a._will;
  const heir = defaultHeirOf(a);
  const setsOne = seeded01(a.id, 'will') < 0.28 && heir;
  if (!setsOne) return (a._will = null);
  // A named heir who is not the default is the whole point of allowing one.
  const kin = (a.rel || []).map(r => AGENT_BY_ID[r.id]).filter(h => h && isAlive(h));
  const alt = kin.length ? kin[Math.floor(seeded01(a.id, 'willheir') * kin.length)] : heir;
  return (a._will = {
    heir: alt || heir,
    tiles: tilesOwnedBy(a).length,
    note: 'Property and savings to a named heir, overriding the default.',
  });
}





// ---- births --------------------------------------------------------------
// DESIGN.md section 5 pairs death with birth; world/life/ owns marriage and
// birth for real. This is the viewer's model of it: a married couple, both
// alive, the mother of childbearing age, roll each day for a child. A newborn
// is a genuine new resident - added to AGENTS, given the household's surname
// and both parents as kin, and counted in the population from that day on.
const BIRTH_MIN_AGE = 18;
const BIRTH_MAX_AGE = 45;
const DAILY_BIRTH_CHANCE = 0.004;   // ASSUMPTION - DESIGN.md pins no rate
const BABY_NAMES_M = ['Bo', 'Cato', 'Dov', 'Eli', 'Finn', 'Gus', 'Hal', 'Ivo', 'Jem', 'Kit', 'Lev', 'Mik', 'Nils', 'Ove', 'Pax', 'Rue', 'Sig', 'Tov'];
const BABY_NAMES_F = ['Ana', 'Bea', 'Cleo', 'Dot', 'Eve', 'Fay', 'Gia', 'Hana', 'Ida', 'Juno', 'Kit', 'Lia', 'Mae', 'Nell', 'Ola', 'Pia', 'Rae', 'Sia'];
let birthCount = 0;
const birthLog = [];

function makeNewborn(mother, father, day) {
  const surname = (mother.n.split(' ')[1]) || 'Kasukabe';
  const salt = mother.id + '|' + day + '|' + birthCount;
  const male = seeded01(salt, 'sex') > 0.5;
  const pool = male ? BABY_NAMES_M : BABY_NAMES_F;
  const used = new Set(AGENTS.map(a => a.n));
  let name = '';
  for (let i = 0; i < pool.length * 3; i++) {
    const cand = pool[(hash(salt + i) >>> 0) % pool.length] + ' ' + surname;
    if (!used.has(cand)) { name = cand; break; }
  }
  if (!name) name = 'Child ' + (birthCount + 1) + ' ' + surname;

  const baby = {
    id: 'b' + String(++birthCount).padStart(4, '0'),
    n: name, rn: name + ' (born in Kasukabe)',
    age: 0, sex: male ? 'm' : 'f', occ: 'child', br: 'minor', dz: 'park',
    hh: mother.hh, ms: 'single', sp: null, nk: 0,
    pn: [mother.n, father.n].filter(Boolean),
    bal: 0, usd: 200,
    trait: 'newborn', habit: 'sleeps most of the day',
    bio: name + ' was born in Kasukabe on day ' + day + ', the child of ' +
         [mother.n, father.n].filter(Boolean).join(' and ') + '.',
    tension: '', fear: '', ambition: '', values: '', limit: '', scarcity: '',
    rel: [], mem: [], exp: {},
    bornDay: day,
  };
  // Kinship, both directions, so the family graph stays consistent.
  // Siblings are gathered across BOTH parents into a set first, then written
  // once. Writing them inside the parent loop pushed each existing child twice
  // - once per parent - and the duplicate landed on the sibling's own record,
  // where a later dedupe of the newborn's list would never have caught it.
  const siblingIds = new Set();
  for (const parent of [mother, father]) {
    if (!parent) continue;
    baby.rel.push({ r: parent.sex === 'm' ? 'father' : 'mother', id: parent.id, n: parent.n, s: 'genesis' });
    parent.rel = parent.rel || [];
    parent.rel.push({ r: male ? 'son' : 'daughter', id: baby.id, n: baby.n, s: 'genesis' });
    parent.nk = (parent.nk || 0) + 1;
    for (const r of parent.rel) {
      if ((r.r === 'son' || r.r === 'daughter') && r.id !== baby.id) siblingIds.add(r.id);
    }
  }
  for (const sibId of siblingIds) {
    const sib = AGENT_BY_ID[sibId];
    if (!sib) continue;
    baby.rel.push({ r: sib.sex === 'm' ? 'brother' : 'sister', id: sib.id, n: sib.n, s: 'genesis' });
    sib.rel = sib.rel || [];
    sib.rel.push({ r: male ? 'brother' : 'sister', id: baby.id, n: baby.n, s: 'genesis' });
  }
  initAgentDerived(baby);
  AGENTS.push(baby);
  AGENT_BY_ID[baby.id] = baby;
  healthSim.states[baby.id] = { status: 'healthy', ailment: null, since: day, until: 0, deathDay: null, cause: null, alive: true };
  birthLog.unshift({ day, name: baby.n, id: baby.id, mother: mother.n, father: father ? father.n : null });
  return baby;
}

function stepBirths(day) {
  // Snapshot first: makeNewborn pushes onto AGENTS, and iterating an array
  // while appending to it would let a newborn be considered for parenthood on
  // the day it was born.
  const candidates = AGENTS.filter(a =>
    isAlive(a) && a.sex === 'f' && a.ms === 'married' && a.sp &&
    a.age >= BIRTH_MIN_AGE && a.age <= BIRTH_MAX_AGE);
  for (const mother of candidates) {
    if (seeded01(mother.id, 'birth' + day) >= DAILY_BIRTH_CHANCE) continue;
    const father = AGENTS.find(x => x.n === mother.sp && isAlive(x));
    if (!father) continue;
    makeNewborn(mother, father, day);
  }
}

// ---- the government budget ----------------------------------------------
// The treasury was previously income-only: tax came in and nothing ever went
// out, so it grew without bound. A real government pays for things. Every
// outgoing below has a basis in the repo:
//   - Government jobs carry a salary paid from treasury
//     (world/charter/models.py, Job.salary_sim; DESIGN.md section 6's
//     "police recruited on treasury salary").
//   - Pensions are paid from treasury at 25% of last salary for private-sector
//     retirees and 50% for government ones, from age 70
//     (world/ledger/money.py: PENSION_MINIMUM_AGE / PENSION_*_PCT).
//   - Buildings the town puts up have to be paid for out of the same pot.
const PENSION_MINIMUM_AGE = 70;
const PENSION_PRIVATE_PCT = 25;
const PENSION_GOVERNMENT_PCT = 50;

// Seat salaries. DESIGN.md gives the government the power to set jobs and
// their pay but pins no number, so these are ASSUMPTIONS, flagged on the page.
const PM_SALARY_SIM = 260;
const MINISTER_SALARY_SIM = 200;
const POLICE_SALARY_SIM = 130;
const POLICE_FORCE_SIZE = 6;
const GOV_CLERK_SALARY_SIM = WAGE.government_clerk;   // 145, same as their wage

// Every institution stands from the first tick, so its capital cost was a
// founding investment rather than a charge on the running treasury. What the
// treasury pays instead is DAILY UPKEEP - staffing, heat, repairs - which is
// the government spending money continuously on the town rather than only
// collecting tax. Figures are ASSUMPTIONS: DESIGN.md gives the government
// power over jobs, permits and buildings but pins no upkeep number.
const BUILD_UPKEEP_SIM = {
  hospital: 900, police: 620, fire: 500, school: 560, library: 240,
  bakery: 120, restaurant: 160, bank: 200, inn: 140,
};
const CAPITAL_VALUE_SIM = {
  bakery: 18000, restaurant: 22000, school: 60000, hospital: 140000,
  police: 90000, library: 45000, fire: 75000, bank: 50000, inn: 30000,
};

/** Who sits in government. The charter seats 1 PM + 4 ministers
 * (world/charter/models.py: pm_seats=1, minister_seats=4). Genesis records no
 * election, so the seats are filled deterministically from the clerks - the
 * residents who actually work in the government complex. */
function seatHolders() {
  const clerks = AGENTS.filter(a => a.occ === 'government_clerk' && isAlive(a));
  const pool = clerks.length >= 5 ? clerks : AGENTS.filter(isAlive);
  const picked = pool.slice().sort((x, y) => hash(x.id + 'seat') - hash(y.id + 'seat')).slice(0, 5);
  return picked.map((a, i) => ({
    agent: a,
    role: i === 0 ? 'Prime Minister' : 'Minister',
    salary: i === 0 ? PM_SALARY_SIM : MINISTER_SALARY_SIM,
  }));
}

/** Income minus recurring costs for one day, ignoring construction. The build
 * scheduler needs this before the schedule exists, so it cannot call
 * budgetForDay (which asks the schedule what was built). */
function dailyNetBeforeBuilding(day) {
  const aliveCount = AGENTS.filter(isAlive).length || POP;
  const seats = PM_SALARY_SIM + 4 * MINISTER_SALARY_SIM;
  const clerks = AGENTS.filter(a => isAlive(a) && a.occ === 'government_clerk').length * GOV_CLERK_SALARY_SIM;
  let pensions = 0;
  for (const a of AGENTS) {
    if (!isAlive(a) || a.age < PENSION_MINIMUM_AGE) continue;
    pensions += Math.round((WAGE[a.occ] || WAGE.day_laborer) *
      (a.occ === 'government_clerk' ? PENSION_GOVERNMENT_PCT : PENSION_PRIVATE_PCT) / 100);
  }
  const upkeep = Object.values(BUILD_UPKEEP_SIM).reduce((a, b) => a + b, 0);
  return aliveCount * HEAD_TAX_PER_DAY - seats - clerks - pensions - upkeep;
}

/** The day's income and outgoings, in sim. */
function budgetForDay(day) {
  const alive = AGENTS.filter(isAlive);
  const taxIncome = alive.length * HEAD_TAX_PER_DAY;

  const seats = seatHolders();
  const seatPay = seats.reduce((sum, s) => sum + s.salary, 0);
  const clerks = alive.filter(a => a.occ === 'government_clerk').length;
  const clerkPay = clerks * GOV_CLERK_SALARY_SIM;
  const police = isBuilt('police', day) ? POLICE_FORCE_SIZE * POLICE_SALARY_SIM : 0;

  // Pensions: everyone at or over the eligibility age draws from treasury.
  let pensions = 0, pensioners = 0;
  for (const a of alive) {
    if (a.age < PENSION_MINIMUM_AGE) continue;
    pensioners++;
    const govJob = a.occ === 'government_clerk';
    const lastSalary = WAGE[a.occ] || WAGE.day_laborer;
    pensions += Math.round(lastSalary * (govJob ? PENSION_GOVERNMENT_PCT : PENSION_PRIVATE_PCT) / 100);
  }

  const upkeep = Object.values(BUILD_UPKEEP_SIM).reduce((a, b) => a + b, 0);

  const spend = seatPay + clerkPay + police + pensions + upkeep;
  return { day, taxIncome, seatPay, clerkPay, police, pensions, pensioners, upkeep,
           spend, net: taxIncome - spend, seats, clerks };
}

/** Treasury balance after `day` days of income and spending. Walks the days
 * rather than closing the form, because pensioner and population counts change
 * as people die - and it is only a few hundred iterations. */
function treasuryOn(hourFrac) {
  const { balance } = fiscal();
  const day = Math.min(FISCAL_HORIZON_DAYS - 1, Math.max(0, Math.floor(hourFrac / 24)));
  // Interpolate across the day so the figure ticks over smoothly rather than
  // jumping once every 24 hours.
  const frac = (hourFrac - Math.floor(hourFrac / 24) * 24) / 24;
  return Math.round(balance[day] + (balance[day + 1] - balance[day]) * frac);
}

function renderTreasury(hourFrac) {
  const el = document.getElementById('treasuryBody');
  if (!el) return;
  const day = Math.floor(hourFrac / 24);
  const b = budgetForDay(day);
  const bal = treasuryOn(hourFrac);
  // Cumulative totals come from the one-pass fiscal history, not from
  // re-walking budgetForDay for every day up to now - that was O(days x
  // population) on every day boundary and stalled the page by day ~100.
  const f = fiscal();
  const cumIn = f.cumIn[Math.min(day, FISCAL_HORIZON_DAYS)];
  const cumOut = f.cumOut[Math.min(day, FISCAL_HORIZON_DAYS)];
  const row = (label, v, cls) =>
    '<div class="bud-row ' + (cls || '') + '"><span>' + label + '</span>' +
    '<span class="bud-v">' + (v >= 0 ? '+' : '−') + Math.abs(v).toLocaleString() + '</span></div>';

  el.innerHTML =
    '<div class="score-grid">' +
      '<div class="score-stat"><span class="sv">' + bal.toLocaleString() + '</span><span class="sk">treasury balance</span></div>' +
      '<div class="score-stat good"><span class="sv">' + cumIn.toLocaleString() + '</span><span class="sk">total tax collected</span></div>' +
      '<div class="score-stat ' + (cumOut ? 'warn' : '') + '"><span class="sv">' + cumOut.toLocaleString() + '</span><span class="sk">total spent</span></div>' +
      '<div class="score-stat ' + (b.net < 0 ? 'crit' : 'good') + '"><span class="sv">' +
        (b.net >= 0 ? '+' : '') + b.net.toLocaleString() + '</span><span class="sk">net today</span></div>' +
    '</div>' +
    '<div class="section-lbl">Today’s budget — day ' + day + '</div>' +
    '<div class="bud-list">' +
      row('Head tax · ' + AGENTS.filter(isAlive).length + ' residents × ' + HEAD_TAX_PER_DAY, b.taxIncome, 'in') +
      row('Elected seats · 1 PM + 4 ministers', -b.seatPay, 'out') +
      row('Civil service · ' + b.clerks + ' clerks', -b.clerkPay, 'out') +
      (b.police ? row('Police · ' + POLICE_FORCE_SIZE + ' officers', -b.police, 'out') : '') +
      row('Pensions · ' + b.pensioners + ' retirees (70+)', -b.pensions, 'out') +
      row('Building upkeep · ' + Object.keys(BUILD_UPKEEP_SIM).length + ' institutions', -b.upkeep, 'out') +
    '</div>' +
    '<div class="section-lbl">Who draws a salary from the treasury</div>' +
    '<div class="seat-list">' + b.seats.map(sd =>
      '<button class="seat-row" data-id="' + sd.agent.id + '">' +
      '<span class="seat-role">' + (sd.role === 'Prime Minister' ? '⭐' : '🏛️') + ' ' + sd.role + '</span>' +
      '<span class="seat-name">' + sd.agent.n + '</span>' +
      '<span class="seat-pay">' + sd.salary + '/day</span></button>').join('') + '</div>' +
    '<p class="rel-note">Head tax, pension rates and the pension age are real constants ' +
    '(world/ledger/*). Seat and police salaries are not pinned anywhere in DESIGN.md, so the ' +
    'figures above are assumptions — the government can set jobs and pay, but no number is specified.</p>';
  el.querySelectorAll('.seat-row').forEach(r =>
    r.addEventListener('click', () => selectAgent(r.dataset.id)));
}

// ---- canvas + camera ----------------------------------------------------
const canvas = document.getElementById('village');
const ctx = canvas ? canvas.getContext('2d') : null;
const camera = { zoom: 1, ox: 0, oy: 0, drag: null };
let liveAgentPos = {};   // id -> { tx, ty, zone, sx, sy }
let zoneCounts = {};
let selectedId = null;
let cardEls = null;

function resizeCanvas() {
  if (!canvas) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = canvas.clientWidth || 1100;
  const h = canvas.clientHeight || 660;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

/** The tile rectangle currently visible, used to cull the ground and prop
 * passes. Inverts the isometric projection at the four screen corners:
 *   u = tx - ty = (sx - ox) / (TILE_W/2 * zoom)
 *   v = tx + ty = (sy - oy) / (TILE_H/2 * zoom)
 * so tx = (u + v)/2 and ty = (v - u)/2. A margin is added because buildings
 * are drawn above their own ground tile and would otherwise pop at the edge.
 */
// The ground is ~9,800 tiles and does not move. Redrawing it every frame cost
// ~68,000 canvas operations and held the page to 25fps once the map grew.
// Instead it is painted into an offscreen canvas and blitted, and only
// repainted when the camera actually changes - plus occasionally, so the
// river shimmer keeps animating rather than freezing.
const groundCache = { canvas: null, ctx: null, key: '', frame: 0 };
const GROUND_REPAINT_EVERY = 24;    // frames, keeps the water and lamps alive

function drawCachedScene(ctx, t, view, w, h, lightsOn) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  if (!groundCache.canvas) {
    groundCache.canvas = document.createElement('canvas');
    groundCache.ctx = groundCache.canvas.getContext('2d');
  }
  const need = Math.round(w * dpr) + 'x' + Math.round(h * dpr);
  if (groundCache.canvas.width !== Math.round(w * dpr) ||
      groundCache.canvas.height !== Math.round(h * dpr)) {
    groundCache.canvas.width = Math.round(w * dpr);
    groundCache.canvas.height = Math.round(h * dpr);
    groundCache.key = '';
  }
  const key = need + '|' + camera.zoom.toFixed(4) + '|' +
              Math.round(camera.ox) + '|' + Math.round(camera.oy) + '|' + lightsOn;
  groundCache.frame++;
  if (key !== groundCache.key || groundCache.frame % GROUND_REPAINT_EVERY === 0) {
    groundCache.key = key;
    const g = groundCache.ctx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.save();
    g.translate(camera.ox, camera.oy);
    g.scale(camera.zoom, camera.zoom);
    drawGround(g, t, view);
    drawProps(g, t, lightsOn, view);
    g.restore();
  }
  // Blitted in screen space, so the caller's world transform must not apply.
  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.drawImage(groundCache.canvas, 0, 0, w, h);
  ctx.restore();
}

function viewBounds(w, h) {
  // Back to isometric, so inverting the projection means undoing the u/v
  // rotation at the four screen corners:
  //   u = tx - ty = (sx - ox) / (TILE_W/2 * zoom)
  //   v = tx + ty = (sy - oy) / (TILE_H/2 * zoom)
  // giving tx = (u + v)/2 and ty = (v - u)/2. The margin covers buildings and
  // trees, which are drawn well above their own ground tile.
  const hw = (TILE_W / 2) * camera.zoom;
  const hh = (TILE_H / 2) * camera.zoom;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [sx, sy] of [[0, 0], [w, 0], [0, h], [w, h]]) {
    const u = (sx - camera.ox) / hw;
    const v = (sy - camera.oy) / hh;
    const tx = (u + v) / 2, ty = (v - u) / 2;
    minX = Math.min(minX, tx); maxX = Math.max(maxX, tx);
    minY = Math.min(minY, ty); maxY = Math.max(maxY, ty);
  }
  const pad = 5;
  return {
    minX: Math.floor(minX - pad), maxX: Math.ceil(maxX + pad),
    minY: Math.floor(minY - pad), maxY: Math.ceil(maxY + pad + 4),
  };
}

function worldToScreen(tx, ty) {
  return {
    sx: (tx - ty) * (TILE_W / 2) * camera.zoom + camera.ox,
    sy: (tx + ty) * (TILE_H / 2) * camera.zoom + camera.oy,
  };
}
/** The zoom at which the whole grid fits the viewport. In this projection the
 * grid spans x from -GRID_H*TILE_W/2 to +GRID_W*TILE_W/2, and y from 0 to
 * (GRID_W+GRID_H)*TILE_H/2; the extra height is headroom for buildings, which
 * stick up above their own ground tile. */
function fitZoom() {
  const w = canvas.clientWidth || 1100, h = canvas.clientHeight || 780;
  const spanX = (GRID_W + GRID_H) * (TILE_W / 2);
  const spanY = (GRID_W + GRID_H) * (TILE_H / 2) + 140;
  return Math.min(w / spanX, h / spanY) * 0.97;
}

/** Point the camera at a tile, at the current zoom. */
function lookAt(tx, ty) {
  const w = canvas.clientWidth || 1100, h = canvas.clientHeight || 780;
  camera.ox = w / 2 - (tx - ty) * (TILE_W / 2) * camera.zoom;
  camera.oy = h / 2 - (tx + ty) * (TILE_H / 2) * camera.zoom;
}

/** Fit the entire village in view - what the Recentre button does. */
function fitCamera() {
  if (!canvas) return;
  camera.zoom = fitZoom();
  lookAt(GRID_W / 2, GRID_H / 2);
  updateZoomLabel();
}

/** The opening view. Fitting all 3,328 tiles makes every building too small to
 * read, so open closer in on the town centre and let the reader zoom out. */
/** Show the zoom as a percentage of the fit-the-whole-town zoom, so 100% means
 * "the whole village is on screen" rather than an arbitrary scale number. */
function updateZoomLabel() {
  const el = document.getElementById('zoomLevel');
  if (!el || !canvas) return;
  const fz = fitZoom() || 1;
  el.textContent = Math.round((camera.zoom / fz) * 100) + '%';
}

function centreCamera() {
  if (!canvas) return;
  camera.zoom = Math.min(1.05, Math.max(0.4, fitZoom() * 1.9));
  lookAt(GRID_W / 2 - 4, GRID_H / 2 - 4);
  updateZoomLabel();
}

// ---- the render pass ----------------------------------------------------
function render(t, hourFrac) {
  if (!ctx) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const hod = hourFrac % 24;
  const lightsOn = hod >= 18 || hod < 6.5;

  // Sky behind the village.
  const sky = ctx.createLinearGradient(0, 0, 0, h);
  if (lightsOn) { sky.addColorStop(0, '#22355C'); sky.addColorStop(1, '#3E5A85'); }
  else { sky.addColorStop(0, '#7FC9F0'); sky.addColorStop(1, '#C4EBFB'); }
  ctx.fillStyle = sky; ctx.fillRect(0, 0, w, h);

  const view = viewBounds(w, h);
  // Ground, props AND buildings all go in one cached layer: none of them
  // move, and in the isometric view redrawing them per frame means ~11,500
  // ground diamonds plus 643 furniture boxes plus 2,200 props every 16ms,
  // which stalled the page outright.
  drawCachedScene(ctx, t, view, w, h, lightsOn);

  ctx.save();
  ctx.translate(camera.ox, camera.oy);
  ctx.scale(camera.zoom, camera.zoom);

  // Residents, painted back-to-front with the props already down. Sorting by
  // depth keeps a person behind a house hidden by it.
  // Isometric: depth is tx + ty, so someone closer to the camera draws last.
  const ordered = AGENTS.slice().sort((a, b) => {
    const pa = liveAgentPos[a.id], pb = liveAgentPos[b.id];
    return (pa.tx + pa.ty) - (pb.tx + pb.ty);
  });
  for (const a of ordered) {
    const p = liveAgentPos[a.id];
    const sx = (p.tx - p.ty) * (TILE_W / 2), sy = (p.tx + p.ty) * (TILE_H / 2);
    if (p.dead) continue;
    // Roofs on: anyone indoors is hidden by the roof, exactly as they would
    // be. Without this they drew over the roof slab and appeared to be
    // standing on it. Lift the roofs and they reappear in their rooms.
    if (typeof ROOFS_ON !== 'undefined' && ROOFS_ON && indoors(p)) continue;
    const working = p.atWork;   // tool shows while walking between tasks too
    drawPerson(ctx, sx, sy, a.shirt, a.hair, a.id === selectedId,
               !!talkingNow[a.id], p.moving, t, working ? WORK_TOOL[a.occ] : null, a.outfit);
  }

  drawBirds(ctx, t);

  // Live speech bubbles sit above everything so they are never occluded.
  const bubbles = activeChats
    .filter(c => liveAgentPos[c.from])
    .sort((x, y) => (x.from === selectedId ? -1 : y.from === selectedId ? 1 : 0))
    .slice(0, MAX_BUBBLES);
  for (const c of bubbles) {
    const p = liveAgentPos[c.from];
    drawBubble(ctx, (p.tx - p.ty) * (TILE_W / 2), (p.tx + p.ty) * (TILE_H / 2) - 46, c.text);
  }

  ctx.restore();
  drawZoneLabels(ctx, w, h);
  nightTint(ctx, w, h, hod);
}

/** A name plaque over every district, so the reader can tell the hospital from
 * the fire station without clicking anything.
 *
 * Drawn AFTER ctx.restore(), i.e. in screen space rather than world space, so
 * the text stays the same readable size at every zoom instead of shrinking
 * into the tiles when you zoom out. Plaques outside the viewport are skipped,
 * and they fade out below a zoom where the map is too small to label.
 */
function drawZoneLabels(ctx, w, h) {
  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // Biggest districts first, so when plaques compete for room the major ones
  // win. A fixed zoom cutoff was wrong: fitting the whole town sits at ~0.21
  // zoom, which hid every label at exactly the moment they are most useful.
  // Instead each plaque is skipped only if it would collide with one already
  // placed, which self-regulates at any zoom.
  const order = ZONE_ORDER
    .filter(z => z !== 'housingEast')
    .map(z => ({ z, area: ZONES[z].w * ZONES[z].h }))
    .sort((a, b) => b.area - a.area);

  const placed = [];
  const hits = (a) => placed.some(b =>
    a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y);

  for (const { z: zid } of order) {
    const z = ZONES[zid];
    const p = worldToScreen(z.x + z.w / 2, z.y + z.h / 2);
    const y = p.sy - 14;
    if (p.sx < -160 || p.sx > w + 160 || y < -40 || y > h + 40) continue;

    const label = z.label.toUpperCase();
    ctx.font = '800 12px Nunito, system-ui, sans-serif';
    const tw = ctx.measureText(label).width;
    const padX = 11, iconW = 17;
    const boxW = tw + padX * 2 + iconW, boxH = 23;
    const box = { x: p.sx - boxW / 2, y: y - boxH / 2, w: boxW, h: boxH + 7 };
    if (hits(box)) continue;
    placed.push(box);

    ctx.fillStyle = 'rgba(255,255,255,.94)';
    ctx.strokeStyle = 'rgba(30,60,40,.30)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(box.x, box.y, boxW, boxH, 11);
    ctx.fill(); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(p.sx - 5, y + boxH / 2);
    ctx.lineTo(p.sx, y + boxH / 2 + 6);
    ctx.lineTo(p.sx + 5, y + boxH / 2);
    ctx.closePath();
    ctx.fillStyle = 'rgba(255,255,255,.94)'; ctx.fill();

    ctx.font = '13px system-ui, sans-serif';
    ctx.fillText(z.icon, box.x + padX, y + 1);
    ctx.font = '800 12px Nunito, system-ui, sans-serif';
    ctx.fillStyle = '#1B2A20';
    ctx.fillText(label, p.sx + iconW / 2, y + 1);
  }
  ctx.restore();
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
}

function drawBubble(c, x, y, text) {
  c.font = '600 12px Nunito, sans-serif';
  const maxW = 190;
  const words = text.split(' ');
  const lines = [];
  let line = '';
  for (const word of words) {
    const test = line ? line + ' ' + word : word;
    if (c.measureText(test).width > maxW && line) { lines.push(line); line = word; }
    else line = test;
  }
  if (line) lines.push(line);
  const wBox = Math.min(maxW, Math.max(...lines.map(l => c.measureText(l).width))) + 18;
  const hBox = lines.length * 15 + 12;
  const bx = x - wBox / 2, by = y - hBox;
  c.fillStyle = 'rgba(255,255,255,.97)';
  c.strokeStyle = 'rgba(40,60,80,.22)'; c.lineWidth = 1.5;
  c.beginPath(); c.roundRect(bx, by, wBox, hBox, 9); c.fill(); c.stroke();
  c.beginPath();
  c.moveTo(x - 6, by + hBox); c.lineTo(x, by + hBox + 9); c.lineTo(x + 6, by + hBox);
  c.closePath(); c.fillStyle = 'rgba(255,255,255,.97)'; c.fill();
  c.fillStyle = '#25313F'; c.textAlign = 'center';
  lines.forEach((l, i) => c.fillText(l, x, by + 17 + i * 15));
  c.textAlign = 'left';
}

// ---- positions ----------------------------------------------------------
/** Is this position inside any building footprint? */
function indoors(p) {
  for (const b of plans) {
    if (p.tx >= b.x && p.tx < b.x + b.w && p.ty >= b.y && p.ty < b.y + b.h) return true;
  }
  return false;
}

function updatePositions(hourFrac) {
  const counts = {};
  for (const a of AGENTS) {
    const { pos, zone, moving, dead, ill, onShift: shiftNow, offDuty } = agentPosition(a, hourFrac);
    const sc = worldToScreen(pos.tx, pos.ty);
    const atWork = !dead && !ill && zone === a.dayZone &&
                   (a.shift ? onShift(a, hourFrac % 24)
                            : phaseAt(Math.floor(hourFrac % 24)) === 'day');
    liveAgentPos[a.id] = { tx: pos.tx, ty: pos.ty, zone, moving, dead: !!dead, ill: !!ill,
                           atWork, onShift: !!shiftNow, offDuty: !!offDuty, sx: sc.sx, sy: sc.sy };
    if (!dead) counts[zone] = (counts[zone] || 0) + 1;   // the living only
  }
  zoneCounts = counts;
}

// ---- live conversation --------------------------------------------------
// Residents at the same place talk to each other. Lines are drawn from
// templates keyed to the speaker's occupation and their real persona fields,
// so a trader haggles and a clerk talks about tax - the words are generated
// here, but who says them, to whom, and where is driven by real data.
const LINES = {
  greet: [
    'Morning. Cold one today.', 'You are about early.', 'Good to see you.',
    'How is the family keeping?', 'Long day ahead?',
  ],
  trade: [
    'Grain moved again this morning — up two on yesterday.',
    'I can do better than that price if you take the lot.',
    'Hold off selling today. It will turn by evening.',
    'Coin is tight this week. Everyone is asking for credit.',
  ],
  work: [
    'The yard needs two more hands before the rain.',
    'Half a day more and the run is finished.',
    'My back is telling me it is nearly the weekend.',
  ],
  civic: [
    'The head tax came out of my balance again this morning.',
    'There is a petition at the complex if you want to sign it.',
    'They say the rate is up for amendment next term.',
  ],
  money: [
    'I am short until payday. Can it wait a week?',
    'I put a little aside. Not much, but a little.',
    'Upkeep takes it as fast as work brings it in.',
  ],
  family: [
    'The little one asked after you.',
    'We are all under one roof again this month.',
    'I will bring something back from the market.',
  ],
};
function lineFor(a, salt) {
  let bucket = 'greet';
  const r = seeded01(a.id + salt, 'bucket');
  if (a.occ === 'trader' || a.occ === 'grocery_store_owner' || a.occ === 'cafe_owner') bucket = r < 0.6 ? 'trade' : 'greet';
  else if (a.occ === 'government_clerk' || a.occ === 'office_administrator') bucket = r < 0.6 ? 'civic' : 'greet';
  else if (a.br === 'minor') bucket = r < 0.5 ? 'family' : 'greet';
  else if (a.br === 'elderly') bucket = r < 0.5 ? 'family' : 'money';
  else if (r < 0.35) bucket = 'work';
  else if (r < 0.6) bucket = 'money';
  else if (r < 0.8) bucket = 'greet';
  else bucket = 'trade';
  const pool = LINES[bucket];
  return pool[Math.floor(seeded01(a.id + salt, 'line') * pool.length)];
}

// Everyone's exchanges are logged, but only a handful get a bubble drawn -
// past roughly this many the map disappears under speech balloons.
const MAX_BUBBLES = 5;
let activeChats = [];       // [{from, to, text}]
let talkingNow = {};
let chatLog = [];
let lastChatHour = -1;

function tickConversations(hourFrac) {
  const hourKey = Math.floor(hourFrac * 2);   // a new exchange every half hour
  if (hourKey === lastChatHour) return;
  lastChatHour = hourKey;
  activeChats = [];
  talkingNow = {};

  const hod = hourFrac % 24;
  if (phaseAt(Math.floor(hod)) === 'night') return;   // the town is asleep

  // Group residents by where they actually are, then pair people up inside a
  // location: a conversation can only happen between two people in one place.
  const byZone = {};
  for (const a of AGENTS) {
    const p = liveAgentPos[a.id];
    if (!p || p.moving || p.dead) continue;   // the dead do not speak
    (byZone[p.zone] = byZone[p.zone] || []).push(a);
  }
  const clock = fmtClock(hourFrac);
  const day = Math.floor(hourFrac / 24) + 1;

  for (const zone of Object.keys(byZone)) {
    const here = byZone[zone];
    if (here.length < 2) continue;
    for (let k = 0; k < 1; k++) {
      const salt = zone + hourKey + k;
      const i = Math.floor(seeded01(salt, 'i') * here.length);
      const from = here[i];
      // Prefer someone the speaker actually knows. People talk to their own
      // family far more than to strangers, and it makes the kinship graph
      // visible in the feed instead of buried in the cards.
      const known = here.filter(o => o.id !== from.id && (from.rel || []).some(r => r.id === o.id));
      let to;
      if (known.length && seeded01(salt, 'pref') < 0.7) {
        to = known[Math.floor(seeded01(salt, 'k') * known.length)];
      } else {
        let j = Math.floor(seeded01(salt, 'j') * here.length);
        if (j === i) j = (j + 1) % here.length;
        to = here[j];
      }
      const text = lineFor(from, salt);
      activeChats.push({ from: from.id, to: to.id, text });
      talkingNow[from.id] = true;
      // Relationship, if any, makes the exchange legible as family or not.
      const rel = (from.rel || []).find(r => r.id === to.id);
      chatLog.unshift({
        day, clock, fromId: from.id, from: from.n, to: to.n, toId: to.id,
        text, place: ZONES[zone] ? ZONES[zone].label : zone,
        rel: rel ? rel.r : null, relSource: rel ? rel.s : null,
      });
    }
  }
  chatLog = chatLog.slice(0, 60);
  renderChat();
}

function renderChat() {
  const el = document.getElementById('chatLog');
  if (!el) return;
  if (!chatLog.length) {
    // Before 08:00 nobody has arrived anywhere yet, so an empty list here is
    // correct - but a blank panel reads as broken, so say which it is.
    el.innerHTML = '<p class="chat-empty">Nobody is talking yet — residents are still ' +
      'walking to work. Conversations start once people arrive somewhere.</p>';
    return;
  }
  el.innerHTML = chatLog.slice(0, 24).map(c => {
    const [emoji, colour] = occFace((AGENT_BY_ID[c.fromId] || {}).occ || '');
    return '<div class="chat-row">' +
      '<div class="chat-avatar" style="background:' + colour + '">' + emoji + '</div>' +
      '<div class="chat-body">' +
        '<div class="chat-head">' +
          '<button class="chat-who" data-id="' + c.fromId + '">' + c.from + '</button>' +
          '<span class="chat-arrow">→</span>' +
          '<button class="chat-who" data-id="' + c.toId + '">' + c.to + '</button>' +
          (c.rel ? '<span class="chat-rel' + (c.relSource === 'inferred' ? ' inferred' : '') + '">' + c.rel + '</span>' : '') +
        '</div>' +
        '<div class="chat-text">“' + c.text + '”</div>' +
        '<div class="chat-meta">day ' + c.day + ' · ' + c.clock + ' · ' + c.place + '</div>' +
      '</div></div>';
  }).join('');
  el.querySelectorAll('.chat-who').forEach(b =>
    b.addEventListener('click', () => selectAgent(b.dataset.id)));
}

// ---- panels -------------------------------------------------------------
function renderOccupancy() {
  const rows = ZONE_ORDER.map(z => ({ label: ZONES[z].label, icon: ZONES[z].icon, n: zoneCounts[z] || 0 }))
    .filter(r => r.n > 0).sort((a, b) => b.n - a.n).slice(0, 8);
  const max = Math.max(1, ...rows.map(r => r.n));
  const el = document.getElementById('occupancy');
  if (el) el.innerHTML = rows.map(r =>
    '<div class="occ-row"><span class="occ-name">' + r.icon + ' ' + r.label + '</span>' +
    '<span class="occ-bar"><span style="width:' + (r.n / max * 100).toFixed(0) + '%"></span></span>' +
    '<span class="occ-n">' + r.n + '</span></div>').join('');
}

function renderGovernment(hourFrac) {
  const treasury = treasuryOn(hourFrac);
  const hud = document.getElementById('hudTreasury');
  if (hud) hud.textContent = treasury.toLocaleString();
  const el = document.getElementById('govBody');
  if (!el) return;
  const clerks = AGENTS.filter(a => a.occ === 'government_clerk');
  el.innerHTML =
    '<div class="gov-fact"><span class="k">Charter seats</span><span class="v">1 PM + 4 Ministers</span></div>' +
    '<div class="gov-fact"><span class="k">Tax rate</span><span class="v">' + (TAX_RATE_BPS / 100).toFixed(1) + '%</span></div>' +
    '<div class="gov-fact"><span class="k">Head tax / person / day</span><span class="v">' + HEAD_TAX_PER_DAY + ' sim</span></div>' +
    '<div class="gov-fact"><span class="k">Collected today</span><span class="v">' + (HEAD_TAX_PER_DAY * POP).toLocaleString() + ' sim</span></div>' +
    '<div class="gov-fact"><span class="k">Treasury (running)</span><span class="v">' + treasury.toLocaleString() + ' sim</span></div>' +
    '<div class="gov-fact"><span class="k">Treasury (USD, genesis)</span><span class="v">$' + GENESIS_TREASURY_USD.toFixed(2) + '</span></div>' +
    '<div class="gov-fact"><span class="k">Land held by the town</span><span class="v">' +
      landRegister().government.toLocaleString() + ' units</span></div>' +
    '<div class="gov-fact"><span class="k">Land in private hands</span><span class="v">' +
      landRegister().private.toLocaleString() + ' units</span></div>' +
    '<div class="gov-fact"><span class="k">Wills on file</span><span class="v">' +
      AGENTS.filter(x => isAlive(x) && willOf(x)).length + ' of ' +
      AGENTS.filter(isAlive).length + '</span></div>' +
    '<div class="gov-fact"><span class="k">Came of age today</span><span class="v">' +
      cameOfAgeOn(Math.floor(hourFrac / 24)).length + '</span></div>' +
    '<div class="gov-fact"><span class="k">Pension private / govt</span><span class="v">25% / 50%</span></div>' +
    '<div class="gov-fact"><span class="k">Pension age</span><span class="v">70</span></div>' +
    '<ul class="rules-list">' +
      '<li>Every resident is taxed for existing, daily — adults and dependents alike.</li>' +
      '<li>Balances may never go negative; the ledger refuses an over-large debit.</li>' +
      '<li>Unpaid tax becomes a debt, and persistent debt goes to the complaints desk.</li>' +
      '<li>All business types are contestable — anyone may register a competitor.</li>' +
      '<li>At eighteen a resident funds their own thinking; the parent\'s bill for it ends.</li>' +
      '<li>Land divides into 1×1 units. Unclaimed land is the town\'s; every other unit has one owner.</li>' +
      '<li>An estate follows the owner\'s will if they set one, and the nearest living relative if not.</li>' +
      '<li>Tax, pension and credit-store margin are amendable by the sitting government.</li>' +
    '</ul>' +
    '<div class="section-lbl">Civil service (' + clerks.length + ' clerks)</div>' +
    '<div>' + clerks.map(c => '<span class="clerk-chip" data-id="' + c.id + '">' + c.n + '</span>').join('') + '</div>';
  el.querySelectorAll('.clerk-chip').forEach(c =>
    c.addEventListener('click', () => selectAgent(c.dataset.id)));
}

function renderScoreboard(hourFrac) {
  const el = document.getElementById('scoreBody');
  if (!el) return;
  const rows = AGENTS.map(a => ({ a, bal: balanceNow(a, hourFrac), spend: costSpent(a, hourFrac) }));
  const total = rows.reduce((s, r) => s + r.bal, 0);
  const spend = rows.reduce((s, r) => s + r.spend, 0);
  const broke = rows.filter(r => r.bal < 1000).length;
  const richest = rows.slice().sort((x, y) => y.bal - x.bal).slice(0, 5);
  const poorest = rows.slice().sort((x, y) => x.bal - y.bal).slice(0, 5);
  const mean = Math.round(total / rows.length);
  const sorted = rows.map(r => r.bal).sort((x, y) => x - y);
  const median = sorted[Math.floor(sorted.length / 2)];

  const hudSpend = document.getElementById('hudSpend');
  if (hudSpend) hudSpend.textContent = '$' + spend.toFixed(2);
  // The cap is the sum of every resident's real $2 wallet, so it grows with
  // the population instead of staying pinned at the genesis 250's $500.
  const capEl = document.getElementById('hudSpendCap');
  if (capEl) capEl.textContent = '$' + (AGENTS.reduce((t, a) => t + a.usd, 0) / 100).toFixed(2);
  const hudWealth = document.getElementById('hudWealth');
  if (hudWealth) hudWealth.textContent = total.toLocaleString();

  const line = (label, list) => '<div class="score-list"><div class="section-lbl">' + label + '</div>' +
    list.map(r =>
      '<div class="score-row" data-id="' + r.a.id + '">' +
      '<span class="score-name">' + r.a.n + '</span>' +
      '<span class="score-occ">' + OCC_LABEL(r.a.occ) + '</span>' +
      '<span class="score-bal">' + r.bal.toLocaleString() + '</span></div>').join('') + '</div>';

  el.innerHTML =
    '<div class="score-grid">' +
      '<div class="score-stat"><span class="sv">' + total.toLocaleString() + '</span><span class="sk">total sim in the town</span></div>' +
      '<div class="score-stat"><span class="sv">' + mean.toLocaleString() + '</span><span class="sk">mean balance</span></div>' +
      '<div class="score-stat"><span class="sv">' + median.toLocaleString() + '</span><span class="sk">median balance</span></div>' +
      '<div class="score-stat"><span class="sv">$' + spend.toFixed(2) + '</span><span class="sk">cognition spent / $500.00</span></div>' +
      '<div class="score-stat ' + (broke ? 'warn' : '') + '"><span class="sv">' + broke + '</span><span class="sk">under 1,000 sim</span></div>' +
      '<div class="score-stat"><span class="sv">' + HEAD_TAX_PER_DAY * POP + '</span><span class="sk">tax collected / day</span></div>' +
    '</div>' + line('Wealthiest', richest) + line('Tightest', poorest);
  el.querySelectorAll('.score-row').forEach(r =>
    r.addEventListener('click', () => selectAgent(r.dataset.id)));
}

// ---- inspector ----------------------------------------------------------
let inspectorTab = 'profile';

// Expert-reflection routing (Park et al. 2024, SM 3). Their classifier is an
// LLM call; keyword matching is enough to show the retrieval step and is
// labelled as such on the page.
let expertQuestion = 'How would you vote on raising the tax rate?';
const EXPERT_KEYS = {
  psychologist: ['feel', 'fear', 'trust', 'angry', 'happy', 'personality', 'friend', 'believe'],
  behavioral_economist: ['money', 'buy', 'sell', 'price', 'pay', 'save', 'cost', 'trade', 'debt', 'wage'],
  political_scientist: ['vote', 'tax', 'law', 'policy', 'government', 'charter', 'election', 'petition'],
  demographer: ['age', 'household', 'family', 'where', 'who', 'occupation', 'live'],
};
function routeExpert(q) {
  const s = (q || '').toLowerCase();
  let best = 'demographer', score = 0;
  for (const [expert, keys] of Object.entries(EXPERT_KEYS)) {
    const n = keys.reduce((acc, k) => acc + (s.includes(k) ? 1 : 0), 0);
    if (n > score) { best = expert; score = n; }
  }
  return best;
}

function familyLine(a) {
  if (a.br === 'minor') return a.pn && a.pn.length ? 'Child of ' + a.pn.join(' & ') : 'Ward of the household';
  const bits = [];
  if (a.ms === 'married' && a.sp) bits.push('Married to ' + a.sp);
  else bits.push(a.ms.charAt(0).toUpperCase() + a.ms.slice(1));
  if (a.nk > 0) bits.push(a.nk + ' child' + (a.nk > 1 ? 'ren' : ''));
  return bits.join(' · ');
}

function renderInspector(a, hourFrac) {
  const el = document.getElementById('modalBody');
  if (!el) return;
  if (!a) {
    el.innerHTML = '<div class="inspector-empty">Click any resident on the map, or any card below, to open their file.</div>';
    return;
  }
  const p = liveAgentPos[a.id] || {};
  const zone = ZONES[p.zone] ? ZONES[p.zone].label : '—';
  const [emoji, colour] = occFace(a.occ);
  const bal = balanceNow(a, hourFrac);
  const spent = costSpent(a, hourFrac);
  const cap = a.usd / 100;
  const pct = Math.min(100, spent / cap * 100);
  const unlocked = hourFrac >= a.skillUnlockDay * 24;

  const tabs = ['profile', 'family', 'estate', 'memory', 'experts', 'money']
    .map(t => '<button class="tab' + (t === inspectorTab ? ' on' : '') + '" data-tab="' + t + '">' +
              t.charAt(0).toUpperCase() + t.slice(1) + '</button>').join('');

  let body = '';
  if (inspectorTab === 'profile') {
    const hs = healthOf(a);
    const statusCls = !hs.alive ? 'dead' : hs.ailment ? (hs.ailment === 'terminal' ? 'crit' : 'ill') : 'ok';
    body =
      '<div class="status-line ' + statusCls + '">' +
        '<span class="status-dot"></span>' +
        '<b>' + (hs.alive ? 'Alive' : 'Deceased') + '</b>' +
        '<span class="status-detail">' + (hs.alive
          ? HEALTH_LABEL[hs.ailment || 'healthy'] + ' · work capacity ' + (workCapacity(a) * 100).toFixed(0) + '%'
          : 'died day ' + (hs.deathDay || '—') + ' · ' + (hs.cause || 'unknown')) + '</span>' +
      '</div>' +
      (hs.alive
        ? '<div class="now-box"><span class="now-verb">' + activityFor(a, p.zone || 'housing', hourFrac % 24) + '</span>' +
          '<span class="now-at">at ' + zone + '</span></div>'
        : '<div class="now-box dead"><span class="now-verb">No longer in the town</span>' +
          '<span class="now-at">revival costs ' + REVIVAL_COST_SIM.toLocaleString() + ' sim</span></div>') +
      '<div class="section-lbl">Description</div><p class="agent-bio">' + a.bio + '</p>' +
      (a.tension ? '<div class="agent-quote"><span class="lbl">Internal tension</span>' + a.tension + '</div>' : '') +
      (a.fear ? '<div class="agent-quote"><span class="lbl">Fear</span>' + a.fear + '</div>' : '') +
      (a.ambition ? '<div class="agent-quote"><span class="lbl">Ambition</span>' + a.ambition + '</div>' : '') +
      (a.limit ? '<div class="agent-quote"><span class="lbl">Will not compromise</span>' + a.limit + '</div>' : '') +
      '<div class="section-lbl">Skills</div><div>' +
        '<span class="skill-chip">⚙️ ' + a.skillBase + '</span>' +
        '<span class="skill-chip ' + (unlocked ? '' : 'locked') + '">' +
          (unlocked ? '✨ ' + a.skillUnlocked : '🔒 unlocks day ' + a.skillUnlockDay) + '</span></div>' +
      '<dl class="facts">' +
        '<div><dt>Trait</dt><dd>' + a.trait + '</dd></div>' +
        '<div><dt>Habit</dt><dd>' + a.habit + '</dd></div>' +
        '<div><dt>Workplace</dt><dd>' + (ZONES[a.dayZone] ? ZONES[a.dayZone].label : '—') + '</dd></div>' +
      '</dl>';
  } else if (inspectorTab === 'family') {
    const g = (a.rel || []).filter(r => r.s === 'genesis');
    const inf = (a.rel || []).filter(r => r.s === 'inferred');
    const row = r => '<button class="rel-row" data-id="' + r.id + '">' +
      '<span class="rel-label">' + r.r + '</span><span class="rel-name">' + r.n + '</span></button>';
    body =
      '<div class="section-lbl">Household ' + a.hh + '</div>' +
      '<p class="agent-bio" style="margin-top:0">' + familyLine(a) + '</p>' +
      '<div class="section-lbl">Immediate family <span class="src-tag genesis">from genesis.json</span></div>' +
      (g.length ? '<div class="rel-list">' + g.map(row).join('') + '</div>'
                : '<p class="agent-bio">No immediate family recorded in genesis.json.</p>') +
      '<div class="section-lbl">Extended family <span class="src-tag inferred">inferred lineage</span></div>' +
      (inf.length ? '<div class="rel-list">' + inf.map(row).join('') + '</div>'
                  : '<p class="agent-bio">None.</p>') +
      '<p class="rel-note">genesis.json records only one generation of parent links, so grandparents, ' +
      'uncles, aunts and cousins are built by a deterministic lineage layer and marked as inferred — ' +
      'never mixed with the recorded facts above.</p>';
  } else if (inspectorTab === 'estate') {
    // A9 (section 31) and A8 (section 26) both land here: what this resident
    // owns, who inherits it, and - for anyone who grew up during the run -
    // the day they stopped being billed to a parent.
    const tiles = tilesOwnedBy(a);
    const will = willOf(a);
    const heir = will ? will.heir : defaultHeirOf(a);
    const coa = comingOfAgeHour(a);
    const adult = isAdultAt(a, hourFrac);
    const payer = billPayerFor(a, hourFrac);
    const reg = landRegister();
    let coaRow = '';
    if (coa !== null) {
      const atHour = (a._bornAtHour || 0) + coa;
      const choice = comingOfAgeChoice(a);
      coaRow =
        '<div class="section-lbl">Coming of age</div>' +
        '<dl class="facts">' +
          '<div><dt>Age now</dt><dd>' + ageAt(a, hourFrac).toFixed(1) + ' years</dd></div>' +
          '<div><dt>Turns 18</dt><dd>day ' + Math.floor(atHour / 24) + ', ' +
            String(Math.floor(atHour % 24)).padStart(2, '0') + ':00</dd></div>' +
          (adult
            ? '<div><dt>Decided</dt><dd>' + COMING_OF_AGE_LABEL[choice] + '</dd></div>' +
              '<div><dt>Took up</dt><dd>' + OCC_LABEL(entryTrade(a)) + '</dd></div>'
            : '<div><dt>Decides</dt><dd>not yet — still a minor</dd></div>') +
          '<div><dt>Thinking billed to</dt><dd>' +
            (payer.id === a.id ? 'themselves' : payer.n + ' (parent)') + '</dd></div>' +
        '</dl>';
    }
    body =
      coaRow +
      '<div class="section-lbl">Property — 1×1 units held</div>' +
      '<dl class="facts">' +
        '<div><dt>Class</dt><dd>' + (tiles.length ? 'Private' : 'None — no land held') + '</dd></div>' +
        '<div><dt>Units owned</dt><dd>' + tiles.length + '</dd></div>' +
        (a._home
          ? '<div><dt>Address</dt><dd>' + (a._home.homeLabel || 'dwelling') + ' at (' +
              Math.round(a._home.x) + ', ' + Math.round(a._home.y) + ')</dd></div>'
          : '') +
        '<div><dt>Town holds</dt><dd>' + reg.government.toLocaleString() +
          ' units (government)</dd></div>' +
      '</dl>' +
      '<div class="section-lbl">Will</div>' +
      (will
        ? '<dl class="facts">' +
            '<div><dt>Status</dt><dd>Will on file — overrides the default</dd></div>' +
            '<div><dt>Named heir</dt><dd>' + (heir ? heir.n : '—') + '</dd></div>' +
            '<div><dt>Covers</dt><dd>' + tiles.length + ' land units and their savings</dd></div>' +
          '</dl>' +
          '<div class="rel-note">' + will.note + '</div>'
        : '<dl class="facts">' +
            '<div><dt>Status</dt><dd>No will — the default applies</dd></div>' +
            '<div><dt>Would pass to</dt><dd>' +
              (heir ? heir.n + ' (nearest living relative)' : 'the treasury — no living heir') +
            '</dd></div>' +
          '</dl>');
  } else if (inspectorTab === 'memory') {
    const icon = { o: '👁️', r: '💭', p: '🎯' };
    const kind = { o: 'observation', r: 'reflection', p: 'plan' };
    body =
      '<p class="rel-note" style="margin-top:0">Memory stream in the Generative Agents format ' +
      '(Park et al., UIST ’23): a flat, timestamped stream of observations, reflections and plans, ' +
      'each scored 1–10 for importance. These are the day-0 seed memories.</p>' +
      '<div class="mem-list">' + (a.mem || []).map(m =>
        '<div class="mem-row mem-' + m.t + '">' +
        '<span class="mem-ico">' + icon[m.t] + '</span>' +
        '<span class="mem-text">' + m.d + '</span>' +
        '<span class="mem-imp" title="importance">' + m.i + '</span></div>').join('') + '</div>' +
      '<div class="mem-legend">' +
        '<span>👁️ observation</span><span>💭 reflection</span><span>🎯 plan</span></div>';
  } else if (inspectorTab === 'experts') {
    const meta = {
      psychologist: ['🧠', 'Psychologist'],
      behavioral_economist: ['📈', 'Behavioral Economist'],
      political_scientist: ['🗳️', 'Political Scientist'],
      demographer: ['📊', 'Demographer'],
    };
    const routed = routeExpert(expertQuestion);
    body =
      '<p class="rel-note" style="margin-top:0">Expert reflection, after Park et al. 2024 ' +
      '(<i>Generative Agent Simulations of 1,000 People</i>). Four social-science experts each ' +
      'write up their own reading of the same resident; at question time only the matching ' +
      'expert’s notes are retrieved. Rule-derived here — the paper runs this with GPT-4o over a ' +
      'two-hour interview.</p>' +
      '<div class="section-lbl">Question-time routing</div>' +
      '<input class="expert-q" id="expertQ" value="' + expertQuestion.replace(/"/g, '&quot;') + '" ' +
      'aria-label="Ask a question to see which expert is retrieved" />' +
      '<div class="routed">→ retrieves <b>' + meta[routed][0] + ' ' + meta[routed][1] + '</b></div>' +
      Object.keys(meta).map(e => {
        const rs = (a.exp && a.exp[e]) || [];
        return '<div class="expert-block' + (e === routed ? ' on' : '') + '">' +
          '<div class="expert-name">' + meta[e][0] + ' ' + meta[e][1] +
          (e === routed ? '<span class="src-tag genesis">retrieved</span>' : '') + '</div>' +
          rs.map(r => '<p class="expert-note">' + r + '</p>').join('') + '</div>';
      }).join('');
  } else {
    const day = Math.floor(hourFrac / 24);
    const net = a.wage - a.upkeep - (a.br === 'minor' ? 0 : HEAD_TAX_PER_DAY);
    const spark = [];
    for (let d = 0; d <= Math.max(1, day); d++) spark.push(balanceOn(a, d));
    const maxB = Math.max(...spark, 1), minB = Math.min(...spark);
    const pts = spark.map((v, i) =>
      (i / Math.max(1, spark.length - 1) * 240).toFixed(1) + ',' +
      (46 - (v - minB) / Math.max(1, maxB - minB) * 42).toFixed(1)).join(' ');
    body =
      '<dl class="facts">' +
        '<div><dt>Balance now</dt><dd>' + bal.toLocaleString() + ' sim</dd></div>' +
        '<div><dt>Started with</dt><dd>' + a.bal.toLocaleString() + ' sim</dd></div>' +
        '<div><dt>Daily wage</dt><dd>+' + a.wage + '</dd></div>' +
        '<div><dt>Daily upkeep</dt><dd>−' + a.upkeep + '</dd></div>' +
        '<div><dt>Head tax</dt><dd>−' + (a.br === 'minor' ? 0 : HEAD_TAX_PER_DAY) + '</dd></div>' +
        '<div><dt>Net per day</dt><dd class="' + (net >= 0 ? 'pos' : 'neg') + '">' +
          (net >= 0 ? '+' : '') + net + '</dd></div>' +
      '</dl>' +
      '<div class="section-lbl">Balance, day 0 → ' + day + '</div>' +
      '<svg class="spark" viewBox="0 0 240 50" preserveAspectRatio="none" aria-hidden="true">' +
        '<polyline points="' + pts + '" fill="none" stroke="' + (net >= 0 ? '#2E9E4F' : '#D64545') +
        '" stroke-width="2.5" stroke-linejoin="round" /></svg>' +
      '<div class="section-lbl">Cognition budget (real $2.00 wallet)</div>' +
      '<div class="cost-bar"><span style="width:' + pct.toFixed(1) + '%;background:' +
        (pct > 85 ? 'var(--critical)' : pct > 55 ? 'var(--watch)' : 'var(--good)') + '"></span></div>' +
      '<div class="cost-line"><span>spent $' + spent.toFixed(3) + '</span><span>wallet $' + cap.toFixed(2) + '</span></div>' +
      '<button class="json-toggle" id="jsonToggle">▸ raw JSON record</button>' +
      '<pre class="json-view" id="jsonView">' + JSON.stringify({
        agent_id: a.id, name: a.n, genesis_name: a.rn, age: a.age, sex: a.sex,
        occupation: a.occ, household_id: a.hh, marital_status: a.ms, spouse: a.sp,
        children: a.nk, starting_sim_balance: a.bal, usd_wallet_cents: a.usd,
        relationships: (a.rel || []).length, memory_entries: (a.mem || []).length,
        full_record: 'data/characters/' + a.id + '.json',
      }, null, 2) + '</pre>';
  }

  el.innerHTML =
    '<div class="insp-head">' +
      '<div class="insp-avatar" style="background:' + colour + '">' + emoji + '</div>' +
      '<div><p class="agent-name">' + a.n + '</p>' +
      '<p class="agent-sub">' + a.id + ' · ' + a.age + (a.sex === 'm' ? 'M' : 'F') +
      ' · ' + OCC_LABEL(a.occ) + '</p>' +
      (a.rn && a.rn !== a.n
      ? '<p class="agent-sub2">genesis record: ' + a.rn + '</p>'
      : '<p class="agent-sub2">born in Kasukabe · staffs the ' +
        (ZONES[a.dayZone] ? ZONES[a.dayZone].label : 'town') + '</p>') +
    '</div></div>' +
    '<div class="tabs">' + tabs + '</div>' +
    '<div class="tab-body">' + body + '</div>';

  el.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
    inspectorTab = b.dataset.tab;
    renderInspector(a, state.hourFrac);
  }));
  el.querySelectorAll('.rel-row').forEach(b =>
    b.addEventListener('click', () => selectAgent(b.dataset.id)));
  const eq = document.getElementById('expertQ');
  if (eq) eq.addEventListener('input', ev => {
    expertQuestion = ev.target.value;
    renderInspector(a, state.hourFrac);
    const again = document.getElementById('expertQ');
    if (again && again.focus) { again.focus(); }
  });
  const jt = document.getElementById('jsonToggle');
  if (jt) jt.addEventListener('click', () => {
    const pre = document.getElementById('jsonView');
    pre.classList.toggle('open');
    jt.textContent = (pre.classList.contains('open') ? '▾' : '▸') + ' raw JSON record';
  });
}

/** Open a resident's file. Every entry point - a figure on the map, a roster
 * card, a name in the chat feed, a relative, a scoreboard row - lands here, so
 * the profile always appears the same way: as a popup over the town. */
/** Open a resident's record.
 *
 * `reveal` decides whether the full file below the map is opened and scrolled
 * to. A click on the MAP is the question "who is this", asked about someone you
 * are looking at - pinning the card and simultaneously yanking the page down to
 * the file loses your place on the map, which is the one thing you were using.
 * So the map pins the card and stops there; "Full record" is the way down.
 * A click on a name in a list is a request for the record, so that one reveals
 * it. */
function selectAgent(id, opts) {
  if (!AGENT_BY_ID[id]) return;
  selectedId = id;
  inspectorTab = 'profile';
  renderInspector(AGENT_BY_ID[id], state.hourFrac);
  showMapCard(AGENT_BY_ID[id]);
  if (!opts || opts.reveal !== false) openModal();
  if (cardEls) Object.keys(cardEls).forEach(cid => cardEls[cid].classList.toggle('selected', cid === id));
}

/** The resident file is a panel directly under the map, not an overlay: the
 * map stays visible and in motion while you read, and scrolling on past the
 * card reaches the rest of the town's records. */
function openModal() {
  const panel = document.getElementById('profilePanel');
  if (!panel) return;
  panel.hidden = false;
  if (panel.scrollIntoView) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closeModal() {
  const panel = document.getElementById('profilePanel');
  if (panel) panel.hidden = true;
  // The map keeps its highlight ring on the last-opened resident, so closing
  // the card does not lose your place in a town of 300-odd.
}

function wireModal() {
  const close = document.getElementById('profileClose');
  if (close) close.addEventListener('click', closeModal);
  window.addEventListener('keydown', ev => { if (ev.key === 'Escape') { closeModal(); hideMapCard(); } });
}


// ---- the card that opens on the map, at the person ----------------------
// Clicking someone should answer "who is this" without moving you off the
// map, so the card is anchored to that resident and follows them as they
// walk. The full record (family, memory, expert reflections, money) still
// lives in the panel below - this is the at-a-glance view, with a button
// through to it.
let mapCardId = null;

function showMapCard(a) {
  const el = document.getElementById('mapCard');
  if (!el) return;
  mapCardId = a.id;
  const [emoji, colour] = occFace(a.occ);
  const h = healthOf(a);
  const p = liveAgentPos[a.id] || {};
  const statusCls = !h.alive ? 'dead' : h.ailment ? (h.ailment === 'terminal' ? 'crit' : 'ill') : 'ok';
  el.innerHTML =
    '<button class="mc-close" id="mapCardClose" type="button" aria-label="Close">×</button>' +
    '<div class="mc-head">' +
      '<span class="mc-avatar" style="background:' + colour + '">' + emoji + '</span>' +
      '<span class="mc-id"><b>' + a.n + '</b>' +
      '<em>' + a.age + (a.sex === 'm' ? 'M' : 'F') + ' · ' + OCC_LABEL(a.occ) + '</em></span>' +
    '</div>' +
    '<div class="mc-status ' + statusCls + '"><span class="dot"></span>' +
      (h.alive ? HEALTH_LABEL[h.ailment || 'healthy'] : 'Deceased') + '</div>' +
    '<div class="mc-doing" id="mapCardDoing">' +
      (h.alive ? activityFor(a, p.zone || 'housing', state.hourFrac % 24) : 'No longer in the town') +
      '<em id="mapCardWhere">' + (ZONES[p.zone] ? ZONES[p.zone].label : '') + '</em></div>' +
    '<div class="mc-rows">' +
      '<span>👪 ' + familyLine(a) + '</span>' +
      '<span>🏠 ' + a.hh + ' · 🧬 ' + (a.rel || []).length + ' relatives</span>' +
      '<span>🪙 <b id="mapCardBal">' + balanceNow(a, state.hourFrac).toLocaleString() + '</b> sim' +
        ' · 💰 <b id="mapCardSpend">$' + costSpent(a, state.hourFrac).toFixed(3) + '</b></span>' +
    '</div>' +
    '<button class="mc-more" id="mapCardMore" type="button">Full record ↓</button>';
  el.hidden = false;
  positionMapCard();

  const close = document.getElementById('mapCardClose');
  if (close) close.addEventListener('click', ev => { ev.stopPropagation(); hideMapCard(); });
  const more = document.getElementById('mapCardMore');
  if (more) more.addEventListener('click', ev => { ev.stopPropagation(); openModal(); });
}

function hideMapCard() {
  const el = document.getElementById('mapCard');
  if (el) el.hidden = true;
  mapCardId = null;
}

/** Keep the card pinned to its resident, clamped inside the viewport so it
 * never gets clipped by the map's overflow. */
function positionMapCard() {
  const el = document.getElementById('mapCard');
  if (!el || el.hidden || !mapCardId || !canvas) return;
  const p = liveAgentPos[mapCardId];
  if (!p) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const cw = el.offsetWidth || 232, ch = el.offsetHeight || 190;
  let x = p.sx - cw / 2;
  let y = p.sy - ch - 26;                     // above their head
  let below = false;
  if (y < 6) { y = p.sy + 22; below = true; } // not enough room above
  x = Math.max(6, Math.min(w - cw - 6, x));
  y = Math.max(6, Math.min(h - ch - 6, y));
  el.style.left = x + 'px';
  el.style.top = y + 'px';
  el.classList.toggle('below', below);
  // The pointer tracks the resident even after the card is clamped sideways.
  const tip = Math.max(14, Math.min(cw - 14, p.sx - x));
  el.style.setProperty('--tip', tip + 'px');
}

/** Refresh only the values that move, so the card stays live without
 * rebuilding its markup every frame. */
function refreshMapCard() {
  if (!mapCardId) return;
  const a = AGENT_BY_ID[mapCardId], p = liveAgentPos[mapCardId];
  if (!a || !p) return;
  const doing = document.getElementById('mapCardDoing');
  const where = document.getElementById('mapCardWhere');
  if (doing && where && isAlive(a)) {
    doing.childNodes[0].nodeValue = activityFor(a, p.zone, state.hourFrac % 24);
    where.textContent = ZONES[p.zone] ? ZONES[p.zone].label : '';
  }
  const bal = document.getElementById('mapCardBal');
  if (bal) bal.textContent = balanceNow(a, state.hourFrac).toLocaleString();
  const sp = document.getElementById('mapCardSpend');
  if (sp) sp.textContent = '$' + costSpent(a, state.hourFrac).toFixed(3);
}

// ---- roster -------------------------------------------------------------
const rosterState = { q: '', br: 'all', occ: 'all', sort: 'name' };

function buildCard(a) {
    const [emoji, colour] = occFace(a.occ);
    const rels = a.rel || [];
    const key = ['father', 'mother', 'husband', 'wife', 'son', 'daughter', 'brother', 'sister'];
    const close = rels.filter(r => key.includes(r.r)).slice(0, 3);
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'char-card';
    card.dataset.id = a.id;
    card.innerHTML =
      '<div class="char-top">' +
        '<div class="char-avatar" style="background:' + colour + '">' + emoji + '</div>' +
        '<div class="char-id">' +
          '<div class="char-name">' + a.n + '</div>' +
          '<div class="char-occ">' + OCC_LABEL(a.occ) + '</div>' +
          '<div class="char-meta">' + a.id + ' · ' + a.age + (a.sex === 'm' ? 'M' : 'F') + ' · ' + a.hh + '</div>' +
        '</div></div>' +
      '<div class="char-rows">' +
        '<div class="char-row"><span class="k">Family</span><span class="v">' + familyLine(a) + '</span></div>' +
        (close.length ? close.map(r =>
          '<div class="char-row"><span class="k">' + r.r + '</span><span class="v">' + r.n + '</span></div>').join('') : '') +
        '<div class="char-row"><span class="k">Relatives</span><span class="v">' + rels.length + ' in town</span></div>' +
        '<div class="char-row"><span class="k">Genesis</span><span class="v">' + a.rn + '</span></div>' +
      '</div>' +
      '<p class="char-bio">' + a.bio + '</p>' +
      '<div class="char-skills">' +
        '<span class="char-skill">⚙️ ' + a.skillBase + '</span>' +
        '<span class="char-skill locked" data-skill="' + a.id + '">🔒 day ' + a.skillUnlockDay + '</span>' +
        '<span class="char-skill mem">🧠 ' + (a.mem || []).length + ' memories</span>' +
      '</div>' +
      '<div class="char-foot">' +
        '<span class="char-status" data-status="' + a.id + '">● alive</span>' +
        '<span class="char-bal" data-bal="' + a.id + '">' + a.bal.toLocaleString() + ' sim</span>' +
        '<span class="char-spend" data-spend="' + a.id + '">$0.000</span>' +
      '</div>';
    card.addEventListener('click', () => selectAgent(a.id));
  return card;
}

function buildRoster() {
  const grid = document.getElementById('rosterGrid');
  if (!grid) return;
  cardEls = {};
  const frag = document.createDocumentFragment();
  for (const a of AGENTS) {
    const card = buildCard(a);
    frag.appendChild(card);
    cardEls[a.id] = card;
  }
  grid.appendChild(frag);

  const sel = document.getElementById('rosterOcc');
  if (sel) {
    const counts = {};
    AGENTS.forEach(a => counts[a.occ] = (counts[a.occ] || 0) + 1);
    Object.entries(counts).sort((x, y) => y[1] - x[1]).forEach(([occ, n]) => {
      const o = document.createElement('option');
      o.value = occ; o.textContent = OCC_LABEL(occ) + ' (' + n + ')';
      sel.appendChild(o);
    });
  }
}

/** Give any resident born since the last check a roster card. buildRoster()
 * runs once at boot, so without this a newborn would exist everywhere except
 * the Characters grid. */
function addNewbornCards() {
  const grid = document.getElementById('rosterGrid');
  if (!grid || !cardEls) return;
  for (const a of AGENTS) {
    if (cardEls[a.id]) continue;
    cardEls[a.id] = buildCard(a);
    grid.appendChild(cardEls[a.id]);
  }
  applyRosterFilter();
}

function applyRosterFilter() {
  const grid = document.getElementById('rosterGrid');
  if (!grid || !cardEls) return;
  const q = rosterState.q.trim().toLowerCase();
  const matches = [];
  for (const a of AGENTS) {
    const hit =
      (rosterState.br === 'all' || a.br === rosterState.br) &&
      (rosterState.occ === 'all' || a.occ === rosterState.occ) &&
      (!q || a.n.toLowerCase().includes(q) || a.rn.toLowerCase().includes(q) ||
       a.occ.replace(/_/g, ' ').includes(q) || a.hh.toLowerCase().includes(q) ||
       a.id.toLowerCase().includes(q) ||
       (a.rel || []).some(r => r.n.toLowerCase().includes(q)));
    cardEls[a.id].hidden = !hit;
    if (hit) matches.push(a);
  }
  const k = rosterState.sort;
  matches.sort((x, y) => {
    if (k === 'age') return y.age - x.age;
    if (k === 'occ') return x.occ.localeCompare(y.occ) || x.n.localeCompare(y.n);
    if (k === 'hh') return x.hh.localeCompare(y.hh) || x.n.localeCompare(y.n);
    if (k === 'wealth') return balanceNow(y, state.hourFrac) - balanceNow(x, state.hourFrac);
    if (k === 'family') return (y.rel || []).length - (x.rel || []).length;
    return x.n.localeCompare(y.n);
  });
  matches.forEach(a => grid.appendChild(cardEls[a.id]));
  const c = document.getElementById('rosterCount');
  if (c) c.textContent = matches.length === POP ? POP + ' shown' : matches.length + ' of ' + POP + ' shown';
  const e = document.getElementById('rosterEmpty');
  if (e) e.hidden = matches.length !== 0;
}

// Card values refresh on their own slow cadence: 250 cards x 60fps of text
// writes costs far more than these slow-moving numbers are worth.
function refreshRosterLiveValues() {
  if (!cardEls) return;
  for (const a of AGENTS) {
    const card = cardEls[a.id];
    if (!card || card.hidden) continue;
    const sp = card.querySelector('[data-spend="' + a.id + '"]');
    if (sp) {
      const spent = costSpent(a, state.hourFrac);
      const pct = spent / (a.usd / 100) * 100;
      sp.textContent = '$' + spent.toFixed(3);
      sp.style.color = pct > 85 ? 'var(--critical)' : pct > 55 ? 'var(--watch)' : 'var(--good)';
    }
    const bl = card.querySelector('[data-bal="' + a.id + '"]');
    if (bl) bl.textContent = balanceNow(a, state.hourFrac).toLocaleString() + ' sim';
    const st = card.querySelector('[data-status="' + a.id + '"]');
    if (st) {
      const h = healthOf(a);
      st.textContent = h.alive ? (h.ailment ? '● ' + HEALTH_LABEL[h.ailment].toLowerCase() : '● alive') : '✝ deceased';
      st.className = 'char-status ' + (!h.alive ? 'dead' : h.ailment ? (h.ailment === 'terminal' ? 'crit' : 'ill') : 'ok');
      card.classList.toggle('is-dead', !h.alive);
    }
    const sk = card.querySelector('[data-skill="' + a.id + '"]');
    if (sk && state.hourFrac >= a.skillUnlockDay * 24 && sk.classList.contains('locked')) {
      sk.classList.remove('locked');
      sk.textContent = '✨ ' + a.skillUnlocked;
    }
  }
}

function wireRosterControls() {
  const s = document.getElementById('rosterSearch');
  if (s) s.addEventListener('input', e => { rosterState.q = e.target.value; applyRosterFilter(); });
  document.querySelectorAll('.filter-chip').forEach(chip =>
    chip.addEventListener('click', () => {
      rosterState.br = chip.dataset.value;
      document.querySelectorAll('.filter-chip').forEach(c => c.setAttribute('aria-pressed', String(c === chip)));
      applyRosterFilter();
    }));
  const o = document.getElementById('rosterOcc');
  if (o) o.addEventListener('change', e => { rosterState.occ = e.target.value; applyRosterFilter(); });
  const so = document.getElementById('rosterSort');
  if (so) so.addEventListener('change', e => { rosterState.sort = e.target.value; applyRosterFilter(); });
}


// Buildings are no longer drawn here. In the top-down view every institution
// is a real floor plan generated by village.js's buildPlans(), so there is
// nothing left for a per-project draw pass to add.

/** The town's build programme, and what each project changes. */
function renderBuildings(day) {
  const el = document.getElementById('buildBody');
  if (!el) return;
  // Count what has actually OPENED, not what was merely wanted by now - the
  // two differ whenever the treasury could not afford a project on schedule.
  const done = BUILD_PROJECTS.filter(b => day >= openedOn(b.zone));
  const rows = BUILD_PROJECTS.map(b => {
    const opened = openedOn(b.zone);
    const built = day >= opened;
    return '<div class="build-row ' + (built ? 'done' : 'pending') + '" data-zone="' + b.zone + '">' +
      '<span class="build-ico">' + b.icon + '</span>' +
      '<span class="build-name">' + b.name +
        '<span class="build-kind">' + b.kind + '</span></span>' +
      '<span class="build-status">open · ' + (BUILD_UPKEEP_SIM[b.zone] || 0) + '/day upkeep</span>' +
      '<p class="build-note">' + b.note + '</p></div>';
  }).join('');
  el.innerHTML =
    '<p class="rel-note" style="margin:0 0 10px">Every institution stands and is staffed from day one — ' +
    'a hospital, police station, fire station, school, library, bank, bakery, restaurant and inn on top of ' +
    'the genesis buildings. <b>This is a deliberate divergence from DESIGN.md §1</b>, which leaves the ' +
    'hospital and police station out so “the town must build its institutions.” Their capital value is ' +
    'treated as a founding investment; what the treasury pays is daily upkeep, shown in the budget.</p>' +
    '<div class="section-lbl">' + BUILD_PROJECTS.length + ' institutions open · ' +
      Object.values(CAPITAL_VALUE_SIM).reduce((a, c) => a + c, 0).toLocaleString() + ' sim of capital · ' +
      Object.values(BUILD_UPKEEP_SIM).reduce((a, c) => a + c, 0).toLocaleString() + ' sim/day upkeep</div>' + rows;
}

/** Alive / ill / dead at a glance, plus what the hospital changes. */
function renderHealth(day) {
  const el = document.getElementById('healthBody');
  if (!el) return;
  const alive = AGENTS.filter(isAlive);
  const byStatus = {};
  alive.forEach(a => { const st = healthOf(a).ailment || 'healthy'; byStatus[st] = (byStatus[st] || 0) + 1; });
  const dead = AGENTS.length - alive.length;
  const hos = hospitalOpen(day);

  const hudAlive = document.getElementById('hudAlive');
  if (hudAlive) hudAlive.textContent = alive.length;
  const hudDead = document.getElementById('hudDead');
  if (hudDead) hudDead.textContent = dead;

  const stat = (v, k, cls) => '<div class="score-stat ' + (cls || '') + '"><span class="sv">' + v +
    '</span><span class="sk">' + k + '</span></div>';
  el.innerHTML =
    '<div class="score-grid">' +
      stat(alive.length, 'alive', 'good') +
      stat(dead, 'deceased', dead ? 'warn' : '') +
      stat(byStatus.healthy || 0, 'healthy') +
      stat((byStatus.short || 0) + (byStatus.medium || 0) + (byStatus.long || 0), 'ill') +
      stat(byStatus.terminal || 0, 'terminally ill', (byStatus.terminal || 0) ? 'crit' : '') +
      stat(healthSim.recoveries, 'recoveries') +
    '</div>' +
    '<div class="hos-note ' + (hos ? 'open' : 'shut') + '">' +
      (hos
        ? '🏥 <b>Hospital open.</b> Medium and long illnesses are treatable, so the untreated-chronic death hazard no longer applies.'
        : '🏥 <b>No hospital yet.</b> The pharmacy treats short ailments only, so every medium and long illness runs untreated at ' +
          (CHRONIC_UNTREATED_DAILY_HAZARD.medium * 100).toFixed(1) + '%/' +
          (CHRONIC_UNTREATED_DAILY_HAZARD.long * 100).toFixed(1) + '% daily hazard. Opens day ' +
          0 + '.') +
    '</div>' +
    (Object.keys(healthSim.causes).length
      ? '<div class="section-lbl">Causes of death (' + healthSim.deaths + ' total)</div>' +
        '<div class="cause-list">' + Object.entries(healthSim.causes).sort((x, y) => y[1] - x[1]).map(([c, n]) =>
          '<div class="cause-row"><span>' + c + '</span><span class="cause-n">' + n + '</span></div>').join('') + '</div>'
      : '') +
    '<div class="section-lbl">Recent</div>' +
    (healthSim.log.length
      ? '<div class="health-log">' + healthSim.log.slice(0, 10).map(e =>
          '<div class="hl-row hl-' + e.kind + '" data-id="' + e.id + '">' +
          '<span class="hl-day">d' + e.day + '</span>' +
          '<span class="hl-name">' + e.name + '</span>' +
          '<span class="hl-what">' + (e.kind === 'death' ? 'died — ' + e.cause
            : e.kind === 'recovery' ? 'recovered from ' + e.cause + ' illness'
            : 'fell ill — ' + e.cause) + '</span></div>').join('') + '</div>'
      : '<p class="chat-empty">Nobody has fallen ill yet.</p>');
  el.querySelectorAll('.hl-row').forEach(r =>
    r.addEventListener('click', () => selectAgent(r.dataset.id)));
}

/** What the town is doing right now, aggregated. */
function renderWork(hourFrac) {
  const el = document.getElementById('workBody');
  if (!el) return;
  const hod = hourFrac % 24;
  const tally = {};
  let working = 0, capacity = 0, capable = 0;
  for (const a of AGENTS) {
    if (!isAlive(a)) continue;
    const p = liveAgentPos[a.id];
    if (!p) continue;
    const act = activityFor(a, p.zone, hod);
    tally[act] = (tally[act] || 0) + 1;
    const cap = workCapacity(a);
    capacity += cap; capable += 1;
    if (phaseAt(Math.floor(hod)) === 'day' && p.zone === a.dayZone && cap > 0) working++;
  }
  const rows = Object.entries(tally).sort((x, y) => y[1] - x[1]).slice(0, 9);
  const max = Math.max(1, ...rows.map(r => r[1]));
  el.innerHTML =
    '<div class="work-head"><span><b>' + working + '</b> at work</span>' +
    '<span><b>' + (capable ? (capacity / capable * 100).toFixed(0) : 0) + '%</b> average work capacity</span></div>' +
    rows.map(([act, n]) =>
      '<div class="occ-row"><span class="occ-name">' + act + '</span>' +
      '<span class="occ-bar"><span style="width:' + (n / max * 100).toFixed(0) + '%"></span></span>' +
      '<span class="occ-n">' + n + '</span></div>').join('');
}

// ---- census -------------------------------------------------------------
function renderCensus() {
  const bar = (label, n, max, colour) =>
    '<div class="bar-row"><span class="bar-lbl">' + label + '</span>' +
    '<span class="bar-track"><span style="width:' + (n / max * 100).toFixed(0) + '%;background:' + colour + '"></span></span>' +
    '<span class="bar-n">' + n + '</span></div>';

  const occ = {}; AGENTS.forEach(a => occ[a.occ] = (occ[a.occ] || 0) + 1);
  const occRows = Object.entries(occ).sort((x, y) => y[1] - x[1]);
  const occMax = Math.max(...occRows.map(r => r[1]));
  const oc = document.getElementById('occChart');
  if (oc) oc.innerHTML = occRows.map(([o, n]) => bar(OCC_LABEL(o), n, occMax, 'var(--brick)')).join('');

  // A8: a minor who has crossed eighteen counts as working-age from that
  // hour, so the age chart shows the roster actually filling over time
  // (section 26) rather than the genesis snapshot forever.
  const br = { working: 0, minor: 0, elderly: 0 };
  AGENTS.filter(isAlive).forEach(a => br[bracketAt(a, state.hourFrac)]++);
  const ac = document.getElementById('ageChart');
  if (ac) ac.innerHTML =
    bar('Working-age', br.working, POP, 'var(--sun-strong)') +
    bar('Minors', br.minor, POP, 'var(--ocean)') +
    bar('Elderly', br.elderly, POP, 'var(--brick)');

  const hh = {}; AGENTS.forEach(a => hh[a.hh] = (hh[a.hh] || 0) + 1);
  const sizes = {}; Object.values(hh).forEach(s => sizes[s] = (sizes[s] || 0) + 1);
  const sizeRows = Object.entries(sizes).sort((x, y) => x[0] - y[0]);
  const sizeMax = Math.max(...sizeRows.map(r => r[1]));
  const hc = document.getElementById('hhChart');
  if (hc) hc.innerHTML = sizeRows.map(([s, n]) =>
    bar(s + ' member' + (s > 1 ? 's' : ''), n, sizeMax, 'var(--ocean)')).join('');

  const rel = {};
  AGENTS.forEach(a => (a.rel || []).forEach(r => rel[r.r] = (rel[r.r] || 0) + 1));
  const relRows = Object.entries(rel).sort((x, y) => y[1] - x[1]).slice(0, 10);
  const relMax = Math.max(...relRows.map(r => r[1]));
  const rc = document.getElementById('relChart');
  if (rc) rc.innerHTML = relRows.map(([r, n]) => bar(r, n, relMax, 'var(--good)')).join('');
}

// ---- clock + loop -------------------------------------------------------
// `?t=13.5` opens the page at that hour of day, and `?speed=6` sets the rate.
// Useful for linking someone straight to a particular moment - and it is the
// only way to inspect a given time in a headless screenshot, where the frame
// clock does not advance on its own.
const _params = typeof location !== 'undefined' && location.search
  ? new URLSearchParams(location.search) : null;
const _startHour = _params && _params.get('t') !== null ? parseFloat(_params.get('t')) : NaN;
const _startSpeed = _params && _params.get('speed') !== null ? parseFloat(_params.get('speed')) : NaN;
const state = {
  hourFrac: Number.isFinite(_startHour) ? _startHour : 6.02,
  playing: true,
  speed: Number.isFinite(_startSpeed) ? _startSpeed : 1,
};
const SEC_PER_HOUR_AT_1X = 5;
let lastTs = null;
let _lastRenderedDay = -1;
let _lastBirthCount = 0;
let _lastPanelRender = 0;

function fmtClock(hourFrac) {
  const h = Math.floor(hourFrac) % 24;
  const m = Math.floor((hourFrac - Math.floor(hourFrac)) * 60);
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
}

function tick(ts) {
  if (lastTs === null) lastTs = ts;
  const dt = (ts - lastTs) / 1000;
  lastTs = ts;
  if (state.playing) state.hourFrac += (dt / SEC_PER_HOUR_AT_1X) * state.speed;

  const hour = Math.floor(state.hourFrac) % 24;
  const day = Math.floor(state.hourFrac / 24) + 1;
  const cl = document.getElementById('clockLabel'); if (cl) cl.textContent = fmtClock(state.hourFrac);
  const dl = document.getElementById('dayLabel'); if (dl) dl.textContent = 'Day ' + day;
  const pp = document.getElementById('phasePill'); if (pp) pp.textContent = PHASE_LABEL[phaseAt(hour)];

  advanceHealthTo(day - 1);
  updatePositions(state.hourFrac);
  tickConversations(state.hourFrac);
  render(ts / 1000, state.hourFrac);
  // Panel DOM updates are throttled: they rebuild innerHTML over the whole
  // population, and none of these numbers move fast enough to justify doing
  // that 60 times a second. The canvas still redraws every frame.
  if (ts - _lastPanelRender > 400) {
    _lastPanelRender = ts;
    renderOccupancy();
    renderGovernment(state.hourFrac);
    renderWork(state.hourFrac);
  }
  if (day !== _lastRenderedDay) {
    // These only change on a day boundary, so they do not belong in a 60fps
    // loop - re-rendering them every frame was measurable and pointless.
    _lastRenderedDay = day;
    renderHealth(day - 1);
    renderBuildings(day - 1);
    renderTreasury(state.hourFrac);
    if (birthCount !== _lastBirthCount) { _lastBirthCount = birthCount; addNewbornCards(); }
  }
  positionMapCard();
  refreshMapCard();

  const panel = document.getElementById('profilePanel');
  if (selectedId && inspectorTab === 'profile' && panel && !panel.hidden) {
    const a = AGENT_BY_ID[selectedId], p = liveAgentPos[selectedId];
    const v = document.querySelector('.now-verb'), at = document.querySelector('.now-at');
    if (a && p && v && at) {
      v.textContent = activityFor(a, p.zone, state.hourFrac % 24);
      at.textContent = 'at ' + (ZONES[p.zone] ? ZONES[p.zone].label : '—');
    }
  }
  requestAnimationFrame(tick);
}

// ---- controls + picking -------------------------------------------------
function wireControls() {
  const play = document.getElementById('playBtn');
  if (play) play.addEventListener('click', () => {
    state.playing = !state.playing;
    play.textContent = state.playing ? '⏸ Pause' : '▶ Play';
    play.setAttribute('aria-pressed', String(state.playing));
  });
  document.querySelectorAll('button.speed').forEach(b =>
    b.addEventListener('click', () => {
      state.speed = Number(b.dataset.speed);
      document.querySelectorAll('button.speed').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    }));

  if (!canvas) return;
  canvas.addEventListener('click', ev => {
    const r = canvas.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    let best = null, bestD = 26 * 26;
    for (const a of AGENTS) {
      const p = liveAgentPos[a.id];
      if (!p) continue;
      const d = (p.sx - mx) ** 2 + (p.sy - 12 - my) ** 2;
      if (d < bestD) { bestD = d; best = a; }
    }
    if (best) {
      // Same path as every other way of opening a resident, minus the reveal:
      // this used to be a hand-copied selectAgent with the openModal left out,
      // which is why the two drifted apart.
      selectAgent(best.id, { reveal: false });
    } else {
      hideMapCard();                // clicking empty ground dismisses it
    }
  });
  canvas.addEventListener('wheel', ev => {
    ev.preventDefault();
    const f = ev.deltaY < 0 ? 1.12 : 0.89;
    // Floor the zoom just below whatever it takes to fit the whole map, so
    // you can always scroll out far enough to see the entire village - and
    // no further, which would leave it a speck in a sea of sky.
    const floor = Math.min(0.4, fitZoom() * 0.92);
    camera.zoom = Math.min(2.6, Math.max(floor, camera.zoom * f));
    updateZoomLabel();
  }, { passive: false });
  canvas.addEventListener('mousedown', ev => {
    camera.drag = { x: ev.clientX, y: ev.clientY, ox: camera.ox, oy: camera.oy };
  });
  window.addEventListener('mousemove', ev => {
    if (!camera.drag) return;
    camera.ox = camera.drag.ox + (ev.clientX - camera.drag.x);
    camera.oy = camera.drag.oy + (ev.clientY - camera.drag.y);
  });
  window.addEventListener('mouseup', () => { camera.drag = null; });
  // Theme toggle. The page opens bright (a town-builder should), and this
  // switches to the fully-designed dark palette rather than following the OS.
  // Roofs on = the town as a diorama; roofs off = the same buildings opened
  // up so you can see the furnished rooms and who is standing in them.
  const roofBtn = document.getElementById('roofBtn');
  if (roofBtn) roofBtn.addEventListener('click', () => {
    const on = roofBtn.textContent.indexOf('Roofs') !== -1 && roofBtn.dataset.off !== '1';
    setRoofs(!on);
    roofBtn.dataset.off = on ? '1' : '';
    roofBtn.textContent = on ? '🏚️ Interiors' : '🏠 Roofs';
    groundCache.key = '';                 // the cached scene must be repainted
  });

  const themeBtn = document.getElementById('themeBtn');
  if (themeBtn) themeBtn.addEventListener('click', () => {
    const root = document.documentElement;
    const dark = root.getAttribute('data-theme') === 'dark';
    root.setAttribute('data-theme', dark ? 'light' : 'dark');
    themeBtn.textContent = dark ? '🌙 Night' : '☀️ Day';
  });

  const fs = document.getElementById('fullscreenBtn');
  if (fs) fs.addEventListener('click', () => {
    const panel = document.getElementById('mapPanel');
    if (!panel) return;
    const on = panel.classList.toggle('fullscreen');
    document.body.classList.toggle('map-fullscreen', on);
    fs.textContent = on ? '⛶ Exit fullscreen' : '⛶ Fullscreen map';
    // The canvas backing store must be resized to the new box before the
    // camera is refitted, or the map renders at the old resolution.
    requestAnimationFrame(() => { resizeCanvas(); fitCamera(); });
  });
  window.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape') return;
    const panel = document.getElementById('mapPanel');
    if (panel && panel.classList.contains('fullscreen')) {
      panel.classList.remove('fullscreen');
      document.body.classList.remove('map-fullscreen');
      if (fs) fs.textContent = '⛶ Fullscreen map';
      requestAnimationFrame(() => { resizeCanvas(); fitCamera(); });
    }
  });

  // Zoom buttons. They step the same camera the wheel drives and obey the
  // same floor, so you can always reach "whole village" with the − button and
  // never zoom past what the map can show.
  function zoomBy(f) {
    const w = canvas.clientWidth || 1, h2 = canvas.clientHeight || 1;
    // keep the centre of the viewport fixed while zooming
    const u = (w / 2 - camera.ox) / ((TILE_W / 2) * camera.zoom);
    const v = (h2 / 2 - camera.oy) / ((TILE_H / 2) * camera.zoom);
    const cx = (u + v) / 2, cy = (v - u) / 2;
    const floor = Math.min(0.4, fitZoom() * 0.92);
    camera.zoom = Math.min(2.6, Math.max(floor, camera.zoom * f));
    lookAt(cx, cy);
    updateZoomLabel();
  }
  const zin = document.getElementById('zoomInBtn');
  if (zin) zin.addEventListener('click', () => zoomBy(1.25));
  const zout = document.getElementById('zoomOutBtn');
  if (zout) zout.addEventListener('click', () => zoomBy(0.8));

  const rc = document.getElementById('recentreBtn');
  if (rc) rc.addEventListener('click', () => { resizeCanvas(); fitCamera(); });
  // Resizing must not throw away where you were looking. Keep the tile the
  // camera is centred on and the current zoom, and just re-fit the canvas
  // around them - the old handler called centreCamera() and snapped the view
  // back to the default every time the window changed size.
  window.addEventListener('resize', () => {
    const w = canvas.clientWidth || 1, h = canvas.clientHeight || 1;
    const u = (w / 2 - camera.ox) / ((TILE_W / 2) * camera.zoom);
    const v = (h / 2 - camera.oy) / ((TILE_H / 2) * camera.zoom);
    const cx = (u + v) / 2, cy = (v - u) / 2;
    resizeCanvas();
    lookAt(cx, cy);
  });
}

// ---- boot ---------------------------------------------------------------
buildVillage();
assignHomes();
assignShifts();
resizeCanvas();
centreCamera();
wireControls();
wireModal();
buildRoster();
wireRosterControls();
applyRosterFilter();
refreshRosterLiveValues();
renderCensus();
advanceHealthTo(Math.floor(state.hourFrac / 24));
renderHealth(Math.floor(state.hourFrac / 24));
renderBuildings(Math.floor(state.hourFrac / 24));
renderTreasury(state.hourFrac);
updatePositions(state.hourFrac);
renderWork(state.hourFrac);
renderOccupancy();
renderGovernment(state.hourFrac);
renderScoreboard(state.hourFrac);
// `?open=a0039` opens straight to that resident's file - handy for linking
// someone to a specific person, and the only way to capture the popup in a
// headless screenshot.
if (_params && _params.get('fit') === '1') { resizeCanvas(); fitCamera(); }
if (_params && _params.get('open') && AGENT_BY_ID[_params.get('open')]) {
  // Show them on the map, where the link is pointing. Opening the full record
  // as well would scroll the page away from the map, which is the opposite of
  // what "look at this person" should do.
  const target = AGENT_BY_ID[_params.get('open')];
  selectedId = target.id;
  renderInspector(target, state.hourFrac);
  showMapCard(target);
}

requestAnimationFrame(tick);
setInterval(refreshRosterLiveValues, 1000);
setInterval(() => renderScoreboard(state.hourFrac), 1200);
