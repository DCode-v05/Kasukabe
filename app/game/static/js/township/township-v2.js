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
  pharmacist:        { shirt: '#DFF2EA', legs: '#3B5566', coat: '#FFFFFF', hat: 'glasses' },
  police_officer:    { shirt: '#2F4B7C', legs: '#22385E', hat: 'cap-police' },
  firefighter:       { shirt: '#C6362A', legs: '#3C4654', hat: 'helmet-fire' },
  construction_worker:{ shirt: '#E8A33D', legs: '#6B5A3A', hat: 'helmet-work' },
  construction_company_owner: { shirt: '#D98E32', legs: '#4A4238', hat: 'helmet-work' },
  miner:             { shirt: '#7A6A57', legs: '#4A4238', hat: 'helmet-mine' },
  farmer:            { shirt: '#7FB069', legs: '#7A5230', hat: 'straw' },
  forester:          { shirt: '#3F7A46', legs: '#5A4A32', hat: 'bandana' },
  chef:              { shirt: '#F4F1E8', legs: '#3C4654', coat: '#FFFFFF', hat: 'toque' },
  baker:             { shirt: '#F2E2C6', legs: '#8C6239', coat: '#FFF8EC', hat: 'toque' },
  waiter:            { shirt: '#FFFFFF', legs: '#2E3440', hat: 'apron' },
  barista:           { shirt: '#B07D56', legs: '#3C4654', coat: '#E8DBC2', hat: 'apron' },
  cafe_owner:        { shirt: '#E28E7C', legs: '#5A4A32', coat: '#F4E4D4', hat: 'apron' },
  school_teacher:    { shirt: '#6E9E4A', legs: '#3B4A56', hat: 'mortar' },
  college_instructor:{ shirt: '#6A8EAE', legs: '#2E3440', hat: 'mortar' },
  librarian:         { shirt: '#8A7458', legs: '#4A4238', hat: 'glasses' },
  banker:            { shirt: '#2E3440', legs: '#22262E', coat: '#3E4756', hat: 'tie' },
  government_clerk:  { shirt: '#C9A227', legs: '#4A4238', hat: 'visor' },
  office_administrator: { shirt: '#7A93AC', legs: '#2E3440', hat: 'headset' },
  trader:            { shirt: '#CE7A5C', legs: '#5A4A32', hat: 'flatcap' },
  artisan:           { shirt: '#F4A259', legs: '#6B5A3A', hat: 'bandana' },
  shopkeeper:        { shirt: '#CE7A5C', legs: '#4A4238', coat: '#E8DBC2', hat: 'apron' },
  grocery_clerk:     { shirt: '#E2833E', legs: '#4A4238', coat: '#F2E2C6', hat: 'apron' },
  grocery_store_owner:{ shirt: '#D96C4F', legs: '#4A4238', coat: '#F2E2C6', hat: 'apron' },
  innkeeper:         { shirt: '#A6784E', legs: '#5A4A32', coat: '#E8DBC2', hat: 'apron' },
  street_sweeper:    { shirt: '#8FA0AE', legs: '#5A6472', hat: 'helmet-work' },
  day_laborer:       { shirt: '#B5A48B', legs: '#6B5A3A', hat: 'bandana' },
  college_student:   { shirt: '#8FB8DE', legs: '#3B4A56', hat: 'beanie' },
  student:           { shirt: '#9BC4E2', legs: '#3B4A56', hat: 'satchel' },
  child:             { shirt: '#8ECAE6', legs: '#4A6072', hat: 'sunhat' },
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

// ---- worksites within an occupation -------------------------------------
// The generated cast gives every occupation exactly one workplace, which left
// the pasture, the orchard and the river docks with nobody in them all day.
// A farm is not one field: some of the farmers keep the livestock, some work
// the fruit, the rest are on the crops. Dock work is day labour, and the
// traders who ship through the quay are down there with them.
//
// The split is by resident id, so it is stable across reloads and a person
// keeps the same worksite for the whole run. It also writes back to `dz`, so
// the record card, the census and the map all say the same thing.
const WORKSITE_SPLIT = {
  farmer:      [['farmland', 0.55], ['pasture', 0.27], ['orchard', 0.18]],
  day_laborer: [['marketrow', 0.62], ['docks', 0.38]],
  trader:      [['marketrow', 0.78], ['docks', 0.22]],
};
/** Deal each occupation out across its worksites in exact proportion.
 *  A per-person draw is too lumpy at this size: with nineteen farmers,
 *  independent coin tosses left the orchard with a single worker in it. */
/** Staff the school to the ratio the rulebook asks for, and put the surplus
 *  where the town is actually short.
 *
 *  Genesis dealt out nine school teachers for a school roll that is rarely
 *  more than a dozen - one teacher per pupil and change, while the wards and
 *  the fields were short-handed. Teachers above the ratio are moved into the
 *  trades the town needs, oldest first, so the school keeps its most
 *  experienced staff. */
const TEACHERS_PER_CLASS = 2, PUPILS_PER_CLASS = 15;
// The college is taught in seminars, not classes: one instructor per eight
// students. That is a different ratio from the school on purpose - a lecture
// hall of fifteen to two is a school, and this is meant to be a college.
const STUDENTS_PER_INSTRUCTOR = 8;
const TEACHER_SURPLUS_GOES_TO = ['nurse', 'farmer', 'day_laborer', 'artisan', 'shopkeeper'];
const INSTRUCTOR_SURPLUS_GOES_TO = ['librarian', 'office_administrator', 'trader',
                                    'artisan', 'day_laborer'];

/** Move whoever is surplus to a staffing ratio into a trade the town needs. */
function retrade(person, trade, flag) {
  person[flag] = person.occ;
  person.occ = trade;
  person.dz = null;
  person.dayZone = OCC_HOME_ZONE[trade] || 'tradeoffices';
  person.wage = WAGE[trade] !== undefined ? WAGE[trade] : person.wage;
}

function rebalanceTeachers(list) {
  const pupils = list.filter(a => a.br === 'minor' && a.occ !== 'college_student').length;
  const classes = Math.max(1, Math.ceil(pupils / PUPILS_PER_CLASS));
  const wanted = classes * TEACHERS_PER_CLASS;
  const teachers = list.filter(a => a.occ === 'school_teacher')
                       .sort((x, y) => y.age - x.age);          // keep the senior staff
  const surplus = teachers.slice(wanted);
  surplus.forEach((a, i) =>
    retrade(a, TEACHER_SURPLUS_GOES_TO[i % TEACHER_SURPLUS_GOES_TO.length], '_wasTeacher'));
  return { pupils, classes, wanted, moved: surplus.length };
}

/** The college, at one instructor per eight students. */
function rebalanceInstructors(list) {
  const students = list.filter(a => a.occ === 'college_student' ||
                                    (a.dz === 'college' && a.occ === 'student')).length;
  const wanted = Math.max(1, Math.ceil(students / STUDENTS_PER_INSTRUCTOR));
  const staff = list.filter(a => a.occ === 'college_instructor')
                    .sort((x, y) => y.age - x.age);
  const surplus = staff.slice(wanted);
  surplus.forEach((a, i) =>
    retrade(a, INSTRUCTOR_SURPLUS_GOES_TO[i % INSTRUCTOR_SURPLUS_GOES_TO.length],
            '_wasInstructor'));
  return { students, wanted, moved: surplus.length };
}

/** The district each trade works in, for anyone whose trade changes. */
const OCC_HOME_ZONE = {
  nurse: 'hospital', doctor: 'hospital', farmer: 'farmland', forester: 'forest',
  miner: 'oreoutcrop', day_laborer: 'marketrow', artisan: 'tradeoffices',
  shopkeeper: 'marketrow', trader: 'marketrow', baker: 'bakery',
  school_teacher: 'school', police_officer: 'police', firefighter: 'fire',
};

// Eight officers across three shifts is two or three on duty at a time, so the
// posts are ordered by what has to be covered first and the last one is
// daytime-only. A guard on a shut bank at four in the morning is an officer
// not on the streets.
const GUARD_POSTS = [
  // The station itself is a post. Sending the whole shift out on the beat left
  // the police station empty around the clock, which is not how a station
  // works - somebody has to be there to walk into.
  { zone: 'police',     officers: 1, what: 'On the desk at the police station' },
  { zone: 'government', officers: 1, what: 'On guard at the government complex' },
  { zone: 'bank',       officers: 1, what: 'On guard at the town bank', hours: [8, 18] },
];

/** Enough officers to actually hold the posts.
 *
 *  Eight officers over three shifts is two or three on duty at once, and the
 *  town asks for a desk, a guard on the government complex, a guard on the
 *  bank by day and somebody on the streets - four. So the bank was never
 *  covered and the beat was thin. The rulebook already says staffing follows
 *  need (`labour.dynamic_staffing`); this applies it to the one service whose
 *  need is a fixed rota rather than a headcount per thousand. */
const SHIFTS_PER_DAY = 3;
const POLICE_RECRUIT_FROM = ['day_laborer', 'street_sweeper', 'artisan'];

function staffThePosts(list) {
  const needOnShift = GUARD_POSTS.reduce((t, p) => t + p.officers, 0) + 1;   // +1 beat
  const want = needOnShift * SHIFTS_PER_DAY;
  const have = list.filter(a => a.occ === 'police_officer').length;
  if (have >= want) return { have, want, moved: 0 };
  const pool = list.filter(a => POLICE_RECRUIT_FROM.indexOf(a.occ) >= 0 &&
                                a.br === 'working')
                   .sort((x, y) => hash(x.id + 'police') - hash(y.id + 'police'));
  const take = pool.slice(0, want - have);
  take.forEach(a => {
    a._wasTrade = a.occ;
    a.occ = 'police_officer';
    a.dz = 'police';
    a.dayZone = 'police';
    a.wage = WAGE.police_officer;
  });
  return { have, want, moved: take.length };
}

function assignWorksites(list) {
  const byOcc = {};
  for (const a of list) if (WORKSITE_SPLIT[a.occ]) (byOcc[a.occ] = byOcc[a.occ] || []).push(a);
  for (const occ of Object.keys(byOcc)) {
    const people = byOcc[occ].slice()
      .sort((p, q) => hash(p.id + '|ws') - hash(q.id + '|ws'));
    const split = WORKSITE_SPLIT[occ];
    let i = 0;
    split.forEach(([zone, share], k) => {
      const n = (k === split.length - 1)
        ? people.length - i : Math.round(people.length * share);
      for (let j = 0; j < n && i < people.length; j++, i++) people[i].dz = zone;
    });
  }
}

function initAgentDerived(a) {
  // Keep the genesis kinship so resetHealth() can restore it: births append to
  // `rel` and bump `nk`, and a replay has to undo exactly that.
  if (!a._relBase) { a._relBase = (a.rel || []).slice(); a._nkBase = a.nk || 0; }
  // A child old enough for school goes to school. Genesis handed every young
  // child `dz: 'park'`, which is where the four-month-olds were found.
  if (a.br === 'minor' && a.occ === 'child' && ZONES.school) a.dayZone = 'school';
  else if (a.dz) a.dayZone = a.dz;
  else if (a.br === 'elderly') a.dayZone = seeded01(a.id, 'day') < 0.5 ? 'park' : 'library';
  else a.dayZone = 'tradeoffices';
  const p = seeded01(a.id, 'evening');
  // Where this resident spends an evening. A third of the town in the park and
  // a third in the square put most of the population in two places every
  // night; a real evening is spread over the whole town, and a good many
  // people are simply out walking. `streets` is that - a wander over the
  // pavements rather than a destination.
  a.eveningZone =
      p < 0.20 ? 'park'
    : p < 0.34 ? 'streets'
    : p < 0.46 ? 'townsquare'
    : p < 0.56 ? 'docks'
    : p < 0.66 ? 'marketrow'
    : p < 0.74 ? 'library'
    : p < 0.82 ? 'restaurant'
    : p < 0.90 ? (a.br === 'elderly' ? 'inn' : 'cafe')
    : p < 0.96 ? 'housing'                       // a quiet night in
    :            (a.br === 'elderly' ? 'library' : 'inn');

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
rebalanceTeachers(AGENTS);      // school: 2 teachers per 15 pupils
rebalanceInstructors(AGENTS);   // college: 1 instructor per 8 students
staffThePosts(AGENTS);          // enough officers to hold the rota
assignWorksites(AGENTS);        // before the derived state reads dz
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
      const inset = 0.35;   // v2 dwellings are ~1.5 tiles across, not 10
      const dep = slot.plan.d !== undefined ? slot.plan.d : slot.plan.h;
      const jx = inset + seeded01(m.id, 'homex') * Math.max(0.25, slot.plan.w - inset * 2);
      const jy = inset + seeded01(m.id, 'homey') * Math.max(0.25, dep - inset * 2);
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
  resetFood();
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
  // UI-04. Whether a resident is alive, ill or dead is the world's fact and
  // rides the same poll (UI-03 already made the map's draw gate read it).
  // Running this over a bound town invented ailments, recoveries and
  // hunger deaths for people the world was still dispatching turns to.
  if (worldIsBound()) return;
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
        h.everIll = true;      // so the recoveries tile can name who they were
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
  for (let d = healthSim.day + 1; d <= day; d++) {
    stepHealthDay(d);
    // Food is settled after the day's health, because who can work decides how
    // much is grown, and before the next day's health, because going hungry is
    // one of the things that makes a person ill.
    if (foodSim.day < d) { if (foodSim.day < d - 1) resetFood(); stepFoodDay(d); foodSim.day = d; }
  }
  healthSim.day = Math.max(healthSim.day, day);
}

function healthOf(a) { return healthSim.states[a.id] || { status: 'healthy', alive: true, ailment: null }; }
// UI-03: the browser could bury a living resident.
//
// `healthOf` is the in-page `healthSim` - terminal rolls at 0.08%/day, an
// 85+ hazard, untreated chronic ailments, plus `foodSim`'s starvation
// after N hungry days - advanced against the BROWSER's clock, which runs
// at 60 wall-seconds per game hour and has nothing to do with the world
// tick. At 10x, a multi-hour session reaches browser-day 50.
//
// Nothing stopped that model from overriding the run. A resident the world
// reported alive was filtered out of `V.setAgents`, `agentPosition`
// returned them "buried at home", the roster said "deceased" and the
// inspector gave a date and a cause. All of it invented, over a live run.
//
// DESIGN section 8: the viewers are windows, never authorities. When a run
// is bound, the world's own `alive` decides and the model is not asked.
function isAlive(a) {
  if (LIVE.enabled && LIVE.connected && LIVE.alive && a.id in LIVE.alive) {
    return LIVE.alive[a.id];
  }
  return healthOf(a).alive;
}
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

// Where a person stands inside a place they are using. An independent hash per
// person clumped them: twelve clerks drew twelve unrelated points and half of
// them landed on top of each other, which reads as a pile rather than an
// office. Each person instead takes a numbered slot in the place and the slots
// walk a low-discrepancy (R2) sequence, so any number of people spread evenly
// over the floor and two of them never share a spot.
const R2A = 0.7548776662466927, R2B = 0.5698402909980532;
const slotBook = {};                       // zoneId -> { agentId: slot }
function slotOf(a, zoneId) {
  const book = slotBook[zoneId] || (slotBook[zoneId] = { _n: 0 });
  if (book[a.id] === undefined) book[a.id] = book._n++;
  return book[a.id];
}
function taskSpot(a, zoneId, i) {
  const z = ZONES[zoneId] || ZONES.townsquare;
  const n = slotOf(a, zoneId) * TASK_POINTS + i + 1;
  const jx = 0.10 + ((0.5 + R2A * n) % 1) * 0.80;
  const jy = 0.10 + ((0.5 + R2B * n) % 1) * 0.80;
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
// ---- walking the streets -------------------------------------------------
// Residents used to cross town on an L-shaped corridor - straight out along x,
// straight down y - which walked them through gardens, buildings and the
// middle of the park. The town has a road network with pavements, so they walk
// on it: out of the door to the nearest kerb, along the streets, and in at the
// far end.
// Built on first use, not at load: the renderer handle V is declared further
// down this file, and the network is read off the renderer's terrain.
let _walkNet = null;
const walkNet = () => _walkNet || (_walkNet = buildWalkNet());
function buildWalkNet() {
  const G = V.GRID;
  const tiles = (V.walkTiles && V.walkTiles.length) ? V.walkTiles : (V.roadTiles || []);
  const at = new Int32Array(G * G).fill(-1);
  tiles.forEach(([x, y], i) => { at[y * G + x] = i; });

  // 4-neighbour graph over the walkable tiles
  const nbr = tiles.map(([x, y]) => {
    const out = [];
    if (x + 1 < G && at[y * G + x + 1] >= 0) out.push(at[y * G + x + 1]);
    if (x - 1 >= 0 && at[y * G + x - 1] >= 0) out.push(at[y * G + x - 1]);
    if (y + 1 < G && at[(y + 1) * G + x] >= 0) out.push(at[(y + 1) * G + x]);
    if (y - 1 >= 0 && at[(y - 1) * G + x] >= 0) out.push(at[(y - 1) * G + x]);
    return out;
  });

  // Nearest kerb for every tile on the map, by one flood fill outwards from
  // the pavements. Doing this per resident per frame was 500k distance tests
  // a tick; this makes it a lookup.
  const near = new Int32Array(G * G).fill(-1);
  {
    const q = new Int32Array(G * G);
    let head = 0, tail = 0;
    tiles.forEach(([x, y], i) => { near[y * G + x] = i; q[tail++] = y * G + x; });
    while (head < tail) {
      const c = q[head++], cx = c % G, cy = (c - cx) / G;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = cx + dx, ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= G || ny >= G) continue;
        const n = ny * G + nx;
        if (near[n] >= 0) continue;
        near[n] = near[c]; q[tail++] = n;
      }
    }
  }
  const kerbAt = (tx, ty) => {
    const x = Math.max(0, Math.min(G - 1, Math.floor(tx)));
    const y = Math.max(0, Math.min(G - 1, Math.floor(ty)));
    return near[y * G + x];
  };

  // One breadth-first field per destination district, rooted at the kerb
  // outside it. Every resident heading there follows it home.
  const roots = {}, fields = {};
  for (const z of Object.keys(ZONES)) {
    const zz = ZONES[z];
    roots[z] = kerbAt(zz.x + zz.w / 2, zz.y + zz.h / 2);
  }
  function fieldFor(z) {
    if (fields[z]) return fields[z];
    const prev = new Int32Array(tiles.length).fill(-1);
    const seen = new Uint8Array(tiles.length);
    const src = roots[z];
    const q = [src]; seen[src] = 1;
    for (let h = 0; h < q.length; h++)
      for (const v of nbr[q[h]]) if (!seen[v]) { seen[v] = 1; prev[v] = q[h]; q.push(v); }
    return (fields[z] = { prev, seen });
  }

  const cache = {};
  /** The full walk: doorstep, kerb, streets, far kerb, destination. Returned
   *  as a polyline with cumulative lengths so a position at any fraction of
   *  the journey is a lookup rather than a re-walk. */
  function route(p1, p2, toZone) {
    const a = kerbAt(p1.tx, p1.ty);
    if (a < 0 || !ZONES[toZone]) return null;
    const key = a + '>' + toZone;
    let mid = cache[key];
    if (mid === undefined) {
      const f = fieldFor(toZone);
      mid = [];
      if (f.seen[a]) {
        let u = a, guard = 0;
        while (u !== -1 && guard++ < 4000) {
          mid.push([tiles[u][0] + 0.5, tiles[u][1] + 0.5]);
          u = f.prev[u];
        }
        // one smoothing pass takes the staircase off a 4-connected path
        for (let i = 1; i < mid.length - 1; i++)
          mid[i] = [(mid[i - 1][0] + mid[i][0] * 2 + mid[i + 1][0]) / 4,
                    (mid[i - 1][1] + mid[i][1] * 2 + mid[i + 1][1]) / 4];
      }
      cache[key] = mid;
    }
    if (!mid.length) return null;
    const pts = [[p1.tx, p1.ty], ...mid, [p2.tx, p2.ty]];
    const cum = [0];
    for (let i = 1; i < pts.length; i++)
      cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    return { pts, cum, total: cum[cum.length - 1] };
  }
  return { route, kerbAt, tiles };
}

function walkRoute(p1, p2, f, toZone) {
  const r = toZone && walkNet().route(p1, p2, toZone);
  if (r && r.total > 0.001) {
    const d = f * r.total;
    let i = 1;
    while (i < r.cum.length - 1 && r.cum[i] < d) i++;
    const seg = r.cum[i] - r.cum[i - 1];
    const g = seg < 0.0001 ? 1 : (d - r.cum[i - 1]) / seg;
    return { tx: r.pts[i - 1][0] + (r.pts[i][0] - r.pts[i - 1][0]) * g,
             ty: r.pts[i - 1][1] + (r.pts[i][1] - r.pts[i - 1][1]) * g };
  }
  // Fallback for a district the network cannot reach: the old corridor.
  const via = { tx: p2.tx, ty: p1.ty };
  const legA = Math.abs(via.tx - p1.tx), legB = Math.abs(p2.ty - via.ty);
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

const SHIFT_TRAVEL = 1;                       // hours walking to and from a shift
const wrap24 = h => ((h % 24) + 24) % 24;
/** Where a shift worker is when they are neither on duty nor travelling.
 *  Waking counts as travelling too: an evening-shift nurse used to be in bed
 *  at 07:59 and standing in the town square at 08:00. */
function shiftRestPos(a, hourFrac, hod) {
  if (shiftAsleep(a, hod)) return { pos: zoneAnchor('housing', a, true), zone: 'housing' };
  const dest = taskPos(a, a.eveningZone, hourFrac, 'off');
  const wake = ((hod - SHIFTS[a.shift].sleep[1]) % 24 + 24) % 24;
  if (wake < SHIFT_TRAVEL) {
    const home = zoneAnchor('housing', a, true);
    const f = wake / SHIFT_TRAVEL;
    return { pos: walkRoute(home, dest, paceEase(f), a.eveningZone),
             zone: f < 0.5 ? 'housing' : a.eveningZone, moving: true };
  }
  return { pos: dest, zone: a.eveningZone, moving: !!dest.walking };
}

/* ===========================================================================
   LIVE MODE - real positions from the running engine, animated in real time
   ===========================================================================
   Two independent things have to be true for a resident to "really" move:

   (b) engine side - world/core/movement.MovementSystem has to actually step
       their route. It does now: both composition roots (world/run_genesis.py
       and world/wiring.py) hand it to TickEngine, so `advance()` runs once
       per tick and `agent.moved` carries a real tile.

   (a) viewer side - this block. The township draws its own 104x104 town,
       NOT the Ville map the engine paths over, so an engine tile cannot be
       plotted here (see game/position.py's township_zone_for). What crosses
       the boundary is the named place. When the engine reports a resident
       somewhere new, the viewer does not teleport them: it starts a real
       walk along the same street network every scripted commute uses, and
       animates it frame by frame over wall-clock seconds.

   Everything degrades to the built-in schedule. No world server, a 502, a
   mock world whose ids do not match genesis, or a location this town has no
   district for - each simply leaves that resident scripted, which is what
   the page does today with no server at all.
   ======================================================================== */

const LIVE = {
  url: 'api/township/live',
  pollMs: 2000,
  // Live from load - see below.
  // On from load. It used to start false because the page opened on
  // "Mock run"; there is no mock run any more, so anything but true
  // would just be a map that ignores the world it is pointed at.
  enabled: true,
  connected: false,
  tick: null,
  bound: 0,
  zone: Object.create(null),      // agentId -> township zone id
  action: Object.create(null),    // agentId -> what the engine says they are doing
  // Part E. Declared here rather than sprung into existence on the first
  // poll, so the Skills tab can render "nothing yet" before one has landed
  // instead of throwing on an undefined lookup.
  skills: Object.create(null),    // agentId -> [HeldSkillView]
  following: Object.create(null), // agentId -> {skill, step, total}
  identity: Object.create(null),  // agentId -> {name, age, occ} per the world
  // UI-04. What the WORLD holds for this resident, in sim and in cents.
  // Absent means the world did not say and the model may answer; a number
  // means it did, and the model may not.
  balance: Object.create(null),   // agentId -> sim_balance
  usdCents: Object.create(null),  // agentId -> usd_wallet_cents
  // UI-04. {prime_minister, ministers, terms_served} as the charter store
  // has them, or null. null and "no seats filled" are different facts: the
  // first means nobody asked or the route is absent, the second means the
  // town has held no election, and only the first may fall back.
  government: null,
  walk: Object.create(null),      // agentId -> in-progress viewer walk
  lastError: null,
  // The three provenance axes, declared rather than sprung into
  // existence on the first successful poll, so the badge's state is
  // readable before one has happened. All three are needed and none
  // implies another: 'world' rows can be served by mock engines, and
  // real engines can be fed canned completions.
  source: null,      // 'mock' | 'world'
  cognition: null,   // 'engine' | 'mock' | 'none'
  inference: null,   // 'bedrock' | 'mock'
};

// How long a live walk takes on screen. Real seconds, scaled by distance and
// by the clock speed, so a live resident keeps pace with a scripted one
// instead of drifting out of step when the operator hits 6x.
// Live walks are timed in real seconds rather than off the clock, so they
// need the same slowing by hand or an engine-driven resident sprints past a
// scripted one going the same way.
const LIVE_TILES_PER_SEC = 0.62;
const LIVE_MIN_WALK_S = 1.2;
const LIVE_MAX_WALK_S = 26;

/** The live layer's answer for one resident, or null to stay scripted. */
function livePosition(a, hourFrac) {
  if (!LIVE.enabled || !LIVE.connected) return null;
  const target = LIVE.zone[a.id];
  if (!target || !ZONES[target]) return null;

  // Keyed on the WORLD's location, not the district it falls in.
  //
  // The township draws about a dozen zones and the world has far more
  // places than that, so most real moves stay inside one zone -
  // farmland:wheat to farmland:cabbage, or one shop to another on market
  // row. Watching only the zone, the sprite never moved and the town looked
  // frozen while every agent was walking somewhere.
  //
  // The seed is the location id, so two residents in the same zone at
  // different places stand at different points, and an agent that really
  // did not move does not drift.
  const where = (LIVE.where && LIVE.where[a.id]) || target;

  let st = LIVE.walk[a.id];
  if (!st) {
    // First sighting: stand them where the engine says, no walk to animate.
    const at = taskPos(a, target, hourFrac, 'live:' + where);
    st = LIVE.walk[a.id] = {
      zone: target, where: where,
      from: { tx: at.tx, ty: at.ty }, to: at, t0: 0, dur: 0,
    };
  }
  if (st.zone !== target || st.where !== where) {
    // The engine moved them. Walk, do not jump.
    const from = livePointOf(st);
    const to = taskPos(a, target, hourFrac, 'live:' + where);
    const dist = Math.hypot(to.tx - from.tx, to.ty - from.ty);
    const speedScale = Math.max(1, state.speed || 1);
    st.from = from;
    st.to = to;
    st.zone = target;
    st.where = where;
    st.t0 = performance.now();
    st.dur = Math.min(LIVE_MAX_WALK_S,
                      Math.max(LIVE_MIN_WALK_S, dist / LIVE_TILES_PER_SEC)) * 1000 / speedScale;
  }

  if (st.dur <= 0) return { pos: st.to, zone: st.zone, moving: false, live: true };
  const f = Math.min(1, (performance.now() - st.t0) / st.dur);
  if (f >= 1) return { pos: st.to, zone: st.zone, moving: false, live: true };
  return {
    pos: walkRoute(st.from, st.to, paceEase(f), st.zone),
    zone: st.zone, moving: true, live: true,
  };
}

/** Where a live walk currently is - used as the origin when a new order
 *  arrives mid-walk, so a resident redirected halfway across town carries on
 *  from where they had actually got to. */
function livePointOf(st) {
  if (!st || st.dur <= 0) return st ? { tx: st.to.tx, ty: st.to.ty } : { tx: 0, ty: 0 };
  const f = Math.min(1, (performance.now() - st.t0) / st.dur);
  const p = f >= 1 ? st.to : walkRoute(st.from, st.to, paceEase(f), st.zone);
  return { tx: p.tx, ty: p.ty };
}

async function pollLive() {
  if (!LIVE.enabled) return;
  // Opened straight off disk (file://), there is no server to ask and the
  // browser blocks the request as cross-origin. build_characters.py bundles
  // the cast as JS precisely so this page works off disk, so polling here
  // would only fill the console with CORS errors every two seconds.
  if (location.protocol === 'file:') {
    LIVE.enabled = false;
    LIVE.lastError = 'opened from disk (file://) - no server to poll';
    renderLiveBadge();
    return;
  }
  try {
    const res = await fetch(LIVE.url, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (!data || !data.connected) {
      LIVE.connected = false; LIVE.bound = 0;
      // UI-04: forget the cabinet too. A world that drops out otherwise
      // leaves its last ministers wearing sashes on the map forever.
      LIVE.government = null;
      LIVE.lastError = (data && data.detail) || 'world server not connected';
    } else {
      const zone = Object.create(null), action = Object.create(null);
      // UI-03. Whether a resident is alive is the WORLD's fact, and it has
      // been on this row the whole time (server.py: "alive": agent.is_alive).
      // The page ignored it and consulted its own `healthSim` instead - see
      // the draw gate in tick().
      const alive = Object.create(null);
      const skills = Object.create(null), following = Object.create(null);
      const identity = Object.create(null);
      const doing = Object.create(null);
      const recent = Object.create(null);
      const approx = Object.create(null);
      const where = Object.create(null);
      // UI-04. The world's money, on the same row as the position - the
      // wealth leaderboard used to rank residents by a wage table this file
      // invented at build time while this number sat unread on the poll.
      const balance = Object.create(null);
      const usdCents = Object.create(null);
      let bound = 0;
      for (const row of (data.agents || [])) {
        if (!row.zone || !AGENT_BY_ID[row.id]) continue;   // unknown id or place
        zone[row.id] = row.zone;
        if (row.action) action[row.id] = row.action;
        // What the resident is actually DOING, from their own completed
        // calls - not inferred from health. A healthy, busy farmer used to
        // read as a blank card because dormancy and illness were the only
        // two things this row carried.
        if (row.doing) doing[row.id] = { line: row.doing, tick: row.doing_tick };
        if (row.recent && row.recent.length) recent[row.id] = row.recent;
        // False means "drawn, but the district is a guess" - the card says
        // so rather than asserting a position the town never gave.
        if (row.zone_exact === false) approx[row.id] = true;
        if (row.location_id) where[row.id] = row.location_id;
        alive[row.id] = row.alive !== false;
        // `!= null` and not a truth test: a resident the world says holds
        // ZERO sim is a fact about the run, and `0 || fallback` would throw
        // it away and print the model's guess instead.
        if (row.sim_balance != null) balance[row.id] = row.sim_balance;
        if (row.usd_wallet_cents != null) usdCents[row.id] = row.usd_wallet_cents;
        // Part E. These arrive on the SAME row as the zone above - one poll,
        // one instant - so the step an agent is on and where it is standing
        // can never disagree by a tick.
        if (row.skills && row.skills.length) skills[row.id] = row.skills;
        if (row.following) following[row.id] = row.following;
        // Who the WORLD says this is. The viewer's built-in roster is the
        // 314-resident genesis; a run against a subset renumbers the town,
        // so the same id is a different person. An engine-bound resident is
        // identified by the run - carrying the model's name beside real
        // skills would put one agent's authored workflow under another
        // agent's face.
        if (row.name) identity[row.id] = {
          name: row.name, age: row.age_years, occ: row.occupation, sex: row.sex,
        };
        // UI-04. Put the world's trade back on the record.
        //
        // `retrade` and `staffThePosts` run when the cast is BUILT, before
        // any world has been polled, and they rewrite `a.occ` outright to
        // keep the model's institutions staffed - a genesis day_laborer
        // becomes a police_officer, and then reads as one on the People
        // card, the place card, the roster filter and the map costume,
        // because 77 sites read `a.occ` directly.
        //
        // Correcting the record on the poll is the same move `live-cast.js`
        // already makes for names (adoptWorldNames): one write, and every
        // reader is right. The alternative was an `occOf()` accessor and 77
        // call sites to remember to use, which is the arrangement that
        // produced this finding in the first place.
        //
        // `_castOcc` keeps what the model had, so a reader who saw the old
        // trade is not left wondering where it went - exactly as
        // `castName` does.
        const rec = AGENT_BY_ID[row.id];
        if (rec && row.occupation && rec.occ !== row.occupation) {
          rec._castOcc = rec._castOcc || rec.occ;
          rec.occ = row.occupation;
          // The fields DERIVED from a trade have to move with it, or the
          // card names one job and the workplace line another.
          rec.dz = null;
          rec.dayZone = OCC_HOME_ZONE[row.occupation] || rec.dayZone;
          if (WAGE[row.occupation] !== undefined) rec.wage = WAGE[row.occupation];
        }
        bound++;
      }
      LIVE.zone = zone; LIVE.action = action;
      LIVE.skills = skills; LIVE.following = following; LIVE.identity = identity;
      LIVE.doing = doing; LIVE.recent = recent; LIVE.approx = approx;
      LIVE.where = where; LIVE.alive = alive;
      LIVE.balance = balance; LIVE.usdCents = usdCents;
      // UI-04. `undefined` (no such key) and `null` (asked, nothing there)
      // both become null here, because the viewer's rule is the same for
      // both: with no answer from the world it may not invent five seats.
      LIVE.government = data.government || null;
      LIVE.bound = bound; LIVE.tick = data.tick;
      LIVE.cognition = data.cognition || null;
      // Third axis. cognition says the ENGINES are real; this says the
      // COMPLETIONS are. Real engines fed canned text look identical
      // without it, and the badge above would call that REAL AGENTS.
      LIVE.inference = data.inference || null;
      LIVE.source = data.source || null;
      LIVE.connected = bound > 0;
      LIVE.lastError = bound ? null : 'connected, but no resident id matched genesis';
    }
    // Publish, rather than let part-e.js open its own poll of the same URL.
    // Two pollers would drift apart and the Workshop tab would end up
    // describing a different tick from the roster beside it.
    document.dispatchEvent(new CustomEvent('kasukabe:live', { detail: {
      connected: LIVE.connected, tick: LIVE.tick, bound: LIVE.bound,
      skills: LIVE.skills || {}, following: LIVE.following || {},
    } }));
  } catch (err) {
    LIVE.connected = false; LIVE.bound = 0;
    LIVE.government = null;   // UI-04: and the cabinet with it
    LIVE.lastError = String((err && err.message) || err);
  }
  renderLiveBadge();
}

/* Is this resident really running, or is the page modelling them?
 *
 * UI-04. The rule this file follows everywhere it has been fixed - see
 * `activityFor` and `dayPlaceLineFor`, which the review points at as "the
 * pattern that fixes it" - is that a bound resident's facts come from the
 * world and an unbound one's come from the model below. That test was
 * written out longhand at each site, so each new site was free to forget
 * it, and most of them did: the Government book named five hash-picked
 * clerks as ministers, the wealth leaderboard ranked a wage table, the
 * People cards billed engine agents "wage +125 - upkeep -55 - tax -30 /
 * day", and `retrade` rewrote a genesis day_laborer into a police officer
 * on the card, the place card and the map costume.
 *
 * Being in `LIVE.zone` is the test rather than `LIVE.connected` alone: a
 * connected world still says nothing about a resident whose id is not in
 * its genesis, and those must keep the model or they freeze on the map.
 */
function isBound(agentOrId) {
  const id = (agentOrId && agentOrId.id) || agentOrId;
  return !!(LIVE.connected && LIVE.zone && LIVE.zone[id]);
}

/* Is a world bound at all? The town-wide fictions - births, food stock,
 * the hunger deaths - are not per-resident, so they turn on this. */
function worldIsBound() {
  return !!(LIVE.connected && LIVE.bound > 0);
}

/* ---- run mode, and what the real one costs ------------------------------
 * Two modes, and the page must never be vague about which one it is in.
 *
 *   Mock run  - the town runs on its own schedule. No model is called and
 *               nothing is spent. This is what runs by default.
 *   Real sim  - agents decide for themselves, each in its own engine, through
 *               the broker to Bedrock. This spends real money per tick.
 *
 * The viewer cannot START a real run - that is `./start-genesis.sh --live`,
 * a process on the operator's machine, and a browser button that could spend
 * money by itself would be a bad idea even if it were possible. What these
 * buttons do is choose which world the page BINDS to, and say plainly which
 * one is answering. `cognition` comes from the world server itself
 * ("engine" | "mock" | "none"), so the badge reflects what is really behind
 * the agents rather than what the page was asked to show.
 */
// UI-07. `runMode` was the constant `'real'`, and `renderLiveBadge`
// branched on it - so the whole `else` arm, the one that says SIMULATED
// SCHEDULE when no world server is bound, was unreachable. The page's own
// HTML defaults to that text (township-v2.html:76), so an operator with no
// world running saw "SIMULATED SCHEDULE" until the first poll and then a
// live badge that could never go back.
//
// Deleted rather than kept: the question the badge answers is "is a world
// bound", and `LIVE.connected` answers it truthfully. A constant that
// always says yes is not a mode, it is a comment.
const COST = { live: false, micros: null, usd: null, cap: null, state: null };

// setRunMode is gone with the toggle. It existed to flip LIVE.enabled
// between the town's own schedule and the engines; there is only one
// answer now, set on the LIVE literal above.

async function pollCost() {
  const el = document.getElementById('costReadout');
  if (!el) return;
  if (location.protocol === 'file:') { el.hidden = true; return; }
  try {
    const res = await fetch('api/cost', { cache: 'no-store' });
    const d = await res.json();
    COST.live = !!d.live;
    COST.micros = d.spent_micros;
    COST.usd = d.spent_usd;
    COST.cap = d.cap_usd;
    COST.state = d.state;
  COST.model = d.model || null;
  COST.exclusive = !!d.model_is_exclusive;
  COST.brokerCap = d.broker_cap_usd;
    COST.reason = d.reason || null;
  } catch (err) {
    COST.live = false; COST.reason = String((err && err.message) || err);
  }
  renderCost();
}

function renderCost() {
  const el = document.getElementById('costReadout');
  if (!el) return;
  const label = document.getElementById('costLabel');
  const figure = document.getElementById('costFigure');
  const micros = document.getElementById('costMicros');
  const usd = document.getElementById('costUsd');
  const modelEl = document.getElementById('costModel');

  // Three states, each a sentence rather than a dash. The old empty state
  // read "— µ$ no broker", which is three fragments and no meaning: a dash
  // where a number goes, a unit for a number that is not there, and an error
  // where the total belongs.
  if (!COST.live) {
    el.className = 'cost-readout idle';
    if (figure) figure.hidden = true;
    if (modelEl) modelEl.textContent = '';
    if (label) {
      label.hidden = false;
      label.textContent = 'Not spending';
    }
    el.title = 'Nothing is being billed. The broker is not running, so no model ' +
               'is being called.\n' + (COST.reason || '') +
               '\nStart one with: make run-broker';
    return;
  }

  const m = COST.micros == null ? 0 : COST.micros;
  el.className = 'cost-readout live' + (m > 0 ? ' spending' : '');
  if (label) label.hidden = true;
  if (figure) figure.hidden = false;
  if (micros) micros.textContent = m.toLocaleString();
  if (usd) {
    // Dollars only once there are dollars to show. A tick costs a fraction of
    // a cent, so "$0.0000" beside a live micro count is noise.
    const d = COST.usd == null ? 0 : COST.usd;
    usd.textContent = d >= 0.01
      ? '$' + d.toFixed(2) + (COST.cap ? ' of $' + COST.cap.toFixed(2) : '')
      : (COST.cap ? 'cap $' + COST.cap.toFixed(2) : '');
  }
  // WHICH model is spending, beside how much. The wrong model leading is
  // silent and costs 3.6x, so the figure says whose it is. The dot marks a
  // model serving under ONLY_MODEL - one model and no overflow - because
  // "gpt-6-luna is in front" and "nothing else may serve" are different
  // guarantees and only the second is worth trusting a budget to.
  if (modelEl) {
    modelEl.textContent = COST.model ? (COST.exclusive ? '\u25CF ' : '') + COST.model : '';
    modelEl.title = COST.model
      ? (COST.exclusive
          ? COST.model + ' is the ONLY model this run may use. Nothing overflows to another provider.'
          : COST.model + ' leads; other models may still serve overflow.')
      : '';
  }
  el.title = 'Real spend, from the broker\'s governor. Accounted in nano-dollars ' +
             'and never rounded per call; 1,000,000 \u00B5$ = $1.00.' +
             (COST.state ? '\nGovernor: ' + COST.state : '');
}

function wireRunMode() {
  const m = document.getElementById('modeMock');
  const r = document.getElementById('modeReal');
  // No mock button to wire any more.
  // Real sim asks before it spends. If a run is already going the dialog
  // says so and offers to stop it, rather than starting a second one.
  if (r) r.addEventListener('click', () => openRealsimDialog());
  const badge = document.getElementById('liveBadge');
  if (badge) {
    badge.classList.add('clickable');
    badge.setAttribute('role', 'button');
    badge.setAttribute('tabindex', '0');
    badge.addEventListener('click', () =>
      (enginePanelOpen ? hideEnginePanel() : showEnginePanel()));
    badge.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); showEnginePanel(); }
    });
  }
  pollCost();
  pollEngine();
  setInterval(pollCost, 3000);
  // Every 2s in real mode: when money is moving and engines are dying, a
  // six-second-old count is worse than no count. 8s otherwise - nothing on
  // this readout changes in mock mode, and it is a request per tick either way.
  setInterval(() => pollEngine(), 2000);
}

function renderLiveBadge() {
  // The empty-town notice tracks the same fact the badge does: how many
  // residents the engine is actually reporting.
  const empty = document.getElementById('emptyTown');
  if (empty) empty.hidden = !!(LIVE.connected && LIVE.bound > 0);

  const el = document.getElementById('liveBadge');
  if (!el) return;
  // In Real sim the badge stops being a status light and becomes the headline
  // number: how many of the residents on this map are actually running on
  // Bedrock right now. That is the question the mode is chosen to answer, so
  // it should not require opening a panel to see.
  // UI-07: the real question, not a constant. When nothing is bound the
  // `else` below is now reachable and says so.
  if (LIVE.connected) {
    const text = renderEngineBadgeText();
    if (text) {
      el.className = 'live-badge on real';
      el.textContent = text;
      el.title = 'Each of these residents is deciding for itself in its own ' +
                 'engine, through the broker to Bedrock.\nClick for the full ' +
                 'breakdown: lanes, models and spend.';
    } else {
      // Not connected is the NORMAL state before a run is started, so it says
      // what is missing and what to run, rather than glowing red.
      const b = engineBlocker();
      el.className = 'live-badge off';
      el.textContent = 'REAL SIM \u00b7 NOT CONNECTED';
      el.title = (b ? b.what + '\n\nNext: ' + b.fix : (ENGINE.err || '')) +
                 '\n\nClick for the full breakdown.';
    }
    return;
  }

  if (LIVE.connected) {
    // `cognition` is the world server's own answer about what is behind its
    // agents. A mock world can report ids that look real; only this says
    // whether anything is actually thinking.
    // TWO independent conditions, both required. cognition === "engine"
    // only means real sim_main subprocesses exist; the broker can still be
    // in MOCK_MODE, handing every one of them the literal string "mock
    // response". That run has real processes, real world state and real
    // wallet debits - and nothing thinking. Calling it REAL AGENTS was the bug.
    const engines = LIVE.cognition === 'engine';
    const real = engines && LIVE.inference === 'bedrock';
    const counts = LIVE.bound + ' of ' + AGENTS.length + ' bound' +
                   (LIVE.tick != null ? ' \u00b7 tick ' + LIVE.tick : '');
    let label, why;
    if (real) {
      label = 'REAL AGENTS';
      why = 'Each resident is deciding for itself in its own engine, through the broker to Bedrock.';
    } else if (engines && LIVE.inference === 'mock') {
      label = 'REAL ENGINES \u00b7 MOCK MINDS';
      why = 'PARTLY REAL. The engines and the world are genuine, but the broker is in\n' +
            'MOCK_MODE: every completion is the literal string "mock response".\n' +
            'No model is reasoning. Set credentials and MOCK_MODE=false on the broker.';
    } else if (engines) {
      label = 'REAL ENGINES \u00b7 INFERENCE UNKNOWN';
      why = 'Real engine subprocesses, but the world did not report whether\n' +
            'completions are real. Treat as UNVERIFIED, not as real.';
    } else {
      label = 'LIVE (MOCK COGNITION)';
      why = 'Positions are live from a world server, but its agents are not engine-backed \u2014 ' +
            'nothing is thinking behind them.';
    }
    el.className = 'live-badge ' + (real ? 'on real' : 'on');
    el.textContent = label + ' \u00b7 ' + counts;
    el.title = why;
  } else {
    el.className = 'live-badge off';
    el.textContent = 'SIMULATED SCHEDULE';
    el.title = 'No live world server bound. ' + (LIVE.lastError || '') +
               '\nStart one with: make run-world (see docs/HANDOVER.md)';
  }
}

/* ---- the real sim: what is actually running out there ---------------------
 *
 * "Real sim" is a claim about the world, so the page has to be able to back it
 * up with numbers rather than a coloured pill. Three separate things have to be
 * true before a single resident is thinking for itself, and each fails
 * independently and silently:
 *
 *   1. a world server is up and the viewer can match its agent ids to the cast
 *      it draws (`bound`);
 *   2. the agents in that world are backed by real engine subprocesses rather
 *      than MockAgentGateway (`cognition === "engine"`);
 *   3. the broker is up, holds credentials, and its lanes are closed rather
 *      than circuit-broken (`broker.live`, per-lane `circuit`).
 *
 * All three answers come from ONE poll of /api/engine, so the counts shown side
 * by side were all true at the same instant. Assembling them from three polls
 * is how you end up reporting 30 agents against a spend figure from four
 * seconds earlier.
 */
const ENGINE = { ok: false, world: null, broker: null, models: null,
                 realsim: null, err: null };

async function pollEngine() {
  if (location.protocol === 'file:') return;      // no server to ask
  try {
    const res = await fetch('api/engine', { cache: 'no-store' });
    // A 404 body still parses as JSON - FastAPI answers an unknown route with
    // {"detail":"Not Found"} - so parsing successfully proves nothing. Without
    // both checks a game server too old to have this route reported "no world
    // server is running", which is a different problem with a different fix
    // and sends you off restarting the wrong process.
    if (!res.ok) throw new Error('api/engine returned HTTP ' + res.status +
      (res.status === 404 ? ' - this game server predates the Real sim readout; restart it' : ''));
    const d = await res.json();
    if (!d || typeof d.world !== 'object' || d.world === null)
      throw new Error('api/engine answered with an unexpected shape');
    ENGINE.world = d.world; ENGINE.broker = d.broker || {}; ENGINE.models = d.models || {};
    ENGINE.realsim = d.realsim || null;
    ENGINE.ok = true; ENGINE.err = null;
  } catch (err) {
    ENGINE.ok = false; ENGINE.err = String((err && err.message) || err);
    ENGINE.world = null; ENGINE.broker = null;
  }
  renderLiveBadge();
  if (enginePanelOpen) renderEnginePanel();
}

/** Is every part of the chain up? The badge and the panel must agree, so both
 *  ask this rather than each deciding for itself. */
function engineIsLive() {
  return !!(ENGINE.ok && ENGINE.world && ENGINE.world.cognition === 'engine' &&
            ENGINE.world.bound > 0);
}

/** The first thing that is missing, in the order it has to be fixed. Naming
 *  ONE step is the difference between a readout you can act on and a red pill:
 *  a run with no world server and no broker has two problems, but only one of
 *  them is the next thing to do. */
function engineBlocker() {
  if (!ENGINE.ok) return { what: 'The page cannot read the run status from the game server.',
                           fix: ENGINE.err || 'uv run uvicorn game.server:app --port 8400' };
  const w = ENGINE.world || {};
  if (!w.connected) return { what: 'No world server is running.',
                             fix: 'make run-world  (see docs/HANDOVER.md \u00a7 8.3)' };
  if (w.cognition !== 'engine')
    return { what: 'The world is up, but nothing is thinking behind its agents' +
                   ' (cognition: ' + (w.cognition || 'unknown') + ').',
             fix: 'Start the world with real engines: run_genesis.py --live' };
  if (!w.bound) return { what: 'The world reports agents, but none of their ids ' +
                               'match a resident on this map.',
                         fix: 'Check the genesis file the world booted from.' };
  const b = ENGINE.broker || {};
  if (!b.live) return { what: 'No broker, so nothing can reach Bedrock.',
                        fix: 'make run-broker' };
  return null;
}

function renderEngineBadgeText() {
  const w = ENGINE.world || {}, b = ENGINE.broker || {};
  if (!engineIsLive()) return null;
  const parts = [w.bound + ' of ' + AGENTS.length + ' on Bedrock'];
  if (w.acting) parts.push(w.acting + ' acting');
  if (w.dormant) parts.push(w.dormant + ' dormant');
  if (w.tick != null) parts.push('tick ' + w.tick);
  if (!b.live) parts.push('NO BROKER');
  return parts.join(' \u00b7 ');
}

/* ---- starting a real run ------------------------------------------------
 * Real sim is not a view toggle - it starts a FRESH town on real Bedrock and
 * begins spending, so it asks first. Two questions, because they are the two
 * that decide what the run measures:
 *
 *   how many agents  - cost and contention scale with the cast;
 *   how much money each starts with - the whole point of a survival run is
 *     that everyone starts level, so this is one figure for all of them.
 *
 * The ceiling on the purse is arithmetic the page must not invent: the town
 * holds a fixed $1000, so the largest equal purse is $1000/cast. It comes
 * from /api/realsim/defaults for that reason.
 */
let REALSIM = { agents: 50, wallet_cents: 200, budget_cap_cents: 100,
                min_agents: 2, max_agents: 250, town_total_cents: 100000,
                status: null, busy: false };

function realsimMaxWallet(agents) {
  return Math.floor(REALSIM.town_total_cents / Math.max(1, agents));
}

async function loadRealsimDefaults() {
  try {
    const res = await fetch('api/realsim/defaults', { cache: 'no-store' });
    if (!res.ok) return false;
    const d = await res.json();
    REALSIM = Object.assign(REALSIM, d);
    return true;
  } catch (err) { return false; }
}

function realsimDialogHtml() {
  const maxW = realsimMaxWallet(REALSIM.agents);
  const st = REALSIM.status || {};
  return '' +
  '<div class="rs-backdrop" id="rsBackdrop"></div>' +
  '<div class="rs-dialog" role="dialog" aria-modal="true" aria-labelledby="rsTitle">' +
    '<button class="mc-close" id="rsClose" type="button" aria-label="Close">×</button>' +
    '<h2 id="rsTitle">Start a real run</h2>' +
    '<p class="rs-sub">A fresh town on AWS Bedrock. Every agent thinks for ' +
      'itself, and this spends real money.</p>' +
    (st.running
      ? '<div class="rs-warn">A run is already going (' + st.agents + ' agents). ' +
        'Starting a new one replaces it.</div>' : '') +
    '<label class="rs-field"><span class="rs-k">How many agents</span>' +
      '<input type="number" id="rsAgents" min="' + REALSIM.min_agents + '" max="' +
        REALSIM.max_agents + '" step="1" value="' + REALSIM.agents + '">' +
      '<em class="rs-note">' + REALSIM.min_agents + '–' + REALSIM.max_agents +
        '. Cost and contention scale with the cast.</em></label>' +
    '<label class="rs-field"><span class="rs-k">Starting money, each</span>' +
      '<input type="number" id="rsMoney" min="0" step="0.01" value="' +
        (REALSIM.wallet_cents / 100).toFixed(2) + '">' +
      '<em class="rs-note" id="rsMoneyNote">Everyone starts level. Maximum $' +
        (maxW / 100).toFixed(2) + ' each — the town holds a fixed $' +
        (REALSIM.town_total_cents / 100).toFixed(0) + ' between them.</em></label>' +
    '<label class="rs-field"><span class="rs-k">Run until this many are left</span>' +
      '<input type="number" id="rsUntil" min="0" step="1" value="' +
        (REALSIM.until_survivors == null ? 1 : REALSIM.until_survivors) + '">' +
      '<em class="rs-note">1 = run until the last one standing. 0 = no ' +
        'survival condition, stop only at the spend cap.<br>' +
        'The town must burn roughly <b>cast x starting money</b> to get there ' +
        '\u2014 everyone spends their own purse thinking, and at $0 they go ' +
        'dormant and then die.</em></label>' +
    '<label class="rs-field"><span class="rs-k">Stop spending after</span>' +
      '<input type="number" id="rsBudget" min="0.01" step="0.01" value="' +
        (REALSIM.budget_cap_cents / 100).toFixed(2) + '">' +
      '<em class="rs-note">A hard cap. The broker stops dispatching at it.</em></label>' +
    '<div class="rs-err" id="rsErr" hidden></div>' +
    '<div class="rs-actions">' +
      '<button class="ctl" id="rsCancel" type="button">Cancel</button>' +
      (st.running ? '<button class="ctl" id="rsStop" type="button">Stop the run</button>' : '') +
      '<button class="ctl primary" id="rsGo" type="button">Start the run</button>' +
    '</div>' +
  '</div>';
}

function closeRealsimDialog() {
  const host = document.getElementById('rsHost');
  if (host) host.remove();
}

function showRealsimError(msg) {
  const el = document.getElementById('rsErr');
  if (!el) return;
  el.hidden = false;
  el.textContent = msg;
}

async function openRealsimDialog() {
  await loadRealsimDefaults();
  closeRealsimDialog();
  const host = document.createElement('div');
  host.id = 'rsHost';
  host.innerHTML = realsimDialogHtml();
  document.body.appendChild(host);

  const agentsEl = document.getElementById('rsAgents');
  const moneyEl = document.getElementById('rsMoney');
  const noteEl = document.getElementById('rsMoneyNote');

  // The ceiling moves with the cast size, so it is recomputed as you type
  // rather than checked only on submit.
  const refreshNote = () => {
    const n = Math.max(1, parseInt(agentsEl.value, 10) || 1);
    const maxW = realsimMaxWallet(n);
    noteEl.textContent = 'Everyone starts level. Maximum $' + (maxW / 100).toFixed(2) +
      ' each — the town holds a fixed $' +
      (REALSIM.town_total_cents / 100).toFixed(0) + ' between them.';
    moneyEl.max = (maxW / 100).toFixed(2);
  };
  agentsEl.addEventListener('input', refreshNote);

  document.getElementById('rsClose').addEventListener('click', closeRealsimDialog);
  document.getElementById('rsCancel').addEventListener('click', closeRealsimDialog);
  document.getElementById('rsBackdrop').addEventListener('click', closeRealsimDialog);
  const stopBtn = document.getElementById('rsStop');
  if (stopBtn) stopBtn.addEventListener('click', async () => {
    stopBtn.disabled = true; stopBtn.textContent = 'Stopping…';
    await fetch('api/realsim/stop', { method: 'POST' }).catch(() => {});
    closeRealsimDialog();
  });

  document.getElementById('rsGo').addEventListener('click', async () => {
    const go = document.getElementById('rsGo');
    const agents = parseInt(agentsEl.value, 10);
    const walletCents = Math.round(parseFloat(moneyEl.value) * 100);
    const budgetCents = Math.round(parseFloat(document.getElementById('rsBudget').value) * 100);
    const untilSurvivors = parseInt(document.getElementById('rsUntil').value, 10);
    if (!isFinite(agents) || !isFinite(walletCents) || !isFinite(budgetCents)) {
      showRealsimError('Fill in all three numbers.'); return;
    }
    go.disabled = true; go.textContent = 'Starting…';
    try {
      const res = await fetch('api/realsim/start', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agents: agents, wallet_cents: walletCents,
                               budget_cap_cents: budgetCents,
                               until_survivors: isFinite(untilSurvivors) ? untilSurvivors : 1 })
      });
      const d = await res.json();
      if (!d.ok) {
        showRealsimError(d.error || 'The run could not be started.');
        go.disabled = false; go.textContent = 'Start the run';
        return;
      }
      REALSIM.agents = agents; REALSIM.wallet_cents = walletCents;
      REALSIM.budget_cap_cents = budgetCents;
      closeRealsimDialog();
      showEnginePanel();
    } catch (err) {
      showRealsimError(String((err && err.message) || err));
      go.disabled = false; go.textContent = 'Start the run';
    }
  });
}

/* ---- the real sim panel --------------------------------------------------
 * Rendered into the same right-hand card a place or a resident uses, so it
 * opens, closes and scrolls exactly like everything else on the page.
 */
let enginePanelOpen = false;

function showEnginePanel() {
  enginePanelOpen = true;
  renderEnginePanel();
  // The right dock itself is opened by shell.js, which wraps this function
  // for exactly that purpose - the same way it wraps selectPlace. Reaching
  // into the dock from here would give the panel a second, competing owner.
}

function hideEnginePanel() {
  enginePanelOpen = false;
  const el = document.getElementById('placeCard');
  if (el) el.hidden = true;
}

function engineStatRow(label, value, note) {
  return '<div class="eng-row"><span class="eng-k">' + label + '</span>' +
         '<span class="eng-v">' + value + '</span>' +
         (note ? '<span class="eng-n">' + note + '</span>' : '') + '</div>';
}

function renderEnginePanel() {
  const el = document.getElementById('placeCard');
  if (!el) return;
  const w = ENGINE.world || {}, b = ENGINE.broker || {}, m = ENGINE.models || {};
  const rs = ENGINE.realsim || null;
  const live = engineIsLive();
  const blocker = engineBlocker();

  // Lanes, one row each. A circuit-broken lane is the difference between
  // "slow" and "this model is not serving you at all", so it is never folded
  // into a single "broker ok".
  // Field names are the broker's, taken from a real /admin/lanes response:
  // lane_id, model, region, tokens_available, capacity, circuit_state,
  // consecutive_failures, trip_count. The first version guessed `circuit` and
  // `trips` from the shape of a test double and rendered "closed" for every
  // lane whatever its real state - a readout that cannot show the one thing
  // it exists to show. Fallbacks are kept so an older broker still renders.
  const lanes = (b.lanes || []).map(L => {
    const state = L.circuit_state || L.circuit || L.state || 'closed';
    const open = String(state).toLowerCase().indexOf('open') >= 0;
    const trips = L.trip_count != null ? L.trip_count : L.trips;
    const fails = L.consecutive_failures;
    return '<div class="eng-lane' + (open ? ' broken' : '') + '">' +
      '<span class="eng-lane-id">' + (L.model || '?') + '<em>@' + (L.region || '?') + '</em></span>' +
      '<span class="eng-lane-state">' + state + '</span>' +
      (L.tokens_available != null
        ? '<span class="eng-lane-tok">' + Math.floor(L.tokens_available) +
          (L.capacity ? '/' + L.capacity : '') + ' tok</span>' : '') +
      (trips ? '<span class="eng-lane-trip">' + trips + ' trips</span>' : '') +
      (fails ? '<span class="eng-lane-trip">' + fails + ' fails</span>' : '') +
      '</div>';
  }).join('');

  paint(el,
    '<button class="mc-close" id="placeCardClose" type="button" aria-label="Close">\u00d7</button>' +
    '<div class="pc-head">' +
      '<span class="pc-icon">' + (live ? '\u26a1' : '\u25cb') + '</span>' +
      '<span class="pc-id"><b>Real sim</b><em>' +
        (live ? 'agents are deciding for themselves' : 'not running \u2014 see below') +
      '</em></span>' +
    '</div>' +
    '<div class="pc-scroll">' +

    (blocker
      ? '<div class="eng-blocked"><b>' + blocker.what + '</b>' +
        '<code>' + blocker.fix + '</code></div>'
      : '') +

    '<div class="pc-group"><div class="section-lbl">In the environment</div>' +
      engineStatRow('Residents on the map', AGENTS.length, 'the cast this page draws') +
      engineStatRow('Reported by the world', w.reported != null ? w.reported : '\u2014',
                    'agents the world server holds') +
      engineStatRow('Connected and placed', w.bound != null ? w.bound : '\u2014',
                    'matched to a resident and drawn') +
      engineStatRow('Alive', w.alive != null ? w.alive : '\u2014', '') +
      engineStatRow('Acting this tick', w.acting != null ? w.acting : '\u2014',
                    'doing something, not just existing') +
      engineStatRow('Dormant', w.dormant != null ? w.dormant : '\u2014',
                    'asleep or out of budget') +
      engineStatRow('Tick', w.tick != null ? w.tick : '\u2014', '') +
    '</div>' +

    // The survival run's own scoreboard. `alive` here is the RUN's count,
    // not the world snapshot's, so it keeps reading correctly after the
    // watcher has stopped the run and the world has gone.
    (rs && rs.agents ? '<div class="pc-group"><div class="section-lbl">Survival</div>' +
      engineStatRow('Started with', rs.agents, 'each holding $' +
                    ((rs.wallet_cents || 0) / 100).toFixed(2)) +
      engineStatRow('Still alive', rs.alive != null ? rs.alive : '\u2014', '') +
      engineStatRow('Died', rs.dead || 0, 'out of money, then dormant, then gone') +
      engineStatRow('Runs until', rs.until_survivors != null
                    ? rs.until_survivors + ' left' : '\u2014', '') +
      (rs.finished_reason
        ? '<div class="eng-blocked"><b>Finished: ' + rs.finished_reason + '</b></div>'
        : '') +
    '</div>' : '') +

    '<div class="pc-group"><div class="section-lbl">Thinking</div>' +
      // Four distinct answers, not three. "none" is a real world server that
      // simply has no engines attached; null is a world that was never asked
      // or could not say. Collapsing them sends you to fix the wrong thing.
      engineStatRow('Cognition', w.cognition || 'unknown',
                    w.cognition === 'engine' ? 'real engine subprocesses'
                    : w.cognition === 'mock' ? 'faked \u2014 nothing is thinking'
                    : w.cognition === 'none' ? 'a real world, but no engines attached to it'
                    : 'the world did not say') +
      engineStatRow('Agent model', m.cognition || '\u2014', 'every decision a resident makes') +
      engineStatRow('Background model', m.background || '\u2014',
                    'reflection, summaries, importance scoring') +
    '</div>' +

    '<div class="pc-group"><div class="section-lbl">Bedrock lanes ' +
      '<span class="pc-count">' + (b.lanes ? b.lanes.length : 0) + '</span></div>' +
      (lanes || '<div class="pc-empty">' +
        (b.reason ? b.reason : 'No broker, so no lanes.') + '</div>') +
    '</div>' +

    '<div class="pc-group"><div class="section-lbl">Spend</div>' +
      engineStatRow('Micros', b.spent_micros != null ? b.spent_micros.toLocaleString() : '\u2014',
                    '1,000,000 \u00b5$ = $1.00') +
      engineStatRow('Dollars', b.spent_usd != null ? '$' + Number(b.spent_usd).toFixed(4) : '\u2014',
                    b.cap_usd != null ? 'cap $' + Number(b.cap_usd).toFixed(2) : '') +
      engineStatRow('Governor', b.state || '\u2014', '') +
    '</div>' +

    '</div>');

  el.hidden = false;
  const close = document.getElementById('placeCardClose');
  if (close) close.addEventListener('click', ev => { ev.stopPropagation(); hideEnginePanel(); });
}

/* ---- being out on the streets --------------------------------------------
 * Two kinds of person are on the pavement rather than in a building: someone
 * out for an evening walk, and a police officer on the beat. Both are a slow
 * circuit of real walkable tiles rather than a destination, so both are built
 * the same way - a stable loop of kerbside points, walked end to end and
 * round again.
 */
// The public places a walk can take in. Waypoints have to be DISTRICTS, not
// arbitrary tiles: walkRoute() can only follow the street network towards a
// named zone, and given a bare coordinate it falls back to a straight
// L-shaped corridor - which is exactly why strollers were seen cutting
// through the middle of buildings.
const STROLL_STOPS = ['townsquare', 'park', 'marketrow', 'docks', 'housing',
                      'government', 'library', 'housingSouth'];

function strollLoop(a, salt) {
  const stops = STROLL_STOPS.filter(z => ZONES[z]);
  if (stops.length < 2) return null;
  const pick = [];
  for (let i = 0; i < 4 && pick.length < stops.length; i++) {
    const z = stops[Math.floor(seeded01(a.id + salt + i, 'loop') * stops.length)];
    if (pick.indexOf(z) < 0) pick.push(z);
  }
  return pick.length >= 2 ? pick : stops.slice(0, 2);
}

/** Where someone out walking is right now: a slow loop of the streets. */
function strollPos(a, hourFrac, salt, minutesPerLeg) {
  const stops = strollLoop(a, salt || 'stroll');
  if (!stops) return zoneAnchor('townsquare', a);
  const legHours = (minutesPerLeg || 42) / 60;
  const t = (hourFrac / legHours) + seeded01(a.id + (salt || 'stroll'), 'phase') * stops.length;
  const i = Math.floor(t) % stops.length;
  const f = t - Math.floor(t);
  const fromZone = stops[i], toZone = stops[(i + 1) % stops.length];
  const from = taskPos(a, fromZone, Math.floor(hourFrac / legHours) * legHours, 'strollA');
  const to = taskPos(a, toZone, Math.floor(hourFrac / legHours) * legHours, 'strollB');
  // Named destination, so this follows the pavement like every other journey.
  return walkRoute(from, to, paceEase(Math.min(1, f / 0.9)), toZone);
}

/* ---- who is standing guard, and who is on the beat -----------------------
 * The town guard was a name in a panel. These are posts: two officers stood
 * at the government complex, one at the bank, and the rest of the shift out
 * walking the streets - by car once the night comes, which is when a patrol
 * is actually worth having.
 */

/** The post this officer holds today, or null if they are on the beat.
 *  Stable per officer per day so a shift is a shift, not a shuffle. */
function guardPostOf(a, day, hod) {
  if (a.occ !== 'police_officer' || !isAlive(a)) return null;
  // Posts are filled from the officers actually ON SHIFT at this hour. Dealing
  // them out across the whole force meant the desk could be assigned to
  // somebody asleep at home, and the station stood empty at three in the
  // morning - which is the one hour it most needs somebody in it.
  const all = (frameMemo.force && frameMemo.force.length)
    ? frameMemo.force
    : AGENTS.filter(x => x.occ === 'police_officer' && isAlive(x))
            .sort((x, y) => hash(x.id + 'post' + day) - hash(y.id + 'post' + day));
  const force = all.filter(x => {
    const h = healthOf(x);
    if (!h.alive || (h.ailment && WORK_CAPACITY_BY_AILMENT[h.ailment] <= 0.3)) return false;
    return x.shift ? onShift(x, hod) : true;
  });
  // Never post the whole shift. With three or four officers on at a time, four
  // posts left nobody at all out on the streets - the patrol the town was
  // supposed to have existed only when the shift happened to be large. The
  // desk comes first, then the government complex, then the bank, and one
  // officer is always held back for the beat once there are two to spare.
  const postable = Math.max(0, force.length - 1);
  let i = 0;
  for (const post of GUARD_POSTS) {
    if (post.hours && (hod < post.hours[0] || hod >= post.hours[1])) continue;
    for (let n = 0; n < post.officers; n++, i++) {
      if (i >= postable) return null;                 // the rest walk the beat
      if (force[i] && force[i].id === a.id) return post;
    }
  }
  return null;                       // on the beat
}

/* ---- children go where their guardian goes -------------------------------
 * Every child in the town was handed a day zone at genesis, and the youngest
 * were all handed the park - so four-month-old babies spent the working day
 * alone in the town park while their parents were at the farm. A child under
 * the ages in the rulebook has no independent day: an infant is carried, a
 * small child stays beside a guardian, and school takes over from there.
 */
function guardianOf(a) {
  // The named parents first - both of them, in order, so a child whose mother
  // has died is with their father rather than nobody.
  for (const name of (a.pn || [])) {
    const p = AGENTS.find(x => x.n === name);
    if (p && isAlive(p) && bracketAt(p, state.hourFrac) !== 'minor') return p;
  }
  // Then any adult in the same household - an older sibling, an aunt.
  const kin = AGENTS.find(x => x.hh && x.hh === a.hh && x.id !== a.id &&
                               isAlive(x) && bracketAt(x, state.hourFrac) !== 'minor');
  return kin || null;
}

/** How this child is looked after right now, given the rulebook's ages. */
function careOf(a, hourFrac) {
  if (bracketAt(a, hourFrac) !== 'minor') return null;
  const age = ageAt(a, hourFrac);
  const carried = rule('survival.childcare.carried_until_age_years', 3);
  const supervised = rule('survival.childcare.supervised_until_age_years', 6);
  const schoolFrom = rule('survival.childcare.school_from_age_years', 6);
  if (age >= supervised && age >= schoolFrom) return null;   // school has them
  return { age, carried: age < carried, guardian: guardianOf(a) };
}

/* ---- the shopping trip ---------------------------------------------------
 * Food was being bought in the abstract: once a game-day the model moved sim
 * out of a purse and units out of a larder, and nobody ever went anywhere. The
 * grocery had a sign on it, two staff standing inside, and no customers, which
 * is not a shop.
 *
 * Now every resident who eats makes the journey. They leave whatever they were
 * doing, walk the streets to the grocery or the bakery, stand at the counter,
 * and walk back. Which shop depends on what is actually on its shelves and
 * what it costs today, so a grain shortage visibly moves the queue to the
 * bakery. Someone who cannot afford to eat still goes - and comes home with
 * nothing, which is the part worth being able to watch.
 */
const SHOP_ZONES = ['grocery', 'bakery'];
const SHOP_TRIP_HOURS = 0.75;          // door to door, including the counter
const SHOP_WINDOW = [11.5, 19.0];      // when the shops are busy

/** Which shop this resident heads for today, and when they set off. Stable
 *  per resident per day, so a replay puts the same people in the same queue. */
function shopTrip(a, day) {
  const open = SHOP_WINDOW[0], close = SHOP_WINDOW[1];
  const at = open + seeded01(a.id + ':' + day, 'shophour') * (close - open - SHOP_TRIP_HOURS);
  // Prefer the shop that can actually serve them. Stock is the day's stock, so
  // when the grocery is cleaned out the bakery gets the crowd.
  // Which shop. Scoring purely on price sent everyone to the bakery (bread is
  // the cheapest thing in town); scoring purely on how much of a day's eating
  // a shop covers sent everyone to the grocery. Both are wrong, because a town
  // does not shop as one person. Households differ: some buy bread, the rest
  // buy flour, milk and fruit and cook. So each resident keeps a standing
  // habit, split in proportion to how much of the town's diet each shop
  // supplies - and abandons it only when their usual shop has nothing left,
  // which is what makes a bread shortage visibly move the queue.
  const spec = rule('market.goods', {});
  const pop = memoFor(day * 24).pop;
  const shareOf = (z) => (FOOD_SHOPS[z] || [])
    .reduce((t, g) => t + ((spec[g] || {}).demand_per_resident_day || 0), 0);
  const bakeryShare = shareOf('bakery') / (shareOf('bakery') + shareOf('grocery') || 1);
  const habit = seeded01(a.id, 'basket') < bakeryShare ? 'bakery' : 'grocery';
  const other = habit === 'bakery' ? 'grocery' : 'bakery';

  const memo = frameMemo.shelf;
  const daysOnShelf = (z) => {
    if (memo[z] !== undefined) return memo[z];
    const sells = FOOD_SHOPS[z] || [];
    let covered = 0, want = 0;
    for (const g of sells) {
      const need = pop * ((spec[g] || {}).demand_per_resident_day || 0);
      want += need;
      covered += Math.min(foodSim.stock[g] || 0, need);
    }
    return (memo[z] = want > 0 ? covered / want : 0);
  };
  const best = (daysOnShelf(habit) > 0.05 || !ZONES[other]) ? habit
             : (daysOnShelf(other) > 0.05 ? other : habit);
  return { zone: best || 'grocery', at, until: at + SHOP_TRIP_HOURS };
}

/** True while this resident is out doing the shopping. */
function isShopping(a, hod, day) {
  if (a.br === 'minor' && a.age < 12) return false;   // small children do not go alone
  const t = shopTrip(a, day);
  return hod >= t.at && hod < t.until;
}
function agentPosition(a, hourFrac, depth) {
  const dayStart = Math.floor(hourFrac / 24) * 24;
  const day = Math.floor(hourFrac / 24);
  const hod = hourFrac - dayStart;
  const phase = phaseAt(Math.floor(hod));
  const h = healthOf(a);
  // The dead stay where they were buried; nothing below applies to them.
  // `isAlive` rather than `h.alive` (UI-03): for a resident the world is
  // reporting on, the world decides, and a live position below is a fact
  // where this branch is a guess.
  if (!isAlive(a)) return { pos: zoneAnchor('housing', a, true), zone: 'housing', moving: false, dead: true };
  // A living resident the engine is actually reporting on is placed by the
  // engine, not by the schedule. Returns null for everyone else, and the
  // scripted day below runs exactly as it always has.
  const live = livePosition(a, hourFrac);
  if (live) return live;
  // Anyone too ill to work spends the day being treated: at the pharmacy while
  // that is all the town has, at the hospital once it opens.
  // Where someone too ill to work spends the day: the hospital once the town
  // has one, the pharmacy before that. Worked out here rather than inside the
  // day branch, because the morning commute has to walk them TO the ward - the
  // old code sent them towards their job and then snapped them to a hospital
  // bed on the stroke of eight.
  // Anyone too ill to work is ADMITTED, not sent home at six o'clock. A
  // medium or long illness keeps them on the ward until it passes - which is
  // what a hospital is for, and what the "patients" list on the hospital card
  // should mean. Short ailments are still treated and sent home.
  const ward = (h.ailment && WORK_CAPACITY_BY_AILMENT[h.ailment] <= 0.3)
    ? (hospitalOpen(day) && isBuilt('hospital', day) ? 'hospital' : 'pharmacy')
    : null;
  if (ward) {
    const admitted = h.ailment === 'medium' || h.ailment === 'long' || h.ailment === 'terminal';
    if (admitted || phase === 'day') {
      return { pos: taskPos(a, ward, hourFrac, 'ward'), zone: ward,
               moving: false, ill: true, admitted };
    }
  }
  // A child too young to be out alone. Resolved before every other branch,
  // because none of them apply: an infant has no commute, no shift and no
  // errand of its own. `depth` stops a cycle if a household is ever malformed
  // enough that two people are each other's guardian.
  const care = careOf(a, hourFrac);
  if (care) {
    const g = care.guardian;
    if (!g) {
      // No living guardian. An orphan stays at their own home rather than
      // being turned out into the street.
      return { pos: zoneAnchor('housing', a, true), zone: 'housing',
               moving: false, withGuardian: null };
    }
    if ((depth || 0) < 2) {
      const gp = agentPosition(g, hourFrac, (depth || 0) + 1);
      // Carried infants share the guardian's exact spot; a small child walks
      // beside them, a stable half-tile off so the two are distinguishable.
      const off = care.carried ? 0 : 0.45;
      const ang = seeded01(a.id, 'beside') * 6.2832;
      return { pos: { tx: gp.pos.tx + Math.cos(ang) * off,
                      ty: gp.pos.ty + Math.sin(ang) * off },
               zone: gp.zone, moving: gp.moving,
               withGuardian: g.id, carried: care.carried };
    }
  }

  // The shopping trip. Ahead of the shift and day branches because it is an
  // errand that interrupts them - a real one, walked on real streets. Skipped
  // for anyone laid up in a ward and for anyone mid-shift, who sends someone
  // else or goes after.
  if (!ward && !(a.shift && onShift(a, hod)) && isShopping(a, hod, day)) {
    const t = shopTrip(a, day);
    const shop = t.zone;
    const f = (hod - t.at) / SHOP_TRIP_HOURS;
    const from = phase === 'night' ? zoneAnchor('housing', a, true)
                                   : taskPos(a, a.dayZone, hourFrac, 'shopfrom');
    const counter = taskPos(a, shop, hourFrac, 'shop');
    if (f < 0.35) {
      return { pos: walkRoute(from, counter, paceEase(f / 0.35), shop),
               zone: f < 0.2 ? (phase === 'night' ? 'housing' : a.dayZone) : shop,
               moving: true, shopping: true, shop };
    }
    if (f < 0.65) {
      return { pos: counter, zone: shop, moving: false, shopping: true, shop };
    }
    return { pos: walkRoute(counter, from, paceEase((f - 0.65) / 0.35), a.dayZone),
             zone: f > 0.85 ? (phase === 'night' ? 'housing' : a.dayZone) : shop,
             moving: true, shopping: true, shop };
  }

  // Shift workers ignore the town's ordinary day entirely: they are at work
  // when their shift says so, asleep in their own bed when it says so, and off
  // duty the rest of the time.
  if (a.shift) {
    // Shift staff had no commute at all: at 06:59 they were at home and at
    // 07:00 they were at a hospital bed. They now walk in over the hour before
    // the shift and out over the hour after it, which is time they were
    // already off duty - so the 24/7 rota is untouched.
    const win = SHIFTS[a.shift].on;
    const since = (from, h) => { let d = h - from; while (d < 0) d += 24; return d; };
    if (!onShift(a, hod)) {
      const inF = since(win[0] - SHIFT_TRAVEL, hod) / SHIFT_TRAVEL;
      if (inF >= 0 && inF < 1) {
        const rest = shiftRestPos(a, dayStart + win[0] - SHIFT_TRAVEL, wrap24(win[0] - SHIFT_TRAVEL));
        const p2s = taskPos(a, a.dayZone, dayStart + win[0], 'shift');
        return { pos: walkRoute(rest.pos, p2s, paceEase(inF), a.dayZone),
                 zone: inF < 0.5 ? rest.zone : a.dayZone, moving: true, offDuty: true };
      }
      const outF = since(win[1], hod) / SHIFT_TRAVEL;
      if (outF >= 0 && outF < 1) {
        const p1s = taskPos(a, a.dayZone, dayStart + win[1], 'shift');
        const rest = shiftRestPos(a, dayStart + win[1] + SHIFT_TRAVEL, wrap24(win[1] + SHIFT_TRAVEL));
        const dz = rest.zone === 'housing' ? (a._eastSide ? 'housingEast' : 'housing') : rest.zone;
        return { pos: walkRoute(p1s, rest.pos, paceEase(outF), dz),
                 zone: outF < 0.5 ? a.dayZone : rest.zone, moving: true, offDuty: true };
      }
    }
    if (onShift(a, hod)) {
      const t = taskPos(a, a.dayZone, hourFrac, 'shift');
      // An officer on shift is either at a post or out on the beat. Standing
      // all of them inside the police station is not policing.
      if (a.occ === 'police_officer') {
        const post = guardPostOf(a, day, hod);
        if (post && ZONES[post.zone]) {
          return { pos: taskPos(a, post.zone, hourFrac, 'post'), zone: post.zone,
                   moving: false, onShift: true, guardPost: post.zone };
        }
        const night = hod >= 20 || hod < 6;
        return { pos: strollPos(a, hourFrac, 'patrol', night ? 16 : 34),
                 zone: 'streets', moving: true, onShift: true,
                 patrolling: true, byCar: night };
      }
      return { pos: t, zone: a.dayZone, moving: t.walking, onShift: true };
    }
    const rest = shiftRestPos(a, hourFrac, hod);
    return { pos: rest.pos, zone: rest.zone, moving: !!rest.moving, offDuty: true };
  }

  if (phase === 'night') {
    // Not everyone is asleep at once. A few residents are out late - a walk
    // to clear the head, a look at the river. Deterministic per person per
    // night, and only ever a small minority, so the town still reads as
    // asleep rather than as a second evening.
    const nightWalker = seeded01(a.id + ':' + day, 'nightwalk') < 0.06 &&
                        bracketAt(a, hourFrac) === 'working' &&
                        !careOf(a, hourFrac) && hod >= 22.2 && hod < 24.6;
    if (nightWalker) {
      return { pos: strollPos(a, hourFrac, 'nightwalk', 50), zone: 'streets',
               moving: true, strolling: true, atNight: true };
    }
    return { pos: zoneAnchor('housing', a, true), zone: 'housing', moving: false };
  }
  // A working day is not nine hours of standing still. Each resident walks a
  // small circuit of task points inside their workplace, so the town reads as
  // people doing jobs rather than as pins dropped on buildings.
  if (phase === 'day') {
    const t = taskPos(a, a.dayZone, hourFrac, 'work');
    return { pos: t, zone: a.dayZone, moving: t.walking };
  }
  // Out for a walk: no destination, a slow circuit of the streets. Handled
  // before the ordinary evening branch because `streets` is not a zone and
  // taskPos would have nothing to place them in.
  if (phase === 'evening' && a.eveningZone === 'streets') {
    const t = strollPos(a, hourFrac);
    return { pos: t, zone: 'streets', moving: true, strolling: true };
  }
  if (phase === 'evening') {
    const t = taskPos(a, a.eveningZone, hourFrac, 'eve');
    return { pos: t, zone: a.eveningZone, moving: t.walking };
  }
  // A commute has to start exactly where the previous phase left the resident
  // and end exactly where the next one picks them up. Anchoring both ends on
  // the district centre instead meant everyone popped several tiles sideways
  // on the stroke of 08:00 and again at 18:00.
  let from, to, base, len, fromSalt, toSalt;
  if (phase === 'commute1') {
    from = 'housing'; to = ward || a.dayZone; base = 6; len = 2;
    fromSalt = null; toSalt = ward ? 'ward' : 'work';
  } else if (phase === 'commute2') {
    from = ward || a.dayZone; to = a.eveningZone; base = 17; len = 1;
    fromSalt = ward ? 'ward' : 'work'; toSalt = 'eve';
  } else {
    from = a.eveningZone; to = 'housing'; base = 20; len = 2; fromSalt = 'eve'; toSalt = null;
  }
  const p1 = fromSalt ? taskPos(a, from, dayStart + base, fromSalt)
                      : zoneAnchor(from, a, true);
  const p2 = toSalt ? taskPos(a, to, dayStart + base + len, toSalt)
                    : zoneAnchor(to, a, true);
  const tt = Math.min(1, Math.max(0, (hod - base) / len));
  const dest = to === 'housing' ? (a._eastSide ? 'housingEast' : 'housing') : to;
  const pos = walkRoute(p1, p2, paceEase(tt), dest);
  return { pos, zone: tt < 0.5 ? from : to, moving: true };
}
function activityFor(a, zone, hod) {
  // An engine-bound resident's activity comes from the WORLD, never from
  // this file's own model.
  //
  // The model below is a full invented life - shifts, guard rosters, ward
  // admissions, a hunger economy - written to make an unbound town readable.
  // Run it over a real agent and it contradicts them to their face: Nell
  // Holloway, artisan, 71, was shown "Resting after the evening shift" while
  // the world had her walking to Kasukabe Forest, and the card above it said
  // she staffed the police station.
  //
  // Nothing here is deleted: with no world bound it is still the only thing
  // that makes the town move. It simply does not get to speak over a
  // resident who is really running.
  if (isBound(a)) {
    const now = LIVE.doing && LIVE.doing[a.id];
    if (now && now.line) return now.line;
    if (LIVE.action && LIVE.action[a.id]) return LIVE.action[a.id];
    return 'Thinking';
  }

  const lp = liveAgentPos[a.id];
  if (lp) {
    if (lp.guardPost) {
      const post = GUARD_POSTS.find(p => p.zone === lp.guardPost);
      if (post) return post.what;
    }
    // What they are doing, not what they are doing it in. The night patrol
    // covers more ground, which is why it is a separate state at all - but
    // the officer is still out on the streets either way.
    if (lp.patrolling) return lp.byCar ? 'Patrolling the streets — night watch'
                                       : 'Patrolling the streets';
    if (lp.strolling) {
      return lp.atNight ? 'Out walking late — clearing their head'
                        : 'Out for a walk through the streets';
    }
    if (lp.admitted) return 'Admitted to the ward';
  }
  // A small child's day is their guardian's day, and saying so is the point of
  // the whole rule - "in the park" told you nothing about who was watching.
  const cp = liveAgentPos[a.id];
  if (cp && cp.withGuardian) {
    const g = AGENT_BY_ID[cp.withGuardian];
    const who = g ? g.n : 'a guardian';
    return (cp.carried ? 'Carried by ' : 'With ') + who;
  }
  if (cp && cp.withGuardian === null && careOf(a, state.hourFrac) &&
      !careOf(a, state.hourFrac).guardian) {
    return 'At home — no guardian living';
  }
  // Out doing the shopping beats whatever the district would otherwise say.
  const sp = liveAgentPos[a.id];
  if (sp && sp.shopping) {
    const where = sp.shop === 'bakery' ? 'the bakery' : 'the grocery';
    if (!sp.moving) {
      if (isHungry(a)) {
        const empty = (FOOD_SHOPS[sp.shop] || []).every(g => (foodSim.stock[g] || 0) <= 0);
        return empty ? 'At ' + where + ' — sold out' : 'At ' + where + ' — cannot afford it';
      }
      return 'Buying food at ' + where;
    }
    return 'Walking to ' + where;
  }
  if (a.shift) {
    if (onShift(a, hod)) return (VERB_AT_WORK[a.occ] || 'On duty') + ' · ' + a.shift + ' shift';
    if (shiftAsleep(a, hod)) return 'Resting after the ' + a.shift + ' shift';
    // When the district has no activity of its own the fallback IS "off duty",
    // and appending the suffix to it produced "Off duty · off duty".
    const doing = ZONE_ACTIVITY[zone];
    return doing ? doing + ' · off duty' : 'Off duty';
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
/* ===========================================================================
   FOOD: where it comes from, who can afford it, and what happens if they can't
   ===========================================================================
   Until now the town had a grocery, a bakery and nineteen farmers, and none of
   it meant anything: nobody ate. This is the chain that makes farming the
   reason the town is alive.

       farmland / orchard / pasture        who actually worked there today
                 |                          x units_per_worker_day
                 v
            the town's stock                grain, produce, milk
                 |                          bakery turns grain into bread
                 v
          the grocery and the bakery        price set by supply vs demand
                 |
                 v
             every resident                 buys food_units per day
                 |
        can they afford it?  -- no --> they go hungry
                 |                          hunger_days_to_illness -> ill
                yes                         and illness can kill
                 v
              they live

   Every number comes from the rulebook (`world/rules/village_rules.json`,
   bundled as rules.js) - production rates, demand per resident, prices, the
   elasticity that moves the price, and how many hungry days it takes to make
   someone ill. Change the JSON and the town's food economy changes.

   Determinism matters here as much as anywhere else: money in this viewer is a
   pure function of the day so the clock can be scrubbed backwards. Food is
   replayed the same way - `advanceFoodTo(day)` walks forward from day 0 and
   resets on rewind, exactly like `advanceHealthTo`. Affordability is checked
   against income earned MINUS food already bought on EARLIER days, never the
   current one, so there is no circular definition of a resident's purse.
   ======================================================================== */

// The rulebook. The bundle is the floor; a served page replaces it with
// whatever /api/rules returns, so a rule edited on a running server takes
// effect on reload.
let RULES = (typeof VILLAGE_RULES !== 'undefined') ? VILLAGE_RULES : null;
function rule(path, fallback) {
  let node = RULES;
  for (const part of path.split('.')) {
    if (!node || typeof node !== 'object' || !(part in node)) return fallback;
    node = node[part];
  }
  return node === undefined ? fallback : node;
}
(function preferServedRules() {
  if (location.protocol === 'file:') return;      // no server to ask
  fetch('api/rules', { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(d => {
      if (!d || !d.ok || !d.rules) return;
      RULES = d.rules;
      foodSim.day = -1;                            // replay under the new rules
      renderFood();
    })
    .catch(() => { /* the bundled copy stands */ });
})();

// Which district each good is grown or made in, so "who worked there today"
// is answered from the same worksite assignment the map draws.
const FOOD_SITE = {
  grain: 'farmland', produce: 'orchard', milk: 'pasture',
  timber: 'forest', ore: 'oreoutcrop', bread: 'bakery',
  goods: 'tradeoffices', medicine: 'pharmacy',
};

const foodSim = {
  day: -1,
  stock: {},        // good -> units in the town's larder
  price: {},        // good -> sim per unit today
  produced: {},     // good -> units made today
  eaten: 0,         // units eaten today
  spend: {},        // agentId -> cumulative sim spent on food
  hunger: {},       // agentId -> consecutive days that ended hungry
  hungryToday: {},  // agentId -> true
  log: [],
  history: [],      // one row per day, for the panel
};

function resetFood() {
  foodSim.day = -1;
  foodSim.stock = {}; foodSim.price = {}; foodSim.produced = {};
  foodSim.spend = {}; foodSim.hunger = {}; foodSim.hungryToday = {};
  foodSim.eaten = 0; foodSim.log = []; foodSim.history = [];
  const goods = rule('market.goods', {});
  for (const g of Object.keys(goods)) {
    if (g.startsWith('_')) continue;
    // The town starts with a few days' buffer, not an empty larder: a famine
    // on day 1 would be the model's doing, not the town's.
    // Two days of what the town eats, so day 1 is the town's own doing and not
    // the model starting it with an empty larder.
    const buffer = (goods[g].demand_per_resident_day || 0) * AGENTS.length * 2;
    foodSim.stock[g] = buffer;
    foodSim.price[g] = goods[g].base_price_sim;
  }
}

/** Everything a resident is allowed to eat, and what one unit of it costs. */
function edibleGoods() {
  const goods = rule('market.goods', {});
  return Object.keys(goods).filter(g => !g.startsWith('_') && goods[g].essential &&
                                        g !== 'medicine');
}

/** Price for a good given how much of it there is against how much is wanted.
 *  The same curve the Python loader applies, so the browser and the world
 *  server price a shortage identically. */
function priceFor(good, supply, demand) {
  const g = rule('market.goods.' + good, null);
  if (!g) return 0;
  const pr = rule('market.price_response', {});
  const lo = pr.min_price_multiplier ?? 0.4, hi = pr.max_price_multiplier ?? 4;
  const e = pr.elasticity ?? 0.6;
  let mult;
  if (supply <= 0) mult = hi;
  else if (demand <= 0) mult = lo;
  else mult = Math.max(lo, Math.min(hi, Math.pow(demand / supply, e)));
  return g.base_price_sim * mult;
}

function shortageLevel(supply, demand) {
  if (demand <= 0) return 'ok';
  const ratio = supply / demand;
  const sh = rule('market.shortage', {});
  if (ratio < (sh.deficit_ratio_alarm ?? 0.7)) return 'shortage';
  if (ratio < (sh.deficit_ratio_watch ?? 0.9)) return 'watch';
  return 'ok';
}

/** Income earned by the end of `day`, before any food was bought. */
function incomeThrough(a, day) {
  const net = a.wage - a.upkeep - (a.br === 'minor' ? 0 : HEAD_TAX_PER_DAY);
  // Drift is the odd job, the small windfall, the unbudgeted spend - things
  // only a working person does. Applied to everyone it quietly paid a
  // four-day-old baby 22 sim while its own card read "Net per day +0".
  const days = Math.max(0, day - (a._bornAtHour || 0) / 24);
  const drift = a.wage > 0 ? (seeded01(a.id, 'drift') - 0.45) * 12 * days : 0;
  return a.bal + net * days + drift;
}

function stepFoodDay(d) {
  // UI-04. A bound town's stock, prices and hunger are the world's - it
  // serves them at `/world/vitals`, which `vitals.js` renders two panels
  // away from these signs. This model ran anyway, so a shop advertised
  // SOLD OUT beside the world's real holdings, and residents the world had
  // alive and working were logged dead of starvation in the health book.
  if (worldIsBound()) return;
  const goods = rule('market.goods', {});
  const living = AGENTS.filter(isAlive);
  const pop = living.length || 1;

  // 1. PRODUCTION - counted off who actually works each site, weighted by how
  //    fit they are to work (an ill farmer brings in less).
  const workers = {};
  for (const a of living) {
    const site = a.dayZone;
    if (!site) continue;
    workers[site] = (workers[site] || 0) + workCapacity(a);
  }
  foodSim.produced = {};
  for (const g of Object.keys(goods)) {
    if (g.startsWith('_')) continue;
    const spec = goods[g];
    const hands = workers[FOOD_SITE[g]] || 0;
    let made = hands * (spec.units_per_worker_day || 0);
    // A good made FROM another good can only be made if the input is there.
    if (spec.consumes) {
      for (const [input, per] of Object.entries(spec.consumes)) {
        const have = foodSim.stock[input] || 0;
        made = Math.min(made, per > 0 ? have / per : made);
      }
      for (const [input, per] of Object.entries(spec.consumes)) {
        foodSim.stock[input] = Math.max(0, (foodSim.stock[input] || 0) - made * per);
      }
    }
    foodSim.produced[g] = made;
    foodSim.stock[g] = (foodSim.stock[g] || 0) + made;
  }

  // 1b. SPOILAGE AND THE LARDER - food does not keep. Without this the town
  //     banked every surplus day forever and a shortage became impossible,
  //     which makes farming decorative. The granary, once built, is what turns
  //     a few days of buffer into a real reserve.
  const store = rule('market.storage', {});
  const spoil = store.spoilage_per_day ?? 0.12;
  const hasGranary = (typeof initiativesOn === 'function')
    ? initiativesOn(d).some(i => i.id === 'granary' && i.funded) : false;
  const bufferDays = hasGranary ? (store.granary_buffer_days ?? 20)
                                : (store.buffer_days ?? 4);
  for (const g of Object.keys(goods)) {
    if (g.startsWith('_')) continue;
    const cap = pop * (goods[g].demand_per_resident_day || 0) * bufferDays;
    foodSim.stock[g] = Math.min((foodSim.stock[g] || 0) * (1 - spoil), cap);
  }

  // 2. PRICE - set against the day's demand before anyone buys.
  const perHead = rule('survival.daily_needs.food_units', 0.5);
  const demandTotal = pop * perHead;
  const edible = edibleGoods();
  const supplyTotal = edible.reduce((t, g) => t + (foodSim.stock[g] || 0), 0);
  for (const g of Object.keys(goods)) {
    if (g.startsWith('_')) continue;
    const want = pop * (goods[g].demand_per_resident_day || 0);
    foodSim.price[g] = priceFor(g, foodSim.stock[g] || 0, want);
  }
  // What a day's food costs: the cheapest edible stock first, so a resident
  // eats bread when bread is cheap and grain when it is not.
  const menu = edible.slice().sort((x, y) => foodSim.price[x] - foodSim.price[y]);

  // 3. THE TABLE - everyone tries to buy a day's food. Children eat too; their
  //    household pays, which is why a household with no earner starves as one.
  let eaten = 0, fed = 0, hungryBroke = 0, hungryEmpty = 0;
  foodSim.hungryToday = {};
  // Who gets to the food first: whoever can pay for it. Shuffling the queue at
  // random spread one day's shortfall across a different 23 people every day,
  // so nobody was ever hungry twice running and hunger never became illness -
  // the shortage was real and hurt no one. A shortage falls on the people who
  // cannot outbid it, which is the whole reason the poorest are the ones the
  // survival question is about. Ties break on a stable hash so a replay of the
  // same day always feeds the same people.
  const purseOf = a => incomeThrough(a, d) - (foodSim.spend[a.id] || 0);
  const order = living.slice().sort((x, y) =>
    purseOf(y) - purseOf(x) || (hash(x.id + 'meal') - hash(y.id + 'meal')));
  for (const a of order) {
    let need = perHead, cost = 0;
    for (const g of menu) {
      if (need <= 0) break;
      const take = Math.min(need, foodSim.stock[g] || 0);
      if (take <= 0) continue;
      cost += take * foodSim.price[g];
      foodSim.stock[g] -= take;
      need -= take;
    }
    const got = perHead - need;
    const purse = purseOf(a);
    if (got <= 0) {
      hungryEmpty++;                                   // nothing left to buy
    } else if (purse < cost && billPayerFor(a).id === a.id) {
      // Cannot pay, and nobody is paying for them. Put the food back.
      let give = got;
      for (const g of menu) { if (give <= 0) break;
        const back = Math.min(give, got); foodSim.stock[g] += back; give -= back; }
      hungryBroke++;
    } else {
      foodSim.spend[a.id] = (foodSim.spend[a.id] || 0) + cost;
      eaten += got; fed++;
      foodSim.hunger[a.id] = 0;
      continue;
    }
    foodSim.hunger[a.id] = (foodSim.hunger[a.id] || 0) + 1;
    foodSim.hungryToday[a.id] = true;
  }
  foodSim.eaten = eaten;

  // 4. HUNGER MAKES PEOPLE ILL, AND THEN IT KILLS THEM. Both thresholds come
  //    from the rulebook. Without the second one a resident could go three
  //    weeks without food and merely be unwell, which makes "survive" a word
  //    with nothing behind it.
  const toIll = rule('survival.daily_needs.hunger_days_to_illness', 3);
  const toDeath = rule('survival.daily_needs.starvation_days_to_death', 12);
  for (const a of living) {
    const days = foodSim.hunger[a.id] || 0;
    const hh = healthSim.states[a.id];
    if (days >= toDeath && hh && hh.alive) {
      hh.alive = false; hh.status = 'deceased'; hh.ailment = null;
      hh.deathDay = d; hh.cause = 'starvation';
      healthSim.log.unshift({ day: d, id: a.id, name: a.n, kind: 'death', cause: 'starvation' });
      foodSim.log.unshift({ day: d, name: a.n, id: a.id,
        what: 'starved after ' + days + ' days without food' });
      continue;
    }
    if (days < toIll) continue;
    const h = healthSim.states[a.id];
    if (!h || !h.alive || h.ailment) continue;
    h.ailment = 'medium'; h.status = 'medium'; h.since = d;
    h.until = d + AILMENT_DURATION.medium[1];
    h.cause = 'hunger';
    healthSim.log.unshift({ day: d, id: a.id, name: a.n, kind: 'illness', cause: 'hunger' });
    foodSim.log.unshift({ day: d, name: a.n, id: a.id,
      what: 'fell ill after ' + foodSim.hunger[a.id] + ' days without food' });
  }

  const level = shortageLevel(supplyTotal, demandTotal);
  if (level !== 'ok' && (!foodSim.history.length || foodSim.history[0].level !== level)) {
    foodSim.log.unshift({ day: d, name: null,
      what: level === 'shortage'
        ? 'FOOD SHORTAGE — the town cannot feed itself at today’s output'
        : 'food is running short — supply is below what the town eats' });
  }
  foodSim.history.unshift({
    day: d, level, fed, hungryBroke, hungryEmpty, eaten,
    supply: supplyTotal, demand: demandTotal,
    price: edible.reduce((t, g) => t + foodSim.price[g], 0) / (edible.length || 1),
    produced: { ...foodSim.produced },
  });
  foodSim.history = foodSim.history.slice(0, 120);
  foodSim.log = foodSim.log.slice(0, 60);
}

function advanceFoodTo(day) {
  if (day < foodSim.day || foodSim.day < 0) resetFood();
  for (let d = foodSim.day + 1; d <= day; d++) stepFoodDay(d);
  foodSim.day = Math.max(foodSim.day, day);
}

/** Sim spent on food by the end of `day`. The term `balanceOn` subtracts. */
function foodSpentThrough(a, day) {
  if (foodSim.day < day) return 0;      // not replayed this far yet
  return foodSim.spend[a.id] || 0;
}
function hungerDaysOf(a) { return foodSim.hunger[a.id] || 0; }
function isHungry(a) { return !!foodSim.hungryToday[a.id]; }

function balanceOn(a, day) {
  // Income, less what this resident actually paid to eat. Food is the one
  // outgoing that can empty a purse faster than upkeep, and the only one that
  // stops when the town runs out of it.
  return Math.max(0, Math.round(incomeThrough(a, day) - foodSpentThrough(a, day)));
}
function balanceNow(a, hourFrac) {
  // UI-04. A bound resident's balance is the world's `sim_balance`.
  //
  // Everything that shows money goes through here - the Wealthiest
  // leaderboard, the household ranking, the roster's sort-by-wealth, the
  // Money tab - so on a live run all of it was a property of this file's
  // WAGE constants and none of it was a property of the run. The number was
  // on the same poll as the position the whole time (world/agents_api.py's
  // AgentRow.sim_balance) and this layer dropped it on the floor.
  //
  // `!== undefined`, not a truth test: a resident the world says is broke
  // holds 0, and `0 || model` would print the model's guess for exactly the
  // resident whose poverty is the interesting fact.
  const held = (LIVE.balance || {})[a.id];
  if (isBound(a) && held !== undefined) return held;

  const d = Math.floor(hourFrac / 24);
  const f = (hourFrac - d * 24) / 24;
  return Math.round(balanceOn(a, d) + (balanceOn(a, d + 1) - balanceOn(a, d)) * f);
}
function costSpent(a, hourFrac) {
  // UI-04: the world's cents when it has them. This models a decay curve
  // against a $2 starting wallet; a bound agent has really spent a really
  // metered amount, and the two disagreed on the same card.
  const spentCents = (LIVE.usdCents || {})[a.id];
  if (isBound(a) && spentCents !== undefined) {
    return Math.max(0, (a.usd - spentCents) / 100);
  }
  // Hours THIS resident has been alive, not hours the town has existed. A
  // baby born on day 4 was being billed for four days of thinking it had not
  // done - the card showed a newborn $1.38 into its own $2 wallet.
  const lived = Math.max(0, hourFrac - (a._bornAtHour || 0));
  return (a.usd / 100) * (1 - Math.exp(-lived / a._costDecay));
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
  // UI-04. A bound town does its own births.
  //
  // This invented one, pushed `b0001`, `b0002`... straight into AGENTS and
  // gave each a People card, so a live run's population grew by residents
  // the world had never heard of - and `POP`, the census bars and every
  // per-capita figure moved with them. `world/life/` owns birth for real
  // (and UI-05 wired the blessing queue that requests one), so the honest
  // answer here is to stop.
  //
  // The model is left intact rather than deleted: with no world bound it
  // is still the only thing that makes the town grow.
  if (worldIsBound()) return;

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
// ---- what the government does with a surplus ------------------------------
// A treasury that only accumulates is a savings account, not a government.
// Once the town can afford it, the sitting government spends on the place:
// each initiative has a price, a day it becomes affordable, and a visible
// effect. Funded strictly in order and only out of money actually on hand
// (fiscal() never lets the balance go negative, per DESIGN.md's ledger
// rule), so the town develops at the pace it can pay for - which is the
// watchable part.
const INITIATIVES = [
  { id: 'guard',    name: 'Town Guard',            icon: '🛡️', cost: 4_000,
    note: 'A standing guard on the government complex and the treasury. Officers rotate through it alongside the police station.' },
  { id: 'lamps',    name: 'Street Lighting',       icon: '💡', cost: 6_000,
    note: 'Lamps down every avenue and street, lit from dusk. Night work and night travel stop being a risk the town simply accepts.' },
  { id: 'wells',    name: 'Public Wells',          icon: '⛲', cost: 9_000,
    note: 'Clean water at the square and the market row, cutting the short-ailment incidence the pharmacy spends its days on.' },
  { id: 'clinic',   name: 'Second Ward',           icon: '🏥', cost: 16_000,
    note: 'A second ward at the hospital, so a bad week of illness no longer outruns the beds.' },
  { id: 'roads',    name: 'Paved Approaches',      icon: '🛣️', cost: 24_000,
    note: 'The approaches to the docks and the ore outcrop paved, so a cart in the wet is no longer a lost day.' },
  { id: 'school2',  name: 'Apprentice Hall',       icon: '🎓', cost: 34_000,
    note: 'Somewhere for the town to teach its own trades, so a shortage of farmers is answered by training farmers.' },
  { id: 'granary',  name: 'Town Granary',          icon: '🌾', cost: 48_000,
    note: 'A stored surplus, so one bad harvest is a hard year rather than a hungry one.' },
];

/** Which initiatives the treasury has been able to fund by this day.
 *
 * Pure and cumulative: an initiative is funded on the first day the running
 * balance covers it AND everything before it, so scrubbing the clock
 * backwards un-funds them exactly the way it un-spends a wallet.
 */
function initiativesOn(day) {
  const f = fiscal();
  const out = [];
  let spent = 0;
  for (const item of INITIATIVES) {
    let fundedOn = null;
    for (let d = 0; d <= Math.min(day, FISCAL_HORIZON_DAYS); d++) {
      if (f.balance[d] - spent >= item.cost) { fundedOn = d; break; }
    }
    if (fundedOn === null) { out.push({ ...item, funded: false, day: null }); continue; }
    spent += item.cost;
    out.push({ ...item, funded: true, day: fundedOn });
  }
  return out;
}

/** The officers standing guard rather than on the beat, once funded. */
function guardDetail(day) {
  const funded = initiativesOn(day).find(i => i.id === 'guard' && i.funded);
  if (!funded) return [];
  // The two longest-serving officers take the government post; the rest of
  // the rota is untouched, so the police station keeps its 24/7 cover.
  return AGENTS.filter(a => a.occ === 'police_officer' && isAlive(a))
               .sort((x, y) => y.age - x.age)
               .slice(0, 2);
}

const BUILD_UPKEEP_SIM = {
  hospital: 900, police: 620, fire: 500, school: 560, library: 240,
  bakery: 120, restaurant: 160, bank: 200, inn: 140,
};
const CAPITAL_VALUE_SIM = {
  bakery: 18000, restaurant: 22000, school: 60000, hospital: 140000,
  police: 90000, library: 45000, fire: 75000, bank: 50000, inn: 30000,
};

/** The seat a resident holds, or null. Built once off seatHolders() so the
 *  map, the place card and the search box all name the Prime Minister and the
 *  four ministers the same way the Government panel does - before this, the
 *  five of them were indistinguishable from the other clerks everywhere except
 *  that one panel. Rebuilt when the cast changes (a seat holder can die). */
let _seatBook = null, _seatBookFor = -1, _seatBookGov = undefined;
function seatBook() {
  // UI-04: keyed on the government too, not the cast size alone. An
  // election replaces a minister without changing the population by one, so
  // a size-only key served the previous cabinet for the rest of the run.
  const gov = LIVE.government;
  if (_seatBook && _seatBookFor === AGENTS.length && _seatBookGov === gov) return _seatBook;
  const book = Object.create(null);
  seatHolders().forEach(s => { book[s.agent.id] = s.role; });
  _seatBookFor = AGENTS.length;
  _seatBookGov = gov;
  return (_seatBook = book);
}
/** 'Prime Minister' | 'Minister' | null */
function seatRoleOf(a) { return seatBook()[a.id] || null; }
/** What to call this resident: their seat if they hold one, else their trade. */
function titleOf(a) { return seatRoleOf(a) || OCC_LABEL(a.occ); }

/** Who sits in government. The charter seats 1 PM + 4 ministers
 * (world/charter/models.py: pm_seats=1, minister_seats=4). Genesis records no
 * election, so the seats are filled deterministically from the clerks - the
 * residents who actually work in the government complex. */
function seatHolders() {
  // UI-04. When a world is bound, the seats are the world's.
  //
  // `/governance/government` has served prime_minister and ministers off
  // the charter store since it was written, and this function never asked -
  // it sorted a hash of the agent ids and declared the first of five the
  // Prime Minister. That name then went everywhere: the Government panel,
  // the place card, the roster's "Prime Minister" filter, the search box,
  // and a gold sash on the map. On a live run the town had a head of
  // government it had never elected, and if it HAD elected one, that person
  // was drawn as an ordinary clerk.
  //
  // An empty list is the honest answer to a bound town with no election
  // yet. Every caller already handles it - a town of 20 has no ministers
  // before it votes, which is a fact about the run and not a gap to fill.
  if (worldIsBound()) {
    const gov = LIVE.government;
    if (!gov) return [];
    const seat = (ref, role, salary) => {
      if (!ref) return null;
      const id = ref.agent_id || ref.id;
      const agent = AGENT_BY_ID[id];
      if (!agent) return null;   // a seat held by someone outside this roster
      return { agent: agent, role: role, salary: salary };
    };
    const out = [];
    const pm = seat(gov.prime_minister, 'Prime Minister', PM_SALARY_SIM);
    if (pm) out.push(pm);
    for (const m of (gov.ministers || [])) {
      const s = seat(m, 'Minister', MINISTER_SALARY_SIM);
      if (s) out.push(s);
    }
    return out;
  }

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

// ---- the map ------------------------------------------------------------
// v2 renders nothing itself: village2.js owns the canvas, camera, draw loop
// and depth sorting. This file decides WHERE each of the 314 residents is and
// what they are doing, and hands that over each frame via Village.setAgents().
const V = window.Village;
const camera = { get zoom() { return V.zoom; } };
let liveAgentPos = {};
let zoneCounts = {};
let selectedId = null;
let cardEls = null;

/** Screen position of a tile - used to pin the resident card to a person. */
function worldToScreen(tx, ty) {
  const p = V.screenOf(tx, ty);
  return { sx: p.x, sy: p.y };
}

/** Is this position inside any building footprint? */
function indoors(p) {
  for (const b of plans) {
    const bd = b.d !== undefined ? b.d : b.h;   // v2 footprints are w x d
    if (p.tx >= b.x && p.tx < b.x + b.w && p.ty >= b.y && p.ty < b.y + bd) return true;
  }
  return false;
}

// Answers that are identical for every agent within one frame. Recomputing
// them per agent made the position pass O(n^2): shopTrip() alone ran
// AGENTS.filter(isAlive) three hundred times a frame, and guardPostOf() sorted
// the whole police force once per officer. That cost the frame rate roughly
// half, and none of it changes between one resident and the next.
const frameMemo = { key: null, pop: 0, force: null, shelf: {} };
function memoFor(hourFrac) {
  const key = Math.floor(hourFrac * 60);          // one memo per game-minute
  if (frameMemo.key === key) return frameMemo;
  frameMemo.key = key;
  frameMemo.pop = AGENTS.filter(isAlive).length || 1;
  frameMemo.force = AGENTS.filter(x => x.occ === 'police_officer' && isAlive(x))
    .sort((x, y) => hash(x.id + 'post' + Math.floor(hourFrac / 24)) -
                    hash(y.id + 'post' + Math.floor(hourFrac / 24)));
  frameMemo.shelf = {};
  return frameMemo;
}

function updatePositions(hourFrac) {
  memoFor(hourFrac);
  const counts = {};
  for (const a of AGENTS) {
    const { pos, zone, moving, dead, ill, onShift: shiftNow, offDuty,
            shopping, shop, withGuardian, carried,
            admitted, patrolling, byCar, guardPost, strolling, atNight } = agentPosition(a, hourFrac);
    const atWork = !dead && !ill && zone === a.dayZone &&
                   (a.shift ? onShift(a, hourFrac % 24)
                            : phaseAt(Math.floor(hourFrac % 24)) === 'day');
    liveAgentPos[a.id] = { tx: pos.tx, ty: pos.ty, zone, moving, dead: !!dead, ill: !!ill,
                           atWork, onShift: !!shiftNow, offDuty: !!offDuty,
                           shopping: !!shopping, shop: shop || null,
                           withGuardian: withGuardian || null, carried: !!carried,
                           // Without these the new states existed in
                           // agentPosition and nowhere else, so a terminally
                           // ill officer lying on a ward was labelled "On
                           // patrol".
                           admitted: !!admitted, patrolling: !!patrolling,
                           byCar: !!byCar, guardPost: guardPost || null,
                           strolling: !!strolling, atNight: !!atNight };
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
  const clock = fmtClock(hourFrac).h24;
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
  if (el) paint(el, rows.map(r =>
    '<div class="occ-row"><span class="occ-name">' + r.icon + ' ' + r.label + '</span>' +
    '<span class="occ-bar"><span style="width:' + (r.n / max * 100).toFixed(0) + '%"></span></span>' +
    '<span class="occ-n">' + r.n + '</span></div>').join(''));
}

function renderGovernment(hourFrac) {
  const treasury = treasuryOn(hourFrac);
  // Retired: this top-bar figure is now owned by vitals.js, which reads the
  // world. The client-side value came from the removed model and disagreed
  // with the ledger - treasury showed -4,737 while the ledger said +220,
  // and town wealth showed NaN once the cast changed under it. Two writers
  // racing, and the wrong one often won.
  // const hud = document.getElementById('hudTreasury');
  // if (hud) hud.textContent = treasury.toLocaleString();
  const el = document.getElementById('govBody');
  if (!el) return;
  const clerks = AGENTS.filter(a => a.occ === 'government_clerk');
  paint(el,
    // Who actually holds the seats, and where each of them is standing right
    // now. "1 PM + 4 Ministers" told you the shape of the charter and nothing
    // about the five people it describes - not even their names.
    '<div class="section-lbl">Who governs, and where they are</div>' +
    '<div class="seat-list">' +
      seatHolders().map(sd => {
        const p = liveAgentPos[sd.agent.id] || {};
        const z = ZONES[p.zone];
        return '<div class="seat-row" data-id="' + sd.agent.id + '" role="button" tabindex="0">' +
          '<span class="seat-badge ' + (sd.role === 'Prime Minister' ? 'pm' : '') + '">' +
            (sd.role === 'Prime Minister' ? '⭐ PM' : '🏛️ Minister') + '</span>' +
          '<span class="seat-who"><b>' + sd.agent.n + '</b>' +
            '<em>' + sd.salary + ' sim/day</em></span>' +
          '<span class="seat-where">' + (z ? z.label : (p.zone || 'unknown')) +
            (p.moving ? ' · on the move' : '') + '</span></div>';
      }).join('') +
    '</div>' +
    '<div class="score-note">Click a name to open their file and find them on the map. ' +
      'They wear the sash of office, so they can be picked out of a crowd.</div>' +
    '<div class="gov-fact"><span class="k">Charter seats</span><span class="v">1 PM + 4 Ministers</span>' +
      '<span class="gov-help">The charter allows exactly five elected seats.</span></div>' +
    '<div class="gov-fact"><span class="k">Tax rate</span><span class="v">' + (TAX_RATE_BPS / 100).toFixed(1) + '%</span></div>' +
    '<div class="gov-fact"><span class="k">Head tax / person / day</span><span class="v">' + HEAD_TAX_PER_DAY + ' sim</span></div>' +
    '<div class="gov-fact"><span class="k">Collected today</span><span class="v">' + (HEAD_TAX_PER_DAY * POP).toLocaleString() + ' sim</span></div>' +
    '<div class="gov-fact"><span class="k">Treasury (running)</span><span class="v">' + treasury.toLocaleString() + ' sim</span></div>' +
    '<div class="gov-fact"><span class="k">Treasury (USD, genesis)</span><span class="v">$' + GENESIS_TREASURY_USD.toFixed(2) + '</span></div>' +
    (() => {
      const reg = landRegister();
      const total = reg.government + reg.private;
      return '<div class="gov-fact"><span class="k">Land held by the town</span><span class="v">' +
        reg.government.toLocaleString() + ' units</span>' +
        '<span class="gov-help">Land is divided into 1×1 squares. These ' +
          Math.round(reg.government / (total || 1) * 100) + '% are public — the roads, the ' +
          'square, the park, the farmland, and every plot nobody has claimed. The town can ' +
          'build on them or auction them.</span></div>' +
      '<div class="gov-fact"><span class="k">Land in private hands</span><span class="v">' +
        reg.private.toLocaleString() + ' units</span>' +
        '<span class="gov-help">The other ' + Math.round(reg.private / (total || 1) * 100) +
          '% belong to households — the ground their homes stand on. Open any resident\'s ' +
          'file to see their address and the squares they hold.</span></div>';
    })() +
    (() => {
      const withWill = AGENTS.filter(x => isAlive(x) && willOf(x));
      const living = AGENTS.filter(isAlive);
      return statFactHtml('Wills on file', withWill.length + ' of ' + living.length,
        'gov-wills', 'Residents who have written down who inherits their home and land ' +
        'when they die. Without one, an estate passes to the nearest living relative — ' +
        'spouse first, then children.', withWill.map(a => a.id), 'Residents with a will');
    })() +
    (() => {
      const came = cameOfAgeOn(Math.floor(hourFrac / 24));
      return statFactHtml('Came of age today', String(came.length), 'gov-coa',
        'Minors who turned 18 today. From now on they pay for their own thinking instead ' +
        'of a parent paying, they take up a trade, and they decide whether to stay home, ' +
        'move out alone, or pair off.', came.map(a => a.id), 'Turned 18 today');
    })() +
    '<div class="gov-fact"><span class="k">Town guard</span><span class="v">' +
      (guardDetail(Math.floor(hourFrac / 24)).map(g => g.n).join(', ') || 'not yet funded') +
      '</span></div>' +
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
      '<li>A surplus is spent on the town, in order, as soon as it can be afforded.</li>' +
    '</ul>' +
    '<div class="section-lbl">What the government has built</div>' +
    (() => {
      const rows = initiativesOn(Math.floor(hourFrac / 24));
      const done = rows.filter(r => r.funded);
      const next = rows.find(r => !r.funded);
      const treasury = treasuryOn(hourFrac);
      return rows.map(r =>
        '<div class="init ' + (r.funded ? 'done' : 'pending') + '">' +
          '<span class="init-icon">' + r.icon + '</span>' +
          '<span class="init-main"><b>' + r.name + '</b>' +
            '<em>' + r.note + '</em></span>' +
          '<span class="init-state">' +
            (r.funded ? 'built · day ' + r.day
                      : r.cost.toLocaleString() + ' sim') +
          '</span>' +
        '</div>').join('') +
        '<div class="init-note">' +
          done.length + ' of ' + rows.length + ' funded' +
          (next ? ' · next: ' + next.name + ', ' +
                  Math.max(0, next.cost - treasury).toLocaleString() + ' sim short' : ' · all built') +
        '</div>';
    })() +
    '<div class="section-lbl">Civil service (' + clerks.length + ' clerks)</div>' +
    '<div>' + clerks.map(c => '<span class="clerk-chip" data-id="' + c.id + '">' + c.n + '</span>').join('') + '</div>');
  el.querySelectorAll('.clerk-chip').forEach(c =>
    c.addEventListener('click', () => selectAgent(c.dataset.id)));
  wireStats(el, () => renderGovernment(hourFrac));
}

// Which household rows the reader has opened. Held outside the render so the
// 1.2-second refresh does not collapse a family the moment you open it.
const openHouseholds = new Set();

/** A household's family name: the surname most of its members share. Genesis
 *  gives households an id ("h0043"), which is what the town bills - but it is
 *  not what anyone calls them. */
function familyNameOf(people) {
  const counts = {};
  for (const r of people) {
    const parts = String(r.a.n || '').trim().split(/\s+/);
    const surname = parts[parts.length - 1];
    if (surname) counts[surname] = (counts[surname] || 0) + 1;
  }
  let best = null, top = 0;
  for (const surname in counts) if (counts[surname] > top) { top = counts[surname]; best = surname; }
  return best ? best + ' family' : 'Household';
}

/** What a member is to the household's purse - a job, or the reason they
 *  have not got one. The wealth ranking counts only earners, so the family
 *  list has to say plainly which members those are, and agree with the
 *  header's "n earning" rather than contradicting it. */
function memberRole(a, hourFrac) {
  const br = bracketAt(a, hourFrac);
  if (br === 'elderly') return 'Retired';
  if (br === 'minor') {
    const age = ageAt(a, hourFrac);
    if (age < 1) return 'Newborn';
    return age >= 5 ? 'At school' : 'Child';
  }
  // Working. Someone who came of age during the run still carries "student"
  // in their frozen genesis record; the trade they actually took up is what
  // their own record shows (see the Coming of age panel), so this says the
  // same rather than calling a working adult a student.
  if (a.occ === 'student') return OCC_LABEL(entryTrade(a));
  return OCC_LABEL(a.occ);
}

function renderScoreboard(hourFrac) {
  const el = document.getElementById('scoreBody');
  if (!el) return;
  const rows = AGENTS.map(a => ({ a, bal: balanceNow(a, hourFrac), spend: costSpent(a, hourFrac) }));
  const total = rows.reduce((s, r) => s + r.bal, 0);
  const spend = rows.reduce((s, r) => s + r.spend, 0);
  const broke = rows.filter(r => r.bal < 1000).length;
  // A newborn with nothing is not the poorest person in town - it is a
  // baby, and a leaderboard that says otherwise is measuring age, not
  // poverty. Only people who can actually earn are ranked; the retired are
  // excluded for the mirror-image reason (a pension is not a wage, and
  // ranking them against earners says nothing about either).
  const earners = rows.filter(r => bracketAt(r.a, hourFrac) === 'working');
  const ranked = earners.length ? earners : rows;
  const richest = ranked.slice().sort((x, y) => y.bal - x.bal).slice(0, 5);
  const poorest = ranked.slice().sort((x, y) => x.bal - y.bal).slice(0, 5);
  // Families are ranked as families, per section 5.2's "the household is
  // billed as one" - a household's wealth is what its members hold together.
  const byHousehold = {};
  rows.forEach(r => {
    const hh = r.a.hh || 'none';
    (byHousehold[hh] = byHousehold[hh] || { hh, bal: 0, members: 0, earners: 0, people: [] });
    byHousehold[hh].bal += r.bal;
    byHousehold[hh].members += 1;
    byHousehold[hh].people.push(r);
    if (bracketAt(r.a, hourFrac) === 'working') byHousehold[hh].earners += 1;
  });
  // The same rule the individual leaderboard uses, applied to families: a
  // household with nobody of working age in it is not "the tightest household
  // in town", it is a retired person living alone - ranking it says something
  // about life stage, not about hardship, exactly as ranking a newborn did.
  const households = Object.values(byHousehold).filter(h => h.hh !== 'none');
  const earningHomes = households.filter(h => h.earners > 0);
  const rankedHomes = earningHomes.length ? earningHomes : households;
  const richestHomes = rankedHomes.slice().sort((x, y) => y.bal - x.bal).slice(0, 3);
  const poorestHomes = rankedHomes.slice().sort((x, y) => x.bal - y.bal).slice(0, 3);
  const mean = Math.round(total / rows.length);
  const sorted = rows.map(r => r.bal).sort((x, y) => x - y);
  const median = sorted[Math.floor(sorted.length / 2)];

  const hudSpend = document.getElementById('hudSpend');
  if (hudSpend) hudSpend.textContent = '$' + spend.toFixed(2);
  // The cap is the sum of every resident's real $2 wallet, so it grows with
  // the population instead of staying pinned at the genesis 250's $500.
  const capEl = document.getElementById('hudSpendCap');
  if (capEl) capEl.textContent = '$' + (AGENTS.reduce((t, a) => t + a.usd, 0) / 100).toFixed(2);
  // Retired: this top-bar figure is now owned by vitals.js, which reads the
  // world. The client-side value came from the removed model and disagreed
  // with the ledger - treasury showed -4,737 while the ledger said +220,
  // and town wealth showed NaN once the cast changed under it. Two writers
  // racing, and the wrong one often won.
  // const hudWealth = document.getElementById('hudWealth');
  // if (hudWealth) hudWealth.textContent = total.toLocaleString();

  // Households are ranked as one purse, the way the town bills them - and a
  // household on a leaderboard is a question ("who are they?"), so the row
  // opens to the family it stands for rather than being a dead end. Each
  // member is a link on to their own record.
  const homeLine = (label, list) => '<div class="score-list"><div class="section-lbl">' + label + '</div>' +
    list.map(h => {
      const open = openHouseholds.has(h.hh);
      const fam = h.people.slice().sort((x, y) => ageAt(y.a, hourFrac) - ageAt(x.a, hourFrac));
      return '<div class="score-row hh-row' + (open ? ' open' : '') + '" data-hh="' + h.hh + '"' +
        ' role="button" tabindex="0" aria-expanded="' + open + '"' +
        ' title="Show the family in this household">' +
        '<span class="score-name"><span class="hh-caret" aria-hidden="true">\u25B8</span>' +
          familyNameOf(h.people) + '</span>' +
        '<span class="score-occ">' + h.hh + ' \u00B7 ' + h.members + ' member' +
          (h.members === 1 ? '' : 's') + ', ' + h.earners + ' earning</span>' +
        '<span class="score-bal">' + h.bal.toLocaleString() + '</span></div>' +
        (open
          ? '<div class="hh-members">' + fam.map(r =>
              '<div class="hh-member" data-id="' + r.a.id + '" role="button" tabindex="0"' +
                ' title="Open ' + r.a.n + '\u2019s record">' +
                '<span class="hm-name">' + r.a.n + '</span>' +
                '<span class="hm-role">' + memberRole(r.a, hourFrac) + '</span>' +
                '<span class="hm-age">' + Math.floor(ageAt(r.a, hourFrac)) +
                  (r.a.sex === 'm' ? 'M' : 'F') + '</span>' +
                '<span class="hm-bal">' + r.bal.toLocaleString() + '</span>' +
              '</div>').join('') + '</div>'
          : '');
    }).join('') + '</div>';

  const line = (label, list) => '<div class="score-list"><div class="section-lbl">' + label + '</div>' +
    list.map(r =>
      '<div class="score-row" data-id="' + r.a.id + '">' +
      '<span class="score-name">' + r.a.n + '</span>' +
      '<span class="score-occ">' + OCC_LABEL(r.a.occ) + '</span>' +
      '<span class="score-bal">' + r.bal.toLocaleString() + '</span></div>').join('') + '</div>';

  paint(el,
    '<div class="score-grid">' +
      '<div class="score-stat"><span class="sv">' + total.toLocaleString() + '</span><span class="sk">total sim in the town</span></div>' +
      '<div class="score-stat"><span class="sv">' + mean.toLocaleString() + '</span><span class="sk">mean balance</span></div>' +
      '<div class="score-stat"><span class="sv">' + median.toLocaleString() + '</span><span class="sk">median balance</span></div>' +
      '<div class="score-stat"><span class="sv">$' + spend.toFixed(2) + '</span><span class="sk">cognition spent / $500.00</span></div>' +
      '<div class="score-stat ' + (broke ? 'warn' : '') + '"><span class="sv">' + broke + '</span><span class="sk">under 1,000 sim</span></div>' +
      '<div class="score-stat"><span class="sv">' + HEAD_TAX_PER_DAY * POP + '</span><span class="sk">tax collected / day</span></div>' +
    '</div>' +
    line('Wealthiest — working people', richest) +
    line('Tightest — working people', poorest) +
    homeLine('Wealthiest households — with earners', richestHomes) +
    homeLine('Tightest households — with earners', poorestHomes) +
    '<div class="score-note">' + (households.length - earningHomes.length) +
      ' household' + (households.length - earningHomes.length === 1 ? '' : 's') +
      ' of ' + households.length + ' have no one of working age and are not ranked.</div>');
  // Only rows that name a person open a person. The old selector caught the
  // household rows too and called selectAgent(undefined) on every click.
  el.querySelectorAll('.score-row[data-id]').forEach(r =>
    r.addEventListener('click', () => selectAgent(r.dataset.id)));
  el.querySelectorAll('.hh-row').forEach(r => {
    const toggle = () => {
      const hh = r.dataset.hh;
      if (openHouseholds.has(hh)) openHouseholds.delete(hh); else openHouseholds.add(hh);
      renderScoreboard(state.hourFrac);
    };
    r.addEventListener('click', toggle);
    r.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });
  el.querySelectorAll('.hh-member').forEach(m => {
    const open = e => { e.stopPropagation(); selectAgent(m.dataset.id); };
    m.addEventListener('click', open);
    m.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(e); }
    });
  });
}

/** Put the shops' state on their own signs, so you can see from the map which
 *  ones can feed you without clicking anything. Recomputed each frame off the
 *  same larder the shelves and the food panel read. */
let _lastShopSigns = null;
function refreshShopSigns() {
  if (!V.setLabelBadges) return;
  const pop = AGENTS.filter(isAlive).length || 1;
  const spec = rule('market.goods', {});
  const badges = {};
  for (const zone of Object.keys(FOOD_SHOPS)) {
    if (zone === 'marketrow') continue;              // a district, not a shop
    const sells = FOOD_SHOPS[zone] || [];
    let covered = 0, want = 0;
    for (const g of sells) {
      const need = pop * ((spec[g] || {}).demand_per_resident_day || 0);
      want += need;
      covered += Math.min(foodSim.stock[g] || 0, need);
    }
    const days = want > 0 ? covered / want : 1;
    if (days < 0.05)     badges[zone] = { text: 'SOLD OUT', fg: '#7A1F1A', bg: '#F6C9C4' };
    else if (days < 0.6) badges[zone] = { text: 'LOW',      fg: '#6B4A05', bg: '#F7DFA6' };
    // A shop with food on the shelves needs no sign about it.
  }
  // UI-29: the badge set is recomputed every second and is almost always
  // the same set. `setLabelBadges` walks the renderer's label layer, so
  // handing it an identical map is work the map cannot possibly need.
  const key = JSON.stringify(badges);
  if (key === _lastShopSigns) return;
  _lastShopSigns = key;
  V.setLabelBadges(badges);
}

/** The food panel: the chain from the fields to the table, in one place. */
function renderFood() {
  const el = document.getElementById('foodBody');
  if (!el) return;
  const day = Math.floor(state.hourFrac / 24);
  advanceHealthTo(day);
  const h = foodSim.history[0];
  if (!h) { el.innerHTML = '<div class="pc-empty">No day has been settled yet.</div>'; return; }

  const goods = rule('market.goods', {});
  const edible = edibleGoods();
  const pop = AGENTS.filter(isAlive).length || 1;
  const hungry = AGENTS.filter(a => isAlive(a) && isHungry(a))
    .map(a => ({ a, days: hungerDaysOf(a) }))
    .sort((x, y) => y.days - x.days);
  const toIll = rule('survival.daily_needs.hunger_days_to_illness', 3);

  const levelClass = h.level === 'shortage' ? 'critical'
                   : (h.hungryEmpty + h.hungryBroke) > 0 || h.level === 'watch' ? 'warn' : '';
  // The headline follows what happened to people, not only the supply ratio:
  // "FED" over a day when sixteen residents found empty shelves is a lie the
  // arithmetic tells.
  const wentWithout = h.hungryEmpty + h.hungryBroke;
  const levelWord = h.level === 'shortage' ? 'SHORTAGE'
                  : wentWithout > 0 ? 'SOME WENT WITHOUT'
                  : h.level === 'watch' ? 'RUNNING SHORT' : 'ALL FED';

  // A unit of food is not an obvious quantity, and "11.1 sim" is not an
  // obvious price, so both are said in terms of a person and a day.
  const perHead = rule('survival.daily_needs.food_units', 0.5);
  const daysPerUnit = perHead > 0 ? 1 / perHead : 1;
  const basePrice = edible.reduce((t, g) => t + goods[g].base_price_sim, 0) / edible.length;
  const dayCost = h.price * perHead;
  const medianWage = 125;                     // a farmer's day, for comparison
  const fedIds = AGENTS.filter(a => isAlive(a) && !isHungry(a)).map(a => a.id);
  const hungryIds = AGENTS.filter(a => isAlive(a) && isHungry(a)).map(a => a.id);

  el.innerHTML =
    '<div class="score-grid">' +
      statTile(levelWord, 'supply vs what the town eats', { cls: levelClass,
        // Say what happened, not only what the ratio was: "enough for everyone"
        // while sixteen people found empty shelves is a sentence nobody should
        // have to reconcile.
        help: 'The town grew ' + Math.round(h.supply) + ' units today and eats ' +
          Math.round(h.demand) + ' a day. ' +
          ((h.hungryEmpty + h.hungryBroke) === 0
            ? 'Everyone ate.'
            : (h.hungryEmpty + h.hungryBroke) + ' still went without — the larder ran ' +
              'down before everybody had been served.') }) +
      statTile(h.fed, 'ate today', { key: 'f-fed', ids: fedIds, label: 'Ate today',
        help: 'Bought and ate a full day\'s food.' }) +
      statTile(h.hungryEmpty + h.hungryBroke, 'went hungry',
        { cls: (h.hungryEmpty + h.hungryBroke) ? 'warn' : '', key: 'f-hungry',
          ids: hungryIds, label: 'Went hungry today',
          note: 'After ' + rule('survival.daily_needs.hunger_days_to_illness', 3) +
                ' days without food a resident falls ill; after ' +
                rule('survival.daily_needs.starvation_days_to_death', 12) + ' they die.',
          help: h.hungryEmpty + ' found the shelves empty, ' + h.hungryBroke +
                ' could not afford the price.' }) +
      statTile(dayCost.toFixed(1) + ' sim', 'to eat for one day',
        { help: 'One unit costs ' + h.price.toFixed(1) + ' sim and feeds one person for ' +
          (daysPerUnit === 1 ? 'a day' : daysPerUnit + ' days') + '. ' +
          'That is ' + Math.round(dayCost / medianWage * 100) + '% of a farmer\'s daily wage' +
          (h.price > basePrice * 1.2
            ? ' — up from ' + (basePrice * perHead).toFixed(1) + ' sim when food is plentiful.'
            : '.') }) +
    '</div>' +
    statDrillHtml() +

    '<div class="score-list"><div class="section-lbl">Where today’s food came from</div>' +
      '<div class="score-note">Each row: how many people worked that land today, how much ' +
      'each of them brings in, how many units resulted, and what one unit sells for. ' +
      'One unit feeds one person for ' + (daysPerUnit === 1 ? 'a day' : daysPerUnit + ' days') +
      '.</div>' +
      Object.keys(goods).filter(g => !g.startsWith('_') && edible.indexOf(g) >= 0).map(g => {
        const made = h.produced[g] || 0;
        const hands = AGENTS.filter(a => isAlive(a) && a.dayZone === FOOD_SITE[g]).length;
        return '<div class="score-row">' +
          '<span class="score-name">' + g + '</span>' +
          '<span class="score-occ">' + hands + ' working ' + (FOOD_SITE[g] || '—') +
            ' · ' + goods[g].units_per_worker_day + '/head' +
            (goods[g].consumes ? ' · from ' + Object.keys(goods[g].consumes).join(', ') : '') + '</span>' +
          '<span class="score-bal">' + Math.round(made) + ' made · ' +
            (foodSim.price[g] || 0).toFixed(1) + ' sim</span></div>';
      }).join('') + '</div>' +

    (hungry.length
      ? '<div class="score-list"><div class="section-lbl">Going hungry ' +
          '<span class="pc-count">' + hungry.length + '</span></div>' +
        hungry.slice(0, 12).map(r =>
          '<div class="score-row" data-id="' + r.a.id + '">' +
            '<span class="score-name">' + r.a.n + '</span>' +
            '<span class="score-occ">' + titleOf(r.a) + ' · ' +
              Math.round(balanceNow(r.a, state.hourFrac)).toLocaleString() + ' sim</span>' +
            '<span class="score-bal' + (r.days >= toIll ? ' bad' : '') + '">' +
              r.days + ' day' + (r.days === 1 ? '' : 's') + '</span></div>').join('') +
        (hungry.length > 12 ? '<div class="pc-more">+ ' + (hungry.length - 12) + ' more</div>' : '') +
        '<div class="score-note">' + toIll + ' days without food makes a resident ill.</div>' +
        '</div>'
      : '<div class="score-note">Everyone ate today.</div>') +

    (foodSim.log.length
      ? '<div class="score-list"><div class="section-lbl">Food log</div>' +
        foodSim.log.slice(0, 8).map(l =>
          '<div class="score-row"><span class="score-name">Day ' + l.day + '</span>' +
          '<span class="score-occ">' + (l.name ? l.name + ' ' : '') + l.what + '</span></div>').join('') +
        '</div>'
      : '');

  el.querySelectorAll('.score-row[data-id]').forEach(row =>
    row.addEventListener('click', () => selectAgent(row.dataset.id)));
  wireStats(el, renderFood);
}

// ---- search ---------------------------------------------------------------
// One box for both, the way a maps search works: type a name and get people
// and places back together, ranked by how well they match rather than by
// which list they came from.

function searchIndex() {
  if (searchIndex._rows) return searchIndex._rows;
  const rows = [];
  for (const zoneId of Object.keys(ZONES)) {
    const z = ZONES[zoneId];
    if (!z.label) continue;             // never offer an unnamed place as a hit
    rows.push({
      kind: 'place', id: zoneId, name: z.label, emoji: z.icon || '📍',
      sub: 'district', x: z.x + z.w / 2, y: z.y + z.h / 2,
      hay: (z.label + ' ' + zoneId).toLowerCase(),
    });
  }
  for (const b of plans) {
    if (!b.label) continue;
    const depth = b.d !== undefined ? b.d : b.h;
    const z = ZONES[b.zone || b.zid];
    rows.push({
      kind: 'place', id: 'b:' + b.x + ',' + b.y, name: b.label,
      emoji: b.icon || (z && z.icon) || '🏢', sub: 'building',
      x: b.x + b.w / 2, y: b.y + depth / 2, building: b,
      hay: b.label.toLowerCase(),
    });
  }
  for (const a of AGENTS) {
    rows.push({
      kind: 'person', id: a.id, name: a.n, emoji: occFace(a.occ)[0],
      sub: titleOf(a) + ' · ' + a.age + (a.sex === 'm' ? 'M' : 'F'),
      // Searchable by the office they hold as well as their name and trade -
      // "prime minister" and "minister" are what an operator actually types.
      hay: (a.n + ' ' + a.occ + ' ' + (a.rn || '') + ' ' + (seatRoleOf(a) || '')).toLowerCase(),
    });
  }
  return (searchIndex._rows = rows);
}

function runSearch(query) {
  const q = (query || '').trim().toLowerCase();
  if (q.length < 2) return [];
  const scored = [];
  for (const row of searchIndex()) {
    const at = row.hay.indexOf(q);
    if (at < 0) continue;
    // A name that STARTS with what you typed is what you meant; a place
    // outranks a person on an equal match, because there are three hundred
    // people and twenty-eight places and the place is the rarer hit.
    const kindBias = row.kind === 'place' ? (row.building ? 0 : 0.05) : 0.5;
    scored.push({ row, score: (at === 0 ? 0 : 1) + kindBias + at * 0.01 });
  }
  scored.sort((a, b) => a.score - b.score || a.row.name.localeCompare(b.row.name));
  // A district and the building in it often share a name ("HOSPITAL"), and
  // two identical rows in a result list is a bug, not a choice. The first
  // one kept wins, and the sort has already put the better match first.
  const out = [], seen = new Set();
  for (const { row } of scored) {
    const key = row.kind + '|' + row.name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
    if (out.length >= 12) break;
  }
  return out;
}

function renderSearchResults(rows) {
  const box = document.getElementById('mapSearchResults');
  if (!box) return;
  if (!rows.length) { box.hidden = true; box.innerHTML = ''; return; }
  box.innerHTML = rows.map((r, i) =>
    '<button class="ms-row" type="button" data-idx="' + i + '">' +
      '<span class="ms-emoji">' + r.emoji + '</span>' +
      '<span class="ms-main"><span class="ms-name">' + r.name + '</span>' +
        '<span class="ms-sub">' + r.sub + '</span></span>' +
      '<span class="ms-kind">' + (r.kind === 'place' ? 'PLACE' : 'PERSON') + '</span>' +
    '</button>').join('');
  box.hidden = false;
  box.querySelectorAll('.ms-row').forEach(btn =>
    btn.addEventListener('click', () => gotoSearchResult(rows[+btn.dataset.idx])));
}

/** Take the camera to a hit, and open whatever it was. */
function gotoSearchResult(row) {
  if (!row) return;
  const box = document.getElementById('mapSearchResults');
  if (box) box.hidden = true;
  const input = document.getElementById('mapSearch');
  if (input) input.blur();

  if (row.kind === 'person') {
    const p = liveAgentPos[row.id];
    if (p) { V.zoomBy(Math.max(1, 1.1 / V.zoom)); V.focus(p.tx, p.ty); }
    hidePlaceCard();
    selectAgent(row.id, { reveal: false });
    return;
  }
  V.zoomBy(Math.max(1, 0.85 / V.zoom));
  V.focus(row.x, row.y);
  hideMapCard();
  if (row.building) selectPlace(row.building);
  else {
    // A district: open the biggest labelled building in it, which is the
    // one a person means when they search "Hospital".
    const inZone = plans.filter(b => (b.zone || b.zid) === row.id && b.label);
    const biggest = inZone.sort((a, b) => (b.w * b.d) - (a.w * a.d))[0];
    if (biggest) selectPlace(biggest);
    else hidePlaceCard();
  }
}

function wireSearch() {
  const input = document.getElementById('mapSearch');
  const clear = document.getElementById('mapSearchClear');
  const box = document.getElementById('mapSearchResults');
  if (!input) return;
  let rows = [];
  const refresh = () => { rows = runSearch(input.value); renderSearchResults(rows); };
  input.addEventListener('input', refresh);
  input.addEventListener('focus', refresh);
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && rows.length) { ev.preventDefault(); gotoSearchResult(rows[0]); }
    if (ev.key === 'Escape') { input.value = ''; renderSearchResults([]); input.blur(); }
  });
  if (clear) clear.addEventListener('click', () => {
    input.value = ''; renderSearchResults([]); input.focus();
  });
  // Clicking anywhere else closes the dropdown, same as a maps search.
  document.addEventListener('click', ev => {
    if (box && !box.hidden && !ev.target.closest('.map-search')) box.hidden = true;
  });
}

// ---- places: what is happening inside a building -------------------------
// Clicking a building should answer the question a person actually has -
// "who is in there and what are they doing" - not show a label. For a
// hospital that means the doctors, the nurses and the patients, listed
// separately, because "15 people" is not what anyone wanted to know.

/** Everyone standing inside this building's footprint right now. */
function peopleIn(building) {
  const depth = building.d !== undefined ? building.d : building.h;
  const out = [];
  for (const a of AGENTS) {
    if (!isAlive(a)) continue;
    const p = liveAgentPos[a.id];
    if (!p) continue;
    if (p.tx < building.x || p.tx >= building.x + building.w) continue;
    if (p.ty < building.y || p.ty >= building.y + depth) continue;
    out.push({ a, p });
  }
  return out;
}

/** Everyone in the DISTRICT this building belongs to - the fallback when a
 *  building's own footprint is empty because its people are in the yard. */
function peopleInZone(zoneId) {
  const out = [];
  for (const a of AGENTS) {
    if (!isAlive(a)) continue;
    const p = liveAgentPos[a.id];
    if (p && p.zone === zoneId) out.push({ a, p });
  }
  return out;
}

// How each kind of place sorts the people in it. The first matching rule
// wins, so a doctor on shift is staff, not "someone here".
const PLACE_ROLES = {
  hospital: [
    ['On duty — doctors',  r => r.a.occ === 'doctor' && r.p.onShift],
    ['On duty — nurses',   r => r.a.occ === 'nurse' && r.p.onShift],
    ['Patients',           r => r.p.ill],
    ['Hospital staff',     r => r.a.dayZone === 'hospital'],
  ],
  police: [
    ['On duty — officers', r => r.a.occ === 'police_officer' && r.p.onShift],
    ['Off-duty officers',  r => r.a.occ === 'police_officer'],
  ],
  fire: [
    ['On duty — crew',     r => r.a.occ === 'firefighter' && r.p.onShift],
    ['Off-duty crew',      r => r.a.occ === 'firefighter'],
  ],
  bank: [
    ['Bankers',            r => r.a.occ === 'banker'],
    ['Customers',          r => r.a.dayZone !== 'bank'],
  ],
  government: [
    ['Prime Minister',       r => seatRoleOf(r.a) === 'Prime Minister'],
    ['Ministers',            r => seatRoleOf(r.a) === 'Minister'],
    ['Government clerks',    r => r.a.occ === 'government_clerk'],
    ['Security',             r => r.a.occ === 'police_officer'],
    ['Petitioners',          r => r.a.dayZone !== 'government'],
  ],
  school:  [['Teachers', r => r.a.occ === 'school_teacher'], ['Pupils', () => true]],
  college: [['Lecturers', r => r.a.occ === 'college_instructor'],
            ['Students', r => r.occ === 'student' || true]],
  docks:   [['Dock workers', r => r.a.dayZone === 'docks'], ['Visitors', () => true]],
  pasture: [['Herders', r => r.a.dayZone === 'pasture'], ['Visitors', () => true]],
  orchard: [['Orchard hands', r => r.a.dayZone === 'orchard'], ['Visitors', () => true]],
  farmland:[['Farmers', r => r.a.dayZone === 'farmland'], ['Visitors', () => true]],
  // A shop's people are its staff and its customers, and they are not the same
  // thing - the whole point of walking them here is being able to tell.
  grocery: [['Behind the counter', r => r.a.dayZone === 'grocery'],
            ['Buying food',        r => r.p.shopping],
            ['Also here',          () => true]],
  bakery:  [['Behind the counter', r => r.a.dayZone === 'bakery'],
            ['Buying bread',       r => r.p.shopping],
            ['Also here',          () => true]],
  // The rest of the outdoor districts, now that they are clickable at all.
  forest:  [['Foresters', r => r.a.dayZone === 'forest'], ['Walkers', () => true]],
  oreoutcrop: [['Miners', r => r.a.dayZone === 'oreoutcrop'], ['Others here', () => true]],
  construction: [['Site crew', r => r.a.dayZone === 'construction'],
                 ['Labourers', r => r.a.occ === 'day_laborer'], ['Others here', () => true]],
  marketrow: [['Traders', r => r.a.occ === 'trader'],
              ['Shopkeepers', r => r.a.occ === 'shopkeeper'],
              ['Working the row', r => r.a.dayZone === 'marketrow'],
              ['Shoppers', () => true]],
  park:    [['Out in the park', () => true]],
  townsquare: [['In the square', () => true]],
  housing: [['At home', () => true]],
  housingEast: [['At home', () => true]],
  housingSouth: [['At home', () => true]],
};

function groupPlacePeople(zoneId, rows) {
  const rules = PLACE_ROLES[zoneId];
  const groups = [];
  const seen = new Set();
  if (rules) {
    for (const [label, test] of rules) {
      const members = rows.filter(r => !seen.has(r.a.id) && test(r));
      members.forEach(r => seen.add(r.a.id));
      if (members.length) groups.push({ label, members });
    }
  }
  const rest = rows.filter(r => !seen.has(r.a.id));
  if (rest.length) groups.push({ label: rules ? 'Also here' : 'Inside', members: rest });
  return groups;
}

let selectedPlace = null;

function selectPlace(building) {
  if (!building) { hidePlaceCard(); return; }
  selectedPlace = building;
  renderPlaceCard(building);
}

function hidePlaceCard() {
  selectedPlace = null;
  const el = document.getElementById('placeCard');
  if (el) el.hidden = true;
}

/** Every resident whose registered address is this building - not who happens
 *  to be standing in it. A house at noon is empty; it is still their house. */
function residentsOf(building) {
  return AGENTS.filter(a => isAlive(a) && a._home === building);
}

function placeTitle(building) {
  if (building.label) return building.label;
  if (building.district) {
    const z = ZONES[building.zone];
    if (z && z.label) return z.label;
  }
  const z = ZONES[building.zone || building.zid];
  if (z) return z.label;
  if (building.home) {
    // "1 BHK" is the size of the dwelling, not the name of it. A home is
    // named for the people who live there, the way the wealth leaderboard
    // names a household.
    const live = residentsOf(building);
    if (live.length) {
      const fams = [];
      live.forEach(a => {
        const parts = String(a.n || '').trim().split(/\s+/);
        const sn = parts[parts.length - 1];
        if (sn && fams.indexOf(sn) < 0) fams.push(sn);
      });
      if (fams.length === 1) return fams[0] + ' family home';
      if (fams.length === 2) return fams[0] + ' & ' + fams[1] + ' homes';
      if (fams.length > 2) return fams.slice(0, 2).join(', ') + ' & ' + (fams.length - 2) + ' more';
    }
    return building.homeLabel || 'Dwelling';
  }
  return 'Outbuilding';
}

// The shops food is bought from, and which goods sit on each one's shelves.
const FOOD_SHOPS = {
  grocery: ['grain', 'produce', 'milk'],
  bakery:  ['bread'],
  pharmacy:['medicine'],
  marketrow: ['grain', 'produce', 'milk', 'bread', 'goods'],
};

/** What is on the shelves right now, and what it costs. */
function shelfHtml(zoneId) {
  const list = FOOD_SHOPS[zoneId] || [];
  const goods = rule('market.goods', {});
  const pop = AGENTS.filter(isAlive).length || 1;
  const rows = list.filter(g => goods[g]).map(g => {
    const stock = foodSim.stock[g] || 0;
    const price = foodSim.price[g] || goods[g].base_price_sim;
    const perDay = pop * (goods[g].demand_per_resident_day || 0);
    const days = perDay > 0 ? stock / perDay : Infinity;
    const state = days < 1 ? 'out' : days < 2 ? 'low' : 'in';
    const word = days < 1 ? 'SOLD OUT' : days < 2 ? 'LOW' : 'IN STOCK';
    return '<div class="shelf-row ' + state + '">' +
      '<span class="sh-good">' + g + '</span>' +
      '<span class="sh-state">' + word + '</span>' +
      '<span class="sh-days">' + (isFinite(days) ? days.toFixed(1) + ' days' : '—') + '</span>' +
      '<span class="sh-price">' + price.toFixed(1) + ' sim</span></div>';
  }).join('');
  if (!rows) return '';
  return '<div class="pc-group"><div class="section-lbl">On the shelves ' +
    '<span class="pc-count">' + list.length + '</span></div>' + rows +
    '<div class="score-note">Grown at the farmland, orchard and pasture; ' +
    'priced by what the town has against what it eats.</div></div>';
}

function renderPlaceCard(building) {
  const el = document.getElementById('placeCard');
  if (!el) return;
  const zoneId = building.zone || building.zid || '';
  // A district covers its whole rect, so "who is inside it" IS "who is in the
  // district" - no fallback needed and none wanted.
  let rows = building.district ? peopleInZone(zoneId) : peopleIn(building);
  let scope = building.district ? 'in this district' : 'inside';
  if (!rows.length && building.home) {
    // Everyone is out at work. Show who LIVES here rather than "nobody is
    // here right now", which is true and useless.
    rows = residentsOf(building).map(a => ({ a, p: liveAgentPos[a.id] || {} }))
                                .filter(r => r.p.tx !== undefined);
    scope = 'who live here';
  }
  if (!rows.length && zoneId) { rows = peopleInZone(zoneId); scope = 'in this district'; }
  const groups = groupPlacePeople(zoneId, rows);
  const zone = ZONES[zoneId];
  const hod = state.hourFrac % 24;

  const person = r =>
    '<div class="pc-person" data-id="' + r.a.id + '">' +
      '<span class="pc-dot" style="background:' + occFace(r.a.occ)[1] + '"></span>' +
      '<span class="pc-who"><b>' + r.a.n + '</b>' +
        '<em>' + (seatRoleOf(r.a) ? '<span class="pc-seat">' + seatRoleOf(r.a) + '</span> · ' : '') +
          OCC_LABEL(r.a.occ) + ' · ' + Math.floor(ageAt(r.a, state.hourFrac)) +
          (r.a.sex === 'm' ? 'M' : 'F') + '</em></span>' +
      '<span class="pc-doing">' + esc(activityFor(r.a, r.p.zone, hod)) + '</span>' +
    '</div>';

  const wrote = paint(el,
    '<button class="mc-close" id="placeCardClose" type="button" aria-label="Close">×</button>' +
    '<div class="pc-head">' +
      '<span class="pc-icon">' + (zone ? zone.icon : '🏠') + '</span>' +
      '<span class="pc-id"><b>' + placeTitle(building) + '</b>' +
        '<em>' + (building.district ? 'district'
                  : building.home ? (building.homeLabel || 'dwelling')
                  : (zone ? zone.label : (building.label || 'place'))) +
        ' · ' + rows.length + ' ' + (rows.length === 1 ? 'person' : 'people') + ' ' + scope + '</em></span>' +
    '</div>' +
    '<div class="pc-scroll">' +
    // A grocery with nothing on its shelves is a building with a sign on it.
    // These are the shops the town actually eats out of, so they show what is
    // in stock and what it costs today.
    (FOOD_SHOPS[zoneId] ? shelfHtml(zoneId) : '') +
    (groups.length
      ? groups.map(g =>
          '<div class="pc-group"><div class="section-lbl">' + g.label +
            ' <span class="pc-count">' + g.members.length + '</span></div>' +
          g.members.map(person).join('') +
          '</div>').join('')
      : '<div class="pc-empty">Nobody is here right now.</div>') +
    '</div>');

  el.hidden = false;
  // UI-29: only when paint() actually replaced the rows. This ran every
  // 400 ms while a place was selected, binding one click listener per
  // person row onto elements that were about to be destroyed and rebuilt
  // 400 ms later. On the 250-resident target with a busy district open
  // that is thousands of listeners a minute, all for a card whose text
  // has not changed.
  if (!wrote) return;
  el.querySelectorAll('.pc-person').forEach(row =>
    row.addEventListener('click', () => selectAgent(row.dataset.id, { reveal: false })));
  const close = document.getElementById('placeCardClose');
  if (close) close.addEventListener('click', ev => { ev.stopPropagation(); hidePlaceCard(); });
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

/* Who to call this resident.
 *
 * The world wins whenever it has an opinion. The viewer's roster is a real
 * genesis file, but not necessarily THIS run's genesis file, and a name is
 * the one field where being confidently wrong is worst: it puts one agent's
 * authored workflow, money and memories under another agent's face. */
function liveName(id, fallback) {
  const who = LIVE.identity && LIVE.identity[id];
  return (who && who.name) || fallback;
}
function liveOcc(id, fallback) {
  const who = LIVE.identity && LIVE.identity[id];
  return (who && who.occ) || fallback;
}
/* world/agents_api.py builds this with str() over a Sex enum, so it arrives
 * as "Sex.MALE" rather than "male". Normalised here rather than at the
 * source because that string is an existing published contract and other
 * readers may already be parsing it as it is. */
function liveSex(id, fallback) {
  const who = LIVE.identity && LIVE.identity[id];
  const raw = who && who.sex;
  if (!raw) return fallback;
  return /male/i.test(raw) && !/female/i.test(raw) ? 'm' : 'f';
}
window.liveName = liveName;

/* This file builds its HTML by string concatenation throughout, which is
 * safe for the genesis roster (a build artefact this repo generates) and is
 * NOT safe for the agent-authored text below: a tool name and its parameters
 * are written by a model, at run time, and land straight in innerHTML. */
/* Write HTML into a panel, but only when it would actually change.
 *
 * UI-29. `renderPlaceCard` ran every 400 ms while a place was selected,
 * rebuilding its innerHTML and rebinding a click listener per person row;
 * `renderOccupancy`, `renderGovernment` and `renderWork` ran on the same
 * 400 ms tick, `renderScoreboard` every 1.2 s over every agent with a
 * `balanceNow` per row, and `refreshShopSigns` every second. On the
 * 50-agent cast that is affordable. The design target is 250 with People
 * cards, and it is not.
 *
 * The expensive part is not building the string - it is assigning it.
 * `innerHTML =` makes the browser tear down and re-parse the whole
 * subtree, drop any text selection inside it, and reset its scroll
 * position; on the place card it also orphans every listener bound to the
 * old rows so they have to be bound again. Most 400 ms frames produce a
 * string identical to the one already on screen, and all of that was
 * being paid for nothing.
 *
 * Comparing the OUTPUT rather than fingerprinting the inputs is the
 * deliberate choice: a fingerprint has to be kept in step by hand with
 * everything a renderer reads, and the first field somebody forgets
 * becomes a panel that silently stops updating. This cannot go stale -
 * if the html differs by one character it is written.
 *
 * Every writer of a shared element must go through here. `placeCard` is
 * written by both renderPlaceCard and renderEnginePanel, and a writer
 * that bypassed this would leave `_painted` describing something that is
 * no longer on screen.
 *
 * Returns true if it wrote, so a caller can skip the work that only
 * matters when the DOM changed (rebinding listeners).
 */
function paint(el, html) {
  if (el._painted === html) return false;
  el._painted = html;
  el.innerHTML = html;
  return true;
}

function esc(s) {
  // UI-09. `'` is in the set because this file quotes attribute values both
  // ways, and an apostrophe is the one character agent prose is guaranteed
  // to contain.
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

/* ---- what this resident is doing, right now -----------------------------
 * The click on a sprite used to open a biography and a balance sheet: who
 * this person IS, with nothing about what they are DOING. The world knew all
 * along - every completed call appends a record, `say` appends an utterance -
 * and /world/activity now joins the two per agent onto the same poll that
 * carries the position, so the line and the place can never disagree.
 *
 * Every string below is escaped. `params` are agent-authored text on an
 * agent-authored tool: the one place in this file where the town writes the
 * content and the page must treat it strictly as data.
 */
/* Is the world showing this resident doing a job right now?
 *
 * Matched against the verbs run_genesis's _phrase_activity actually emits,
 * not a guess: harvesting, mining, felling, crafting, cooking, building,
 * buying, selling, teaching. A resident walking or looking around is not
 * working, and neither is one the world has said nothing about.
 */
const WORK_VERBS = /^(harvest|min|fell|craft|cook|build|buy|sell|teach|register)/i;
function isWorkingNow(id) {
  const now = LIVE.doing && LIVE.doing[id];
  return !!(now && now.line && WORK_VERBS.test(now.line));
}

/* Where the world says this resident is standing, if it is bound. */
/* What the world says this resident has actually practised.
 *
 * The chips this replaces are a build-time invention with an unlock
 * schedule ("Policing · unlocks day 3"), which over a real agent reads as
 * fact. An agent that has practised nothing yet is told so, because an
 * empty row and a fabricated one look identical otherwise.
 */
function liveSkillLine(id) {
  // UI-04: the map card wants one line, not a chip row. Same source.
  const rows = (LIVE.skills && LIVE.skills[id]) || [];
  if (!rows.length) return 'nothing practised yet';
  const named = rows.slice(0, 3).map(function (s) {
    return esc(s.name || s.skill || String(s));
  });
  return named.join(' · ') + (rows.length > 3 ? ' +' + (rows.length - 3) : '');
}

function liveSkillChips(id) {
  const rows = (LIVE.skills && LIVE.skills[id]) || [];
  if (!rows.length) {
    return '<span class="skill-chip locked">nothing practised yet</span>';
  }
  return rows.map(function (s) {
    const name = esc(s.name || s.skill || String(s));
    const level = (s.level != null) ? s.level : (s.proficiency != null ? s.proficiency : null);
    return '<span class="skill-chip">⚙️ ' + name +
           (level != null ? ' <b>' + level + '</b>' : '') + '</span>';
  }).join('');
}

function liveWhere(id) {
  return (LIVE.connected && LIVE.where && LIVE.where[id]) || null;
}

function doingBlock(id) {
  const now = LIVE.doing && LIVE.doing[id];
  const tail = (LIVE.recent && LIVE.recent[id]) || [];
  if (!LIVE.connected) return '';
  if (!now && !tail.length) {
    return '<div class="doing-now idle"><span class="doing-lbl">Doing</span>' +
           '<span class="doing-line">has not acted yet this run</span></div>';
  }
  const approx = LIVE.approx && LIVE.approx[id]
    ? '<span class="doing-approx" title="The town named a place this viewer ' +
      'has no district for, so the position is the nearest guess">approx. position</span>'
    : '';
  const lines = tail.slice().reverse().map(r => {
    const t = r.tick != null ? 'tick ' + r.tick : '';
    if (r.kind === 'said') {
      const to = r.to_name ? ' → ' + esc(r.to_name) : '';
      return '<li class="act said"><span class="act-t">' + t + '</span>' +
             '<span class="act-w">said' + to + '</span>' +
             '<span class="act-q">“' + esc(r.text || '') + '”</span></li>';
    }
    const p = r.params || {};
    const args = Object.keys(p).map(k => esc(k) + '=' + esc(String(p[k]))).join(' ');
    const authored = (r.tool === 'skillify' || r.tool === 'toolify' ||
                      r.tool === 'revise_skill' || r.tool === 'revise_tool');
    return '<li class="act' + (authored ? ' authored' : '') + '">' +
           '<span class="act-t">' + t + '</span>' +
           '<span class="act-w">' + esc(r.tool || '?') + '</span>' +
           (args ? '<span class="act-a">' + args + '</span>' : '') + '</li>';
  }).join('');
  return '<div class="doing-now">' +
    '<div class="doing-head"><span class="doing-lbl">Doing</span>' +
    '<span class="doing-line">' + esc((now && now.line) || tail[tail.length - 1].tool || '') +
    '</span>' + (now && now.tick != null
      ? '<span class="doing-tick">tick ' + now.tick + '</span>' : '') + approx + '</div>' +
    (lines ? '<ul class="act-list">' + lines + '</ul>' : '') +
  '</div>';
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

  // 'skills' is Part E.1 and sits second deliberately: a skill is the
  // thing this instrument exists to observe, and burying it behind
  // estate/memory would make the town's authored knowledge the least
  // reachable fact about a resident.
  const tabs = ['profile', 'skills', 'family', 'estate', 'memory', 'experts', 'money']
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
          ? HEALTH_LABEL[hs.ailment || 'healthy'] + ' · ' + capacityLineFor(a, hourFrac)
          : 'died day ' + (hs.deathDay || '—') + ' · ' + (hs.cause || 'unknown')) + '</span>' +
      '</div>' +
      (hs.alive
        ? '<div class="now-box"><span class="now-verb">' + esc(activityFor(a, p.zone || 'housing', hourFrac % 24)) + '</span>' +
          '<span class="now-at">at ' + zone + '</span></div>'
        : '<div class="now-box dead"><span class="now-verb">No longer in the town</span>' +
          '<span class="now-at">revival costs ' + REVIVAL_COST_SIM.toLocaleString() + ' sim</span></div>') +
      '<div class="section-lbl">Description</div><p class="agent-bio">' + esc(a.bio) + '</p>' +
      (a.tension ? '<div class="agent-quote"><span class="lbl">Internal tension</span>' + a.tension + '</div>' : '') +
      (a.fear ? '<div class="agent-quote"><span class="lbl">Fear</span>' + a.fear + '</div>' : '') +
      (a.ambition ? '<div class="agent-quote"><span class="lbl">Ambition</span>' + a.ambition + '</div>' : '') +
      (a.limit ? '<div class="agent-quote"><span class="lbl">Will not compromise</span>' + a.limit + '</div>' : '') +
      // Skills. For a bound resident these are what the WORLD says they
      // have practised; the chips below it are a build-time invention with
      // an unlock schedule, and shown over a real agent they read as fact.
      // Nell Holloway, artisan, was credited with "Policing · unlocks day 3".
      '<div class="section-lbl">Skills</div><div>' +
        (isBound(a)
          ? liveSkillChips(a.id)
          : '<span class="skill-chip">⚙️ ' + a.skillBase + '</span>' +
            '<span class="skill-chip ' + (unlocked ? '' : 'locked') + '">' +
              (unlocked ? '✨ ' + a.skillUnlocked : '🔒 unlocks day ' + a.skillUnlockDay) + '</span>') +
      '</div>' +
      '<dl class="facts">' +
        '<div><dt>Trait</dt><dd>' + a.trait + '</dd></div>' +
        '<div><dt>Habit</dt><dd>' + a.habit + '</dd></div>' +
        (() => {
          const d = dayPlaceLineFor(a, hourFrac);
          return '<div><dt>' + d.label + '</dt><dd>' +
            (d.id ? '<button class="rel-row inline" data-id="' + d.id + '">' + d.value + '</button>'
                  : d.value) + '</dd></div>';
        })() +
      '</dl>';
  } else if (inspectorTab === 'skills') {
    body = (window.PartE && PartE.skillsTabHtml)
      ? PartE.skillsTabHtml(a.id)
      : '<div class="inspector-empty">Skills view unavailable.</div>';
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
        // UI-04. `Balance now` above is the world's `sim_balance` for a
        // bound resident; these four are the model's payslip, and shown
        // beneath a real balance they read as its derivation. They are not:
        // the world's wages, upkeep and tax are whatever the run's
        // employers and charter did, and this file cannot see any of it.
        (isBound(a)
          ? '<div><dt>Daily wage</dt><dd class="unknown">the world does not ' +
              'report a wage</dd></div>'
          : '<div><dt>Daily wage</dt><dd>+' + a.wage + '</dd></div>' +
            '<div><dt>Daily upkeep</dt><dd>−' + a.upkeep + '</dd></div>' +
            '<div><dt>Head tax</dt><dd>−' + (a.br === 'minor' ? 0 : HEAD_TAX_PER_DAY) + '</dd></div>' +
            '<div><dt>Net per day</dt><dd class="' + (net >= 0 ? 'pos' : 'neg') + '">' +
              (net >= 0 ? '+' : '') + net + '</dd></div>') +
      '</dl>' +
      // Someone born mid-run has no balance before they existed; "day 0 → 4"
      // over a four-day-old's chart is the same category of nonsense as
      // billing them for thinking they never did.
      '<div class="section-lbl">Balance, day ' +
        (a.bornDay !== undefined ? a.bornDay + ' (born) → ' : '0 → ') + day + '</div>' +
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

  // The world's identity wins whenever it has one - see liveName(). When it
  // disagrees with the built-in roster the run is against a SUBSET genesis,
  // which renumbers the town, and that fact is stated rather than hidden:
  // the reader needs to know the biography below is the model's, not this
  // run's, while the skills and money are this run's.
  const who = LIVE.identity && LIVE.identity[a.id];
  const shownName = liveName(a.id, a.n);
  const shownOcc = liveOcc(a.id, null);
  const modelled = !!(who && who.name && who.name !== a.n);
  el.innerHTML =
    '<div class="insp-head">' +
      '<div class="insp-avatar" style="background:' + colour + '">' + emoji + '</div>' +
      '<div><p class="agent-name">' + shownName + '</p>' +
      '<p class="agent-sub">' + a.id + ' · ' +
      (who && who.age != null ? Math.round(who.age) : a.age) +
      (liveSex(a.id, a.sex) === 'm' ? 'M' : 'F') +
      ' · ' + (shownOcc ? OCC_LABEL(shownOcc) : OCC_LABEL(a.occ)) + '</p>' +
      (modelled
      ? '<p class="agent-sub2 modelled">live from the world · the roster on ' +
        'this page calls ' + a.id + ' “' + a.n + '”, so the biography below ' +
        'is the town model, not this run</p>'
      : (a.rn && a.rn !== a.n
        ? '<p class="agent-sub2">genesis record: ' + a.rn + '</p>'
        : (isBound(a)
          // A bound resident's workplace is whatever the world says, and
          // this file does not know it. The day-zone table is a guess from
          // the occupation - it put a 71-year-old artisan on the POLICE
          // STATION desk - so for a real agent it says where they are
          // instead, which is a fact.
          ? '<p class="agent-sub2">live from the world · at ' +
            esc(liveWhere(a.id) || 'somewhere in town') + '</p>'
          : '<p class="agent-sub2">born in Kasukabe · staffs the ' +
            (ZONES[a.dayZone] ? ZONES[a.dayZone].label : 'town') + '</p>'))) +
    '</div></div>' +
    doingBlock(a.id) +
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
/* Part E.1: keep an open Skills tab current as the run advances.
 *
 * Called from part-e.js each time a live poll lands. Re-renders only when the
 * Skills tab is the one showing, so an operator reading somebody's estate is
 * never yanked back a tab by a tick arriving. */
window.refreshOpenSkillsTab = function () {
  if (inspectorTab !== 'skills' || !selectedId) return;
  const panel = document.getElementById('profilePanel');
  if (!panel || panel.hidden) return;
  renderInspector(AGENT_BY_ID[selectedId], state.hourFrac);
};

function selectAgent(id, opts) {
  if (!AGENT_BY_ID[id]) return;
  selectedId = id;
  // Part E.5: "clicking opens the resident file already scrolled to that
  // skill". Somebody carrying the mid-workflow mark was clicked BECAUSE of
  // the mark, so the file opens on the document they are walking; everyone
  // else still opens on the profile.
  inspectorTab = (window.PartE && PartE.isFollowing && PartE.isFollowing(id))
    ? 'skills' : 'profile';
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
      (h.alive ? esc(activityFor(a, p.zone || 'housing', state.hourFrac % 24)) : 'No longer in the town') +
      '<em id="mapCardWhere">' + (ZONES[p.zone] ? ZONES[p.zone].label : '') + '</em></div>' +
    '<div class="mc-rows">' +
      '<span>👪 ' + familyLine(a) + '</span>' +
      '<span>🏠 ' + (a._home ? (a._home.homeLabel || 'dwelling') + ' · ' + a.hh : a.hh) +
        ' · 🧬 ' + (a.rel || []).length + ' relatives</span>' +
      '<span>🪙 <b id="mapCardBal">' + balanceNow(a, state.hourFrac).toLocaleString() + '</b> sim' +
        ' · 💰 <b id="mapCardSpend">$' + costSpent(a, state.hourFrac).toFixed(3) + '</b>' +
        ' of $' + (a.usd / 100).toFixed(2) + '</span>' +
      '<span>💼 ' + OCC_LABEL(a.occ) + ' · works at ' +
        (ZONES[a.dayZone] ? ZONES[a.dayZone].label : '—') +
        (a.shift ? ' · ' + a.shift + ' shift' : '') + '</span>' +
      // UI-04. The inspector's Skills section was already fixed to read the
      // world; this card is the same two facts on the map, and was not.
      // "Policing · unlocks day 3" is an unlock schedule this file invented
      // at build time, and over a real agent it reads as a statement about
      // the run.
      (isBound(a)
        ? '<span>🧠 ' + liveSkillLine(a.id) + '</span>'
        : '<span>🧠 ' + (a.skillBase || 'General Ability') +
          (state.hourFrac >= a.skillUnlockDay * 24
            ? ' · unlocked <b>' + a.skillUnlocked + '</b>'
            : ' · ' + a.skillUnlocked + ' unlocks day ' + a.skillUnlockDay) + '</span>') +
      // The wage/upkeep/tax line is this file's economy, not the town's. A
      // bound resident has a real balance and no modelled payslip behind
      // it, so the card states the balance and stops there rather than
      // inventing the three numbers that would have to add up to it.
      (isBound(a)
        ? '<span>💸 ' + balanceNow(a, state.hourFrac).toLocaleString() +
          ' sim held</span>'
        : '<span>💸 wage +' + a.wage + ' · upkeep −' + a.upkeep +
          ' · tax −' + (bracketAt(a, state.hourFrac) === 'minor' ? 0 : HEAD_TAX_PER_DAY) +
          ' / day</span>') +
    '</div>' +
    // The two lines that make this a person rather than a row: what they are
    // like, and the thing they will not do. Both come from the generated
    // persona, not from the occupation.
    (a.trait ? '<div class="mc-trait">“' + a.trait + '” · ' + (a.habit || '') + '</div>' : '') +
    (a.tension ? '<div class="mc-quote">' + a.tension + '</div>' : '') +
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
  const cv = document.getElementById('village');
  if (!el || el.hidden || !mapCardId || !cv) return;
  const p = liveAgentPos[mapCardId];
  if (!p) return;
  // village2 draws into a fixed 2013x1089 frame that CSS scales down, and the
  // v2 viewport centres that canvas - so a resident's frame coordinate has to
  // be scaled and offset before it means anything in the card's CSS pixels.
  const s = worldToScreen(p.tx, p.ty);
  const k = cv.clientWidth / (cv.width || 1);
  const px = cv.offsetLeft + s.sx * k;
  const py = cv.offsetTop + s.sy * k;
  const host = el.offsetParent || cv.parentElement;
  const w = host.clientWidth, h = host.clientHeight;
  const cw = el.offsetWidth || 236, ch = el.offsetHeight || 190;
  let x = px - cw / 2;
  let y = py - ch - 26;                       // above their head
  let below = false;
  if (y < 6) { y = py + 22; below = true; }   // not enough room above
  x = Math.max(6, Math.min(w - cw - 6, x));
  y = Math.max(6, Math.min(h - ch - 6, y));
  el.style.left = x + 'px';
  el.style.top = y + 'px';
  el.classList.toggle('below', below);
  // The pointer tracks the resident even after the card is clamped sideways.
  const tip = Math.max(14, Math.min(cw - 14, px - x));
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
      '<p class="char-bio">' + esc(a.bio) + '</p>' +
      '<div class="char-skills">' +
        // UI-04: the world's practised skills for a bound resident. The
        // 🔒 chip is a countdown to an unlock date this file chose with
        // `seeded01(a.id, 'unlockday')`, and on a live card it reads as a
        // scheduled fact about a real agent.
        (isBound(a)
          ? liveSkillChips(a.id)
          : '<span class="char-skill">⚙️ ' + a.skillBase + '</span>' +
            '<span class="char-skill locked" data-skill="' + a.id + '">🔒 day ' +
              a.skillUnlockDay + '</span>') +
        '<span class="char-skill mem">🧠 ' + (a.mem || []).length + ' memories</span>' +
      '</div>' +
      // Part E.2: what this resident is following, live from the world.
      // Empty until a poll lands, and "(no skill)" once one has - the
      // absence is a finding, not a blank to be hidden.
      '<div class="char-follow" data-follow="' + a.id + '"></div>' +
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
/* ---- numbers you can open, and numbers that explain themselves -----------
 * A panel full of counts is a panel full of questions: "28 ill" tells you a
 * number and not one name, and "Wills on file 80 of 315" assumes you already
 * know what a will is for. Every stat that stands for a group of people is
 * now a button that opens that group, and every stat that needs a sentence
 * gets one.
 */
const statRosters = {};        // key -> {label, ids, note}
const openStat = {};           // key -> true while its list is showing

/** A stat tile. Pass `ids` to make it open the people behind the number. */
function statTile(value, label, opts) {
  const o = opts || {};
  const key = o.key || null;
  const clickable = key && o.ids && o.ids.length;
  if (clickable) statRosters[key] = { label: o.label || label, ids: o.ids, note: o.note };
  return '<div class="score-stat ' + (o.cls || '') + (clickable ? ' openable' : '') + '"' +
    (clickable ? ' data-stat="' + key + '" role="button" tabindex="0"' +
                 ' title="Show the ' + o.ids.length + ' people behind this number"' : '') +
    (o.help ? ' data-help="1"' : '') + '>' +
    '<span class="sv">' + value + '</span>' +
    '<span class="sk">' + label + (clickable ? '<span class="stat-caret">▸</span>' : '') + '</span>' +
    (o.help ? '<span class="stat-help">' + o.help + '</span>' : '') +
    '</div>';
}

/** The list a stat opens, rendered under its grid. */
function statDrillHtml() {
  const keys = Object.keys(openStat).filter(k => openStat[k] && statRosters[k]);
  if (!keys.length) return '';
  return keys.map(statDrillFor).join('');
}

/** What this resident's day is FOR - a job, a school, or a guardian. Printing
 *  ZONES[a.dayZone] flatly gave a newborn "Workplace: TOWN SCHOOL", because
 *  dayZone is where they will eventually go, not where a four-week-old is. */
function dayPlaceLineFor(a, hourFrac) {
  const care = careOf(a, hourFrac);
  if (care) {
    const g = care.guardian;
    return { label: care.carried ? 'Looked after by' : 'With',
             value: g ? g.n : 'nobody living — an orphan at home',
             id: g ? g.id : null };
  }
  if (bracketAt(a, hourFrac) === 'minor') {
    return { label: 'School', value: ZONES.school ? ZONES.school.label : 'Town School' };
  }
  if (bracketAt(a, hourFrac) === 'elderly') {
    return { label: 'Spends the day', value: ZONES[a.dayZone] ? ZONES[a.dayZone].label : '—' };
  }
  // A bound resident's workplace is the world's business to state, and it
  // does not. The day-zone table is a guess from the occupation, and it put
  // a 71-year-old artisan on the POLICE STATION desk on a live card.
  if (isBound(a)) {
    return { label: 'Currently at', value: liveWhere(a.id) || '—' };
  }
  return { label: 'Workplace', value: ZONES[a.dayZone] ? ZONES[a.dayZone].label : '—' };
}

/** Work capacity, said in a way that is true of the reader. A newborn is not
 *  "100% able to work"; it is not of working age at all. */
function capacityLineFor(a, hourFrac) {
  const br = bracketAt(a, hourFrac);
  if (br === 'minor') {
    return careOf(a, hourFrac) ? 'not of working age' : 'at school, not yet working';
  }
  if (br === 'elderly') return 'retired · ' + (workCapacity(a) * 100).toFixed(0) + '% if needed';
  return 'work capacity ' + (workCapacity(a) * 100).toFixed(0) + '%';
}

/** A short word for what is true of this person right now. */
function statusWordFor(a) {
  const h = healthOf(a);
  // For the dead, the cause IS the useful word - "deceased" against four names
  // in a list headed "Died, and of what" answers nothing.
  if (!h.alive) return h.cause || 'deceased';
  if (h.ailment) return h.ailment === 'terminal' ? 'terminally ill' : h.ailment + ' illness';
  if (isHungry(a)) return hungerDaysOf(a) + 'd hungry';
  return 'well';
}

/** A labelled fact that both explains itself and opens the people behind it.
 *  The government panel is a list of these: "Wills on file 80 of 315" means
 *  nothing without a sentence saying what a will is for, and less without the
 *  eighty names. */
function statFactHtml(label, value, key, help, ids, listLabel) {
  const clickable = ids && ids.length;
  if (clickable) statRosters[key] = { label: listLabel || label, ids };
  return '<div class="gov-fact' + (clickable ? ' openable' : '') + '"' +
    (clickable ? ' data-stat="' + key + '" role="button" tabindex="0"' : '') + '>' +
    '<span class="k">' + label + (clickable ? '<span class="stat-caret">▸</span>' : '') + '</span>' +
    '<span class="v">' + value + '</span>' +
    '<span class="gov-help">' + help + '</span></div>' +
    (openStat[key] ? statDrillFor(key) : '');
}

/** One drilldown list, by key. */
function statDrillFor(k) {
  const r = statRosters[k];
  if (!r) return '';
  const people = r.ids.map(id => AGENT_BY_ID[id]).filter(Boolean);
  return '<div class="stat-drill"><div class="section-lbl">' + r.label +
    ' <span class="pc-count">' + people.length + '</span>' +
    '<button class="drill-close" data-stat-close="' + k + '" type="button" aria-label="Close">×</button></div>' +
    (r.note ? '<div class="score-note">' + r.note + '</div>' : '') +
    (people.length
      ? people.slice(0, 60).map(a =>
          '<div class="score-row" data-id="' + a.id + '">' +
            '<span class="score-name">' + a.n + '</span>' +
            '<span class="score-occ">' + titleOf(a) + ' · ' +
              Math.floor(ageAt(a, state.hourFrac)) + (a.sex === 'm' ? 'M' : 'F') + '</span>' +
            '<span class="score-where">' + whereLineFor(a) + '</span>' +
            '<span class="score-bal">' + statusWordFor(a) + '</span></div>').join('') +
        (people.length > 60 ? '<div class="pc-more">+ ' + (people.length - 60) + ' more</div>' : '')
      : '<div class="pc-empty">Nobody.</div>') +
    '</div>';
}

/** Where this resident is standing, for a list that is about people rather
 *  than places. "30 ill" is more useful when you can see that twenty-one of
 *  them are in the ward and nine are still at home. */
function whereLineFor(a) {
  const p = liveAgentPos[a.id];
  if (!p) return '';
  if (!healthOf(a).alive) return 'buried';
  const z = ZONES[p.zone];
  return (z ? z.label : p.zone || '') + (p.moving ? ' · walking' : '');
}

/** Wire a panel's stat tiles once its HTML is in place. */
function wireStats(el, rerender) {
  el.querySelectorAll('.score-stat.openable, .gov-fact.openable').forEach(tile => {
    const go = () => {
      const k = tile.dataset.stat;
      // One list at a time inside a panel keeps the panel readable.
      Object.keys(openStat).forEach(other => { if (other !== k) openStat[other] = false; });
      openStat[k] = !openStat[k];
      rerender();
    };
    tile.addEventListener('click', go);
    tile.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
    });
  });
  el.querySelectorAll('[data-stat-close]').forEach(b =>
    b.addEventListener('click', e => {
      e.stopPropagation(); openStat[b.dataset.statClose] = false; rerender();
    }));
  el.querySelectorAll('.stat-drill .score-row[data-id], .seat-row[data-id]').forEach(row => {
    const open = () => selectAgent(row.dataset.id);
    row.addEventListener('click', open);
    row.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
  });
}

function renderHealth(day) {
  const el = document.getElementById('healthBody');
  if (!el) return;
  const alive = AGENTS.filter(isAlive);
  const byStatus = {};
  alive.forEach(a => { const st = healthOf(a).ailment || 'healthy'; byStatus[st] = (byStatus[st] || 0) + 1; });
  const dead = AGENTS.length - alive.length;
  const hos = hospitalOpen(day);

  // Retired: this top-bar figure is now owned by vitals.js, which reads the
  // world. The client-side value came from the removed model and disagreed
  // with the ledger - treasury showed -4,737 while the ledger said +220,
  // and town wealth showed NaN once the cast changed under it. Two writers
  // racing, and the wrong one often won.
  // const hudAlive = document.getElementById('hudAlive');
  // if (hudAlive) hudAlive.textContent = alive.length;
  // const hudDead = document.getElementById('hudDead');
  // if (hudDead) hudDead.textContent = dead;

  // Each count opens the people it stands for. "28 ill" is a number; the
  // twenty-eight names are the thing you actually wanted.
  const ids = f => AGENTS.filter(f).map(a => a.id);
  const illKinds = ['short', 'medium', 'long'];
  const deceased = AGENTS.filter(a => !healthOf(a).alive);
  el.innerHTML =
    '<div class="score-grid">' +
      statTile(alive.length, 'alive', { cls: 'good', key: 'h-alive',
        ids: ids(isAlive), label: 'Living residents' }) +
      statTile(dead, 'deceased', { cls: dead ? 'warn' : '', key: 'h-dead',
        ids: deceased.map(a => a.id), label: 'Died, and of what',
        note: 'Cause is shown against each name.' }) +
      statTile(byStatus.healthy || 0, 'healthy', { key: 'h-well',
        ids: ids(a => isAlive(a) && !healthOf(a).ailment), label: 'Well today' }) +
      statTile((byStatus.short || 0) + (byStatus.medium || 0) + (byStatus.long || 0), 'ill',
        { key: 'h-ill', label: 'Ill right now',
          ids: ids(a => isAlive(a) && illKinds.indexOf(healthOf(a).ailment) >= 0),
          note: 'Short illness passes in a day; medium takes 3–7; long takes 20. ' +
                'How much work they can do falls with the severity.' }) +
      statTile(byStatus.terminal || 0, 'terminally ill',
        { cls: (byStatus.terminal || 0) ? 'crit' : '', key: 'h-term',
          ids: ids(a => isAlive(a) && healthOf(a).ailment === 'terminal'),
          label: 'Terminally ill',
          note: 'A terminal illness has a set day it ends in death. Nothing the town has treats it.' }) +
      statTile(healthSim.recoveries, 'recoveries', { key: 'h-rec',
        ids: ids(a => isAlive(a) && !healthOf(a).ailment && healthOf(a).everIll),
        label: 'Recovered at least once',
        help: 'Illnesses shaken off since the run began. One person can recover ' +
              'many times, so this counts more than there are people.',
        note: 'The number above counts recoveries; this list is the people, each ' +
              'shown once.' }) +
    '</div>' +
    statDrillHtml() +
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
  wireStats(el, () => renderHealth(day));
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
  paint(el,
    '<div class="work-head"><span><b>' + working + '</b> at work</span>' +
    '<span><b>' + (capable ? (capacity / capable * 100).toFixed(0) : 0) + '%</b> average work capacity</span></div>' +
    rows.map(([act, n]) =>
      '<div class="occ-row"><span class="occ-name">' + esc(act) + '</span>' +
      '<span class="occ-bar"><span style="width:' + (n / max * 100).toFixed(0) + '%"></span></span>' +
      '<span class="occ-n">' + n + '</span></div>').join(''));
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
// One sim-hour per this many real seconds at 1x. At the old value of 5 a
// whole day went by in two minutes, which is why residents appeared to
// sprint everywhere: the clock was fast, not the legs. 1x is now an
// unhurried walk, and the speed buttons are what make it quick.
// One sim-hour per this many real seconds at 1x. Raised from 18: at 18 the
// clock still outran a walking pace, so 1x looked brisk rather than ordinary.
// 1x is now an unhurried walk and is the default; 5x and 10x are there for
// when you want to get through a day, not for how people normally move.
// One game-hour per minute of real time at 1x. This is the walking pace
// control: every scripted journey is interpolated from `hourFrac`, so the
// clock rate IS how fast people move on screen. At 30 the town hurried -
// residents crossed a district faster than the eye follows them, which reads
// as sliding rather than walking. 60 gives an unhurried walk at 1x and keeps
// 5x/10x for getting through a day.
const SEC_PER_HOUR_AT_1X = 60;
let lastTs = null;
let _lastRenderedDay = -1;
let _lastBirthCount = 0;
let _lastPanelRender = 0;

/** The village clock, to the second, in twelve-hour time.
 *
 *  Seconds are not decoration: at 1x an hour of village time takes thirty real
 *  seconds, so the minute figure alone changes in visible jumps and the town
 *  looks like it is stepping rather than running. The seconds hand is what
 *  shows the clock is live - and it is the same `state.hourFrac` everything
 *  else in the simulation is driven from, not a separate wall clock. */
function fmtClock(hourFrac) {
  const total = ((hourFrac % 24) + 24) % 24;
  const h24 = Math.floor(total);
  const mins = (total - h24) * 60;
  const m = Math.floor(mins);
  const sec = Math.floor((mins - m) * 60);
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return {
    hhmmss: String(h12).padStart(2, '0') + ':' + String(m).padStart(2, '0') +
            ':' + String(sec).padStart(2, '0'),
    meridiem: h24 < 12 ? 'AM' : 'PM',
    h24: String(h24).padStart(2, '0') + ':' + String(m).padStart(2, '0'),
  };
}

function tick(ts) {
  if (lastTs === null) lastTs = ts;
  const dt = (ts - lastTs) / 1000;
  lastTs = ts;
  // UI-17: two clocks, and the wrong one was on the wall.
  //
  // `state.hourFrac` is the browser's own clock - 60 wall-seconds per game
  // hour, 10x on demand - and every "Day N" on this page came from it: the
  // header, "died day N", "built day N", "unlocks day 3". An operator
  // reading "Day 3" over a run that never crossed tick 144 is being told
  // something untrue by the instrument they are using to judge the run.
  //
  // When a run is bound the world's tick is the clock. 144 ticks a day, ten
  // game-minutes each, from `world/core/clock.py`. The scripted schedule
  // still needs an hourFrac to interpolate unbound residents' walks, so
  // this sets it rather than replacing it - one clock, sourced from the
  // world whenever the world is there to ask.
  const boundToWorld = LIVE.enabled && LIVE.connected && Number.isFinite(LIVE.tick);
  if (boundToWorld) {
    state.hourFrac = LIVE.tick / 6;          // 6 ticks per game-hour
  } else if (state.playing) {
    state.hourFrac += (dt / SEC_PER_HOUR_AT_1X) * state.speed;
  }

  const hour = Math.floor(state.hourFrac) % 24;
  // Day 0 is day 0. The world counts from zero (`timestamp.game_day`) and
  // every run report and trace row does too; the viewer's +1 meant the page
  // and the report disagreed by one about which day a thing happened on.
  const day = boundToWorld
    ? Math.floor(LIVE.tick / 144)
    : Math.floor(state.hourFrac / 24) + 1;
  const t = fmtClock(state.hourFrac);
  const cl = document.getElementById('clockLabel'); if (cl) cl.textContent = t.hhmmss;
  const mer = document.getElementById('clockMeridiem'); if (mer) mer.textContent = t.meridiem;
  const dl = document.getElementById('dayLabel'); if (dl) dl.textContent = 'Day ' + day;
  const pp = document.getElementById('phasePill'); if (pp) pp.textContent = PHASE_LABEL[phaseAt(hour)];

  advanceHealthTo(day - 1);
  updatePositions(state.hourFrac);
  tickConversations(state.hourFrac);

  // Hand the cast to the renderer. It draws and depth-sorts; this file only
  // decides who is where and what they are doing.
  // ONLY REAL AGENTS ARE DRAWN.
  //
  // agentPosition() still knows where a resident WOULD be on the town's
  // built-in schedule, and that schedule is what the panels below are
  // still computed from. But a figure standing on the map is a claim that
  // somebody is standing there, and the only source that can support that
  // claim is the engine. An unbound resident is therefore not drawn at
  // all, rather than drawn from the model and indistinguishable from one
  // that is real.
  //
  // The consequence is deliberate and visible: with no world server bound,
  // the town is empty. That is the honest picture of a run that is not
  // running - see the empty-state notice in renderLiveBadge's tooltip.
  const bound = (LIVE.enabled && LIVE.connected) ? LIVE.zone : null;
  V.setAgents(AGENTS.filter(a => isAlive(a) && bound && bound[a.id]).map(a => {
    const p = liveAgentPos[a.id];
    if (a._bob === undefined) a._bob = seeded01(a.id, 'bob') * 6.28;
    return {
      id: a.id, x: p.tx, y: p.ty,
      // The whole costume, not just the shirt. OCC_OUTFIT has dressed every
      // trade since the cast was built - legs, coat and hat were being worked
      // out and then thrown away here, which is why a doctor and a miner
      // looked like the same person in a different colour.
      shirt: a.shirt, skin: a.skin || '#E8BC93', hair: a.hair,
      legs: (a.outfit && a.outfit.legs) || '#4A4238',
      coat: a.outfit && a.outfit.coat,
      hat: a.outfit && a.outfit.hat,
      tool: WORK_TOOL[a.occ],
      // The Prime Minister and the four ministers wear the sash of office, so
      // the people who run the town are findable on the map rather than being
      // five clerks among fourteen.
      sash: seatRoleOf(a) === 'Prime Minister' ? '#B8860B'
          : seatRoleOf(a) === 'Minister' ? '#7B4B96' : null,
      sashRank: seatRoleOf(a) === 'Prime Minister' ? 'pm' : 'minister',
      // Both of these are read off THIS FILE's model of the day, and for a
      // bound resident that model is fiction. `lying` fired on zone alone,
      // so an agent the world had walking to Kasukabe Forest was drawn
      // asleep in the street outside a house - the zone said housing and
      // nothing asked the world what they were doing.
      //
      // The world reports no sleep state, so a bound resident is simply
      // never drawn lying down. Standing when we do not know beats asleep
      // when we do not know.
      // At work, according to the WORLD.
      //
      // The unbound model compares the district against a day-zone guessed
      // from the occupation. Gating that off for bound residents was right
      // and left them never working at all - so this asks what they are
      // actually doing instead. The verbs are the ones the world's own
      // activity phrasing produces (see run_genesis's _phrase_activity).
      atWork: bound[a.id] ? isWorkingNow(a.id) && !p.moving
                          : (p.zone === a.dayZone && !p.moving),
      lying: !bound[a.id] && !p.dead && !p.moving &&
             (p.zone === 'housing' || (p.ill && (p.zone === 'hospital' || p.zone === 'pharmacy'))),
      walking: p.moving, talking: !!talkingNow[a.id],
      // Part E.5. One affordance, and deliberately only one: a quiet accent
      // beside anyone currently mid-workflow, so a glance shows how much of
      // the town is executing a written procedure versus improvising.
      // Everything else about skills belongs in the books, not floating
      // over the map.
      following: !!(LIVE.following && LIVE.following[a.id]),
      selected: a.id === selectedId, bob: a._bob,
    };
  }));
  V.setNight(hour >= 20 || hour < 6);

  if (selectedPlace && ts - _lastPanelRender > 400) renderPlaceCard(selectedPlace);
  if (ts - _lastPanelRender > 400) {
    _lastPanelRender = ts;
    renderOccupancy();
    renderGovernment(state.hourFrac);
    renderWork(state.hourFrac);
  }
  if (day !== _lastRenderedDay) {
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

// ---- controls -----------------------------------------------------------
// Dragging and wheel-zoom live in village2.js; this wires the buttons.
function wireControls() {
  const play = document.getElementById('playBtn');
  if (play) play.addEventListener('click', () => {
    state.playing = !state.playing;
    play.textContent = state.playing ? 'Pause' : 'Play';
    play.setAttribute('aria-pressed', String(state.playing));
  });
  document.querySelectorAll('button.speed').forEach(b =>
    b.addEventListener('click', () => {
      state.speed = Number(b.dataset.speed);
      document.querySelectorAll('button.speed').forEach(x =>
        x.setAttribute('aria-pressed', String(x === b)));
    }));

  const zin = document.getElementById('zoomInBtn');
  if (zin) zin.addEventListener('click', () => { V.zoomBy(1.25); updateZoomLabel(); });
  const zout = document.getElementById('zoomOutBtn');
  if (zout) zout.addEventListener('click', () => { V.zoomBy(0.8); updateZoomLabel(); });
  const rc = document.getElementById('recentreBtn');
  if (rc) rc.addEventListener('click', () => { V.fit(); updateZoomLabel(); });

  const lbl = document.getElementById('labelBtn');
  if (lbl) lbl.addEventListener('click', () => {
    const on = lbl.getAttribute('aria-pressed') !== 'true';
    V.setLabels(on); lbl.setAttribute('aria-pressed', String(on));
  });
  const inside = document.getElementById('insideBtn');
  if (inside) inside.addEventListener('click', () => {
    const on = inside.getAttribute('aria-pressed') !== 'true';
    V.setInteriors(on); inside.setAttribute('aria-pressed', String(on));
    inside.textContent = on ? 'Roofs on' : 'Roofs off';
  });
  // `?roofs=off` starts with the roofs off, so a recording of the run shows
  // residents indoors at work and at home rather than rooftops.
  if (inside && _params && _params.get('roofs') === 'off') inside.click();
  // `?camera=<district>:<zoom>` holds the camera on a named district at that
  // multiple of the fitted zoom - what a recording of the election asks for
  // (`camera=government:2.2`), so residents are people and not pixels. Held
  // for a few seconds because the shell resizes the map on load, and a resize
  // resets the view (see the township-headless notes).
  // `?tour=on` moves the camera instead of holding it. A four-hour recording
  // from one fixed vantage is four hours of the same frame: the town is wide,
  // most of it is not where anything is happening, and a still camera either
  // shows everywhere at a scale where residents are pixels or one district
  // while the rest of the run happens off-screen.
  //
  // So it walks the districts, pausing on each, and every few stops pulls
  // back to the whole town so a viewer keeps their bearings. Deliberately
  // slow - a stop lasts TOUR_DWELL_MS, which is many frames at one frame
  // every four seconds - because a camera that moves faster than the
  // recorder samples produces a slideshow of unrelated places rather than a
  // pan.
  if (_params && _params.get('tour') === 'on' && window.Village && Village.viewZone) {
    const TOUR_DWELL_MS = 20000;
    const stops = ['townsquare', 'marketrow', 'government', 'housing',
                   'park', 'docks', 'library', 'housingSouth'];
    // Zoom varies per stop rather than staying put, so the recording shows
    // both the street and the town it sits in.
    const zooms = [2.4, 1.8, 2.6, 2.0, 1.6, 2.2];
    let i = 0;
    const step = () => {
      // Every fourth stop, the whole town - the establishing shot that makes
      // the close ones legible.
      if (i % 4 === 3) {
        if (Village.fit) Village.fit();
      } else {
        const zone = stops[i % stops.length];
        const z = zooms[i % zooms.length];
        // viewZone returns false for a district this map does not have, and
        // a cast without docks should not freeze the tour on them.
        if (!Village.viewZone(zone, z) && Village.fit) Village.fit();
      }
      i += 1;
    };
    setTimeout(step, 4000);
    setInterval(step, TOUR_DWELL_MS);
  }
  if (_params && _params.get('camera') && window.Village && Village.viewZone) {
    const [zid, times] = String(_params.get('camera')).split(':');
    const factor = parseFloat(times) || 2;
    const until = performance.now() + 5000;
    const hold = () => {
      if (Village.viewZone(zid, factor) && performance.now() < until) requestAnimationFrame(hold);
    };
    requestAnimationFrame(hold);
  }
  const ex = document.getElementById('exportBtn');
  if (ex) ex.addEventListener('click', () => V.exportPNG());

  const themeBtn = document.getElementById('themeBtn');
  if (themeBtn) themeBtn.addEventListener('click', () => {
    const root = document.documentElement;
    const dark = root.getAttribute('data-theme') === 'dark';
    root.setAttribute('data-theme', dark ? 'light' : 'dark');
    themeBtn.textContent = dark ? 'Night' : 'Day';
  });

  // The fullscreen toggle is gone: the shell already gives the map the whole
  // window, so the button offered to do what had already been done.

  window.addEventListener('keydown', ev => {
    if (ev.key === 'Escape') { closeModal(); hideMapCard(); }
  });
}

/** Zoom shown against "the whole town fits", so 100% means exactly that. */
function updateZoomLabel() {
  const el = document.getElementById('zoomLevel');
  if (el) el.textContent = Math.round((V.zoom / V.fitZoom()) * 100) + '%';
}

// ---- boot ---------------------------------------------------------------
assignHomes();
assignShifts();

V.mount(document.getElementById('village'), {
  onPick(hit) {
    if (hit.agent && hit.agent.id) {
      hidePlaceCard();
      selectAgent(hit.agent.id, { reveal: false });
    } else if (hit.building) {
      hideMapCard();
      selectPlace(hit.building);
    } else {
      hideMapCard();
      hidePlaceCard();
    }
  }
});

wireControls();
wireModal();
wireSearch();
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
updateZoomLabel();

if (_params && _params.get('open') && AGENT_BY_ID[_params.get('open')]) {
  const target = AGENT_BY_ID[_params.get('open')];
  selectedId = target.id;
  renderInspector(target, state.hourFrac);
  showMapCard(target);
}

requestAnimationFrame(tick);
wireRunMode();
renderLiveBadge();
pollLive();
setInterval(pollLive, LIVE.pollMs);
setInterval(refreshRosterLiveValues, 1000);
setInterval(() => renderScoreboard(state.hourFrac), 1200);
renderFood();
setInterval(renderFood, 1500);
refreshShopSigns();
setInterval(refreshShopSigns, 1000);
