const TILE_W = 56;
const TILE_H = 28;
const GRID_W = 208;
const GRID_H = 166;
const TILE = TILE_W;

function isoX(tx, ty) { return (tx - ty) * (TILE_W / 2); }
function isoY(tx, ty) { return (tx + ty) * (TILE_H / 2); }
function depth(tx, ty) { return tx + ty; }

const PAL = {
  grassA: '#7DC95F', grassB: '#74C156', grassC: '#88D268', grassD: '#6BBA50',
  water: '#5BB4D8', waterDeep: '#3E96BE', waterFoam: '#A5DCF0',
  path: '#F0E4A8', pathEdge: '#E4D693',
  road: '#D9CBAE', roadEdge: '#C4B394',
  soil: '#C08A52', soilDark: '#A6743F',
  rock: '#9AA1A8', rockDark: '#7C838A',
  wallLine: '#8C8577',
};

const ROOM_STYLE = {
  house:       ['#D9A96A', '#CE9C5C', '#F6EFDF', '#B5603F'],
  hospital:    ['#E3EFF2', '#D6E6EA', '#FFFFFF', '#D64545'],
  police:      ['#DFE3EC', '#D2D8E4', '#F2F4FA', '#2F4B7C'],
  fire:        ['#EFDCD4', '#E4CFC5', '#FBEEE8', '#C6362A'],
  school:      ['#E4EEDA', '#D8E5CB', '#F4F8EE', '#6E9E4A'],
  library:     ['#E8E0D0', '#DCD2BF', '#F5EFE2', '#7C6248'],
  bank:        ['#EDEADA', '#E1DDC9', '#F8F5E8', '#9B8A45'],
  bakery:      ['#F2E2C6', '#E7D4B4', '#FBF2E2', '#C98A4B'],
  restaurant:  ['#EEDCE0', '#E2CCD2', '#FAEFF2', '#A4485C'],
  cafe:        ['#EDDCC4', '#E1CFB2', '#F9F0E2', '#B5643C'],
  grocery:     ['#F2E6C8', '#E6D8B6', '#FBF4E4', '#D9552F'],
  pharmacy:    ['#DCEFE9', '#CDE4DC', '#EEF9F5', '#3FA98C'],
  government:  ['#F0E9D2', '#E4DCC1', '#FBF6E6', '#C9A227'],
  temple:      ['#EDE4DA', '#E0D5C8', '#F8F2EA', '#9E3B32'],
  college:     ['#E6E0EE', '#DAD2E4', '#F4F0FA', '#6A5A9E'],
  office:      ['#DFE6EA', '#D2DBE0', '#F0F4F6', '#597B92'],
  market:      ['#F2E0C4', '#E6D3B2', '#FBF1E0', '#C8613F'],
  inn:         ['#E9DCC4', '#DDCFB4', '#F7F0E2', '#8C6239'],
  construct:   ['#E4DCC4', '#D8CFB4', '#F4EEDE', '#D9992F'],
};

const ZONES = {
  housing:      { label: 'Housing District',   icon: '🏘️', x: 6,   y: 6,   w: 62, h: 42, style: 'house',      rooms: 'terrace' },
  park:         { label: 'Town Park',          icon: '🌳', x: 74,  y: 8,   w: 44, h: 36, style: null,         rooms: 'open' },
  housingEast:  { label: 'East Terraces',      icon: '🏡', x: 124, y: 6,   w: 58, h: 42, style: 'house',      rooms: 'terrace' },
  orchard:      { label: 'Orchard',            icon: '🍏', x: 188, y: 8,   w: 16, h: 36, style: null,         rooms: 'grove' },

  school:       { label: 'Town School',        icon: '🏫', x: 8,   y: 52,  w: 22, h: 18, style: 'school',     rooms: 'hall' },
  hospital:     { label: 'Hospital',           icon: '🏥', x: 36,  y: 52,  w: 26, h: 20, style: 'hospital',   rooms: 'ward' },
  library:      { label: 'Library',            icon: '📚', x: 68,  y: 52,  w: 20, h: 16, style: 'library',    rooms: 'library' },
  government:   { label: 'Government Complex', icon: '🏛️', x: 94,  y: 50,  w: 32, h: 24, style: 'government', rooms: 'hall' },
  bakery:       { label: 'Bakery',             icon: '🥖', x: 132, y: 52,  w: 18, h: 15, style: 'bakery',     rooms: 'shop' },
  cafe:         { label: 'Kasukabe Cafe',      icon: '☕', x: 156, y: 52,  w: 20, h: 16, style: 'cafe',       rooms: 'kitchenset' },
  docks:        { label: 'River Docks',        icon: '⚓', x: 188, y: 52,  w: 16, h: 66, style: null,         rooms: 'quay' },

  college:      { label: 'Oak Hill College',   icon: '🎓', x: 8,   y: 78,  w: 34, h: 24, style: 'college',    rooms: 'hall' },
  marketrow:    { label: 'Market Row',         icon: '🏪', x: 48,  y: 78,  w: 22, h: 22, style: 'market',     rooms: 'shop' },
  townsquare:   { label: 'Town Square',        icon: '⛲', x: 76,  y: 78,  w: 30, h: 24, style: null,         rooms: 'plaza' },
  grocery:      { label: 'Kasukabe Grocery',   icon: '🛒', x: 112, y: 80,  w: 20, h: 16, style: 'grocery',    rooms: 'shop' },
  pharmacy:     { label: 'Kasukabe Pharmacy',  icon: '💊', x: 138, y: 80,  w: 20, h: 16, style: 'pharmacy',   rooms: 'shop' },
  restaurant:   { label: 'Restaurant',         icon: '🍽️', x: 164, y: 80,  w: 20, h: 16, style: 'restaurant', rooms: 'kitchenset' },

  housingSouth: { label: 'South Cottages',     icon: '🏚️', x: 6,   y: 108, w: 52, h: 34, style: 'house',      rooms: 'terrace' },
  temple:       { label: 'Kasukabe Temple',    icon: '⛩️', x: 58,  y: 126, w: 22, h: 18, style: 'temple',     rooms: 'hall' },
  police:       { label: 'Police Station',     icon: '🚓', x: 64,  y: 108, w: 22, h: 18, style: 'police',     rooms: 'station' },
  fire:         { label: 'Fire Station',       icon: '🚒', x: 92,  y: 108, w: 22, h: 18, style: 'fire',       rooms: 'firehouse' },
  tradeoffices: { label: 'Trade Offices',      icon: '💼', x: 120, y: 106, w: 30, h: 22, style: 'office',     rooms: 'cubicle' },
  bank:         { label: 'Town Bank',          icon: '🏦', x: 156, y: 108, w: 20, h: 16, style: 'bank',       rooms: 'bankrooms' },
  inn:          { label: 'The Inn',            icon: '🛏️', x: 182, y: 126, w: 22, h: 18, style: 'inn',        rooms: 'ward' },

  farmland:     { label: 'Farmland',           icon: '🌾', x: 6,   y: 146, w: 38, h: 18, style: null,         rooms: 'field' },
  pasture:      { label: 'Pasture',            icon: '🐄', x: 50,  y: 146, w: 30, h: 18, style: null,         rooms: 'graze' },
  oreoutcrop:   { label: 'Ore Outcrop',        icon: '⛏️', x: 86,  y: 146, w: 16, h: 18, style: null,         rooms: 'quarry' },
  construction: { label: 'Construction Yard',  icon: '🏗️', x: 108, y: 144, w: 28, h: 20, style: 'construct',  rooms: 'yard' },
  forest:       { label: 'Forest',             icon: '🌲', x: 142, y: 148, w: 38, h: 16, style: null,         rooms: 'wood' },
};

(function assertNoZoneOverlap() {
  const ids = Object.keys(ZONES);
  for (let i = 0; i < ids.length; i++)
    for (let j = i + 1; j < ids.length; j++) {
      const a = ZONES[ids[i]], b = ZONES[ids[j]];
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > 0 && oy > 0) console.warn(`zone overlap: ${ids[i]} x ${ids[j]} (${ox}x${oy} tiles)`);
      if (a.x + a.w > GRID_W || a.y + a.h > GRID_H) console.warn(`zone off grid: ${ids[i]}`);
    }
})();
const ZONE_ORDER = Object.keys(ZONES);

const AVENUE_PATHS = [
  [4.2, [[2, 40], [34, 44], [72, 41], [110, 47], [150, 43], [206, 49]]],
  [4.2, [[2, 96], [40, 101], [84, 95], [128, 100], [168, 94], [206, 99]]],
  [3.8, [[40, 2], [44, 38], [39, 78], [46, 120], [41, 164]]],
  [3.8, [[124, 2], [119, 40], [126, 84], [120, 128], [127, 164]]],
];
const STREET_PATHS = [
  [2.6, [[2, 20], [36, 23], [76, 19], [118, 24], [160, 20], [200, 25]]],
  [2.6, [[2, 68], [38, 72], [78, 67], [120, 72], [162, 67], [200, 71]]],
  [2.6, [[2, 128], [40, 132], [82, 127], [124, 133], [166, 128], [200, 132]]],
  [2.6, [[2, 152], [44, 156], [88, 151], [130, 157], [172, 152], [200, 156]]],
  [2.4, [[70, 2], [74, 34], [69, 70], [75, 108], [70, 150]]],
  [2.4, [[168, 6], [163, 44], [170, 88], [164, 130], [170, 162]]],
  [2.4, [[16, 6], [20, 46], [15, 90], [21, 132], [16, 162]]],
  [2.4, [[96, 30], [101, 66], [95, 104], [100, 140]]],
];
const FOOT_PATHS = [
  [1.5, [[6, 56], [40, 60], [78, 55], [116, 61], [156, 56], [198, 60]]],
  [1.5, [[6, 84], [36, 88], [72, 83], [112, 89], [152, 84], [196, 88]]],
  [1.5, [[6, 114], [42, 118], [84, 113], [126, 119], [166, 114], [198, 118]]],
  [1.5, [[56, 8], [60, 42], [55, 78], [61, 116], [56, 158]]],
  [1.5, [[146, 8], [141, 46], [148, 90], [142, 132], [148, 160]]],
  [1.5, [[110, 12], [115, 50], [109, 92], [114, 134]]],
];
const RIVER_PATHS = [
  [7.5, [[196, 2], [201, 26], [194, 52], [200, 78], [193, 104], [199, 130], [192, 158], [198, 165]]],
  [3.4, [[60, 150], [78, 132], [92, 118], [108, 112], [126, 104], [146, 92], [168, 80], [186, 68], [196, 58]]],
];

const roadTiles = new Set();
const streetTiles = new Set();
const footTiles = new Set();
const waterTiles = new Set();
const bankTiles = new Set();
const key = (x, y) => x + ',' + y;

function stamp(set, w, pts) {
  const r = w / 2;
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    const len = Math.hypot(x1 - x0, y1 - y0);
    const steps = Math.max(2, Math.ceil(len * 2));
    for (let sIdx = 0; sIdx <= steps; sIdx++) {
      const f = sIdx / steps;
      const cx = x0 + (x1 - x0) * f, cy = y0 + (y1 - y0) * f;
      for (let dy = -Math.ceil(r); dy <= Math.ceil(r); dy++)
        for (let dx = -Math.ceil(r); dx <= Math.ceil(r); dx++) {
          const tx = Math.round(cx) + dx, ty = Math.round(cy) + dy;
          if (tx < 0 || ty < 0 || tx >= GRID_W || ty >= GRID_H) continue;
          const edge = Math.hypot(tx + 0.5 - cx, ty + 0.5 - cy);
          if (edge <= r - 0.35 + nz(tx, ty, 'edge') * 0.7) set.add(key(tx, ty));
        }
    }
  }
}

function buildPaths() {
  [roadTiles, streetTiles, footTiles, waterTiles, bankTiles].forEach(s2 => s2.clear());
  for (const [w, pts] of AVENUE_PATHS) stamp(roadTiles, w, pts);
  for (const [w, pts] of STREET_PATHS) stamp(streetTiles, w, pts);
  for (const [w, pts] of FOOT_PATHS) stamp(footTiles, w, pts);
  for (const [w, pts] of RIVER_PATHS) stamp(waterTiles, w, pts);
  for (const k of waterTiles) {
    const [x, y] = k.split(',').map(Number);
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const k2 = key(x + dx, y + dy);
      if (!waterTiles.has(k2)) bankTiles.add(k2);
    }
  }
  for (const k of roadTiles) if (waterTiles.has(k)) bridgeTiles.add(k);
  for (const k of streetTiles) if (waterTiles.has(k)) bridgeTiles.add(k);
}
const bridgeTiles = new Set();

function isRoad(tx, ty) { const k = key(tx, ty); return roadTiles.has(k) || streetTiles.has(k); }
function isPath(tx, ty) { const k = key(tx, ty); return footTiles.has(k) && !isRoad(tx, ty); }
function isBridge(tx, ty) { return bridgeTiles.has(key(tx, ty)); }
function isRiver(tx, ty) { const k = key(tx, ty); return waterTiles.has(k) && !bridgeTiles.has(k); }
function isBank(tx, ty) { const k = key(tx, ty); return bankTiles.has(k) && !waterTiles.has(k) && !isRoad(tx, ty); }

function inZone(z, tx, ty) { return tx >= z.x && tx < z.x + z.w && ty >= z.y && ty < z.y + z.h; }

function nz(x, y, salt) {
  let h = 2166136261;
  const s = x + ',' + y + ',' + salt;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  h ^= h >>> 16; h = Math.imul(h, 2246822507);
  h ^= h >>> 13; h = Math.imul(h, 3266489909); h ^= h >>> 16;
  return ((h >>> 0) % 100000) / 100000;
}

function isoTile(ctx, tx, ty, fill, edge) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + TILE_W / 2, y + TILE_H / 2);
  ctx.lineTo(x, y + TILE_H);
  ctx.lineTo(x - TILE_W / 2, y + TILE_H / 2);
  ctx.closePath();
  ctx.fillStyle = fill; ctx.fill();
  if (edge) { ctx.strokeStyle = edge; ctx.lineWidth = 1; ctx.stroke(); }
}

function isoRect(ctx, tx, ty, w, d, fill) {
  const a = [isoX(tx, ty), isoY(tx, ty)];
  const b = [isoX(tx + w, ty), isoY(tx + w, ty)];
  const c = [isoX(tx + w, ty + d), isoY(tx + w, ty + d)];
  const e = [isoX(tx, ty + d), isoY(tx, ty + d)];
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
  ctx.lineTo(c[0], c[1]); ctx.lineTo(e[0], e[1]);
  ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
}

function isoBox(ctx, tx, ty, w, d, h, top, left, right) {
  const P = (a, b, lift) => [isoX(a, b), isoY(a, b) - (lift || 0)];
  const A = P(tx, ty), B = P(tx + w, ty), C = P(tx + w, ty + d), D = P(tx, ty + d);
  const At = P(tx, ty, h), Bt = P(tx + w, ty, h), Ct = P(tx + w, ty + d, h), Dt = P(tx, ty + d, h);
  const poly = (pts, fill) => {
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  };
  poly([D, C, Ct, Dt], left);
  poly([C, B, Bt, Ct], right);
  poly([At, Bt, Ct, Dt], top);
}

function shade(col, f) {
  let r, g, b;
  if (col[0] === '#') {
    const n = parseInt(col.slice(1), 16);
    r = (n >> 16) & 255; g = (n >> 8) & 255; b = n & 255;
  } else {
    const m = col.match(/-?\d+/g);
    if (!m || m.length < 3) return col;
    r = +m[0]; g = +m[1]; b = +m[2];
  }
  return `rgb(${Math.min(255, Math.round(r * f))},${Math.min(255, Math.round(g * f))},${Math.min(255, Math.round(b * f))})`;
}
function box3(ctx, tx, ty, w, d, h, col) {
  isoBox(ctx, tx, ty, w, d, h, shade(col, 1.0), shade(col, 0.72), shade(col, 0.86));
}

const plans = [];

function splitRooms(x, y, w, h, minW, minH, depthLeft, out, seed) {
  if (depthLeft <= 0 || (w < minW * 2 + 1 && h < minH * 2 + 1)) {
    out.push({ x, y, w, h });
    return;
  }
  const canV = w >= minW * 2 + 1;
  const canH = h >= minH * 2 + 1;
  const vertical = canV && (!canH || nz(x, y, 'sp' + seed) > 0.5);
  if (vertical) {
    const cut = minW + Math.floor(nz(x, y, 'cv' + seed) * (w - minW * 2 - 1));
    splitRooms(x, y, cut, h, minW, minH, depthLeft - 1, out, seed + 1);
    splitRooms(x + cut + 1, y, w - cut - 1, h, minW, minH, depthLeft - 1, out, seed + 2);
  } else {
    const cut = minH + Math.floor(nz(x, y, 'ch' + seed) * (h - minH * 2 - 1));
    splitRooms(x, y, w, cut, minW, minH, depthLeft - 1, out, seed + 1);
    splitRooms(x, y + cut + 1, w, h - cut - 1, minW, minH, depthLeft - 1, out, seed + 2);
  }
}

const ROOM_PLAN = {
  terrace:    ['bedroom', 'living', 'kitchen', 'bath'],
  ward:       ['ward', 'ward', 'nurses', 'store'],
  hall:       ['classroom', 'classroom', 'reception', 'office'],
  cubicle:    ['office', 'office', 'meeting', 'reception'],
  shop:       ['shopfloor', 'shopfloor', 'checkout', 'store'],
  yard:       ['store', 'workshop', 'office', 'store'],
  station:    ['lockers', 'bay', 'office', 'cells'],
  library:    ['stacks', 'stacks', 'reading', 'reception'],
  bankrooms:  ['tellers', 'vault', 'office', 'reception'],
  firehouse:  ['bay', 'lockers', 'office', 'store'],
  kitchenset: ['kitchen', 'dining', 'dining', 'store'],
};

const BEDDING = ['#E4694E', '#4E82C4', '#59A86B', '#D6A128', '#8E6BC4', '#DA7BA8', '#3FA9A0'];
const SOFA    = ['#C4614E', '#4F7FB5', '#5C9E63', '#9B6BB5', '#C08552', '#3FA9A0'];
const RUGCOL  = ['#D9A6A6', '#A6C3D9', '#BFD9A6', '#D9CFA6', '#C9A6D9'];
const BOOKS   = ['#C4443B', '#3B6BC4', '#3BA05C', '#D19B2A', '#8E4BC4', '#C4553B', '#2E8F8A'];
const GOODS   = ['#E4694E', '#F2C14E', '#59A86B', '#4E82C4', '#DA7BA8', '#E28E3C'];

function furnishRoom(room, kind, seed) {
  const f = [];
  const r = (s) => nz(room.x, room.y, s + seed);
  const put = (type, ox, oy, w, h, extra) => {
    if (ox < 0.3 || oy < 0.3 || ox + w > room.w - 0.3 || oy + h > room.h - 0.3) return false;
    f.push({ type, x: room.x + ox, y: room.y + oy, w, h, ...(extra || {}) });
    return true;
  };
  const pick = (arr, salt) => arr[Math.floor(r(salt) * arr.length) % arr.length];
  const W = room.w, H = room.h;

  switch (kind) {
    case 'bedroom':
      put('bed', 0.4, 0.4, 1.5, 2.2, { col: pick(BEDDING, 'b1') });
      put('nightstand', 2.0, 0.4, 0.8, 0.8);
      if (W > 4.6) put('bed', 2.9, 0.4, 1.5, 2.2, { col: pick(BEDDING, 'b2') });
      put('wardrobe', W - 1.5, 0.4, 1.2, 2.0);
      put('rug', 0.6, H - 2.2, Math.min(3, W - 1.4), 1.5, { col: pick(RUGCOL, 'rg') });
      put('plant', W - 1.3, H - 1.3, 0.8, 0.8);
      break;
    case 'living':
      put('sofa', 0.4, 0.5, 2.6, 1.1, { col: pick(SOFA, 'sf') });
      put('coffee', 1.0, 1.9, 1.5, 0.9);
      put('tv', 0.6, H - 1.2, 2.2, 0.6);
      put('armchair', W - 1.5, 0.6, 1.0, 1.0, { col: pick(SOFA, 'ac') });
      put('bookshelf', W - 1.4, 2.0, 1.1, 2.0, { books: true });
      put('rug', 0.5, 1.7, 2.8, 1.6, { col: pick(RUGCOL, 'rg2') });
      put('plant', W - 1.2, H - 1.2, 0.8, 0.8);
      break;
    case 'kitchen':
      put('counter', 0.4, 0.4, W - 0.8, 0.9);
      put('stove', 0.6, 0.5, 1.1, 0.7);
      put('sink', 2.0, 0.5, 1.0, 0.7);
      put('fridge', W - 1.5, 1.6, 1.1, 1.4);
      put('table', 0.8, H - 2.2, 1.8, 1.2);
      put('chair', 0.6, H - 2.5, 0.6, 0.6);
      put('chair', 2.6, H - 2.5, 0.6, 0.6);
      put('chair', 1.5, H - 1.0, 0.6, 0.6);
      break;
    case 'bath':
      put('bathtub', 0.4, 0.4, 2.0, 1.1);
      put('toilet', W - 1.2, 0.5, 0.8, 1.0);
      put('sink', 0.5, 1.9, 0.9, 0.7);
      put('rug', 1.6, 1.9, 1.2, 0.9, { col: '#BFD9E4' });
      break;
    case 'ward':
      for (let i = 0; i < Math.floor(W / 2.3); i++) {
        put('bed', 0.4 + i * 2.3, 0.4, 1.4, 2.1, { col: '#EDF3F6' });
        put('monitor', 0.4 + i * 2.3, 2.6, 0.7, 0.6);
        put('curtain', 1.9 + i * 2.3, 0.4, 0.15, 2.2);
        if (H > 6) {
          put('bed', 0.4 + i * 2.3, H - 2.5, 1.4, 2.1, { col: '#EDF3F6' });
          put('monitor', 0.4 + i * 2.3, H - 3.2, 0.7, 0.6);
        }
      }
      break;
    case 'nurses':
      put('counter', 0.4, 0.4, W - 0.8, 1.0);
      put('cabinet', 0.5, 1.8, 1.0, 1.4);
      put('chair', 2.0, 1.9, 0.7, 0.7);
      put('plant', W - 1.2, H - 1.2, 0.8, 0.8);
      break;
    case 'classroom':
      put('board', 0.5, 0.35, W - 1.0, 0.45);
      for (let j = 0; j < Math.floor((H - 1.6) / 1.5); j++)
        for (let i = 0; i < Math.floor(W / 2.0); i++) {
          put('desk', 0.4 + i * 2.0, 1.4 + j * 1.5, 1.4, 0.8);
          put('chair', 0.8 + i * 2.0, 2.3 + j * 1.5, 0.6, 0.5);
        }
      break;
    case 'office':
      for (let j = 0; j < Math.floor(H / 2.2); j++)
        for (let i = 0; i < Math.floor(W / 2.2); i++) {
          put('desk', 0.4 + i * 2.2, 0.4 + j * 2.2, 1.5, 0.9);
          put('monitor', 0.9 + i * 2.2, 0.5 + j * 2.2, 0.6, 0.4);
          put('chair', 0.9 + i * 2.2, 1.5 + j * 2.2, 0.7, 0.7);
        }
      put('filing', W - 1.3, H - 1.8, 1.0, 1.5);
      break;
    case 'meeting':
      put('table', 0.6, H / 2 - 0.8, W - 1.2, 1.6);
      for (let i = 0; i < Math.floor(W / 1.3); i++) {
        put('chair', 0.7 + i * 1.3, H / 2 - 1.6, 0.7, 0.7);
        put('chair', 0.7 + i * 1.3, H / 2 + 1.0, 0.7, 0.7);
      }
      put('plant', W - 1.2, 0.5, 0.8, 0.8);
      break;
    case 'reception':
      put('reception', 0.5, 0.4, W - 1.0, 1.0);
      put('sofa', 0.5, 1.9, 2.2, 1.0, { col: pick(SOFA, 'rs') });
      put('coffee', 1.0, 3.1, 1.2, 0.8);
      put('plant', W - 1.2, 1.8, 0.8, 0.8);
      put('rug', 0.6, 3.0, 2.0, 1.2, { col: pick(RUGCOL, 'rr') });
      break;
    case 'shopfloor':
      for (let j = 0; j < Math.floor(H / 1.7); j++)
        put('shelf', 0.4, 0.4 + j * 1.7, W - 0.8, 0.9, { goods: true });
      break;
    case 'checkout':
      put('till', 0.5, 0.4, W - 1.0, 0.9);
      put('chair', 1.0, 1.5, 0.7, 0.7);
      put('display', 0.5, 2.4, W - 1.0, 0.8, { goods: true });
      break;
    case 'store':
      for (let j = 0; j < Math.floor(H / 1.4); j++)
        for (let i = 0; i < Math.floor(W / 1.4); i++)
          if (r('c' + i + j) > 0.35) put('crate', 0.4 + i * 1.4, 0.4 + j * 1.4, 1.0, 1.0);
      break;
    case 'workshop':
      put('bench', 0.4, 0.4, W - 0.8, 0.9);
      put('tools', 0.6, 1.6, 1.4, 0.5);
      put('crate', W - 1.5, 1.6, 1.1, 1.1);
      break;
    case 'lockers':
      for (let i = 0; i < Math.floor(W / 0.9); i++) put('locker', 0.4 + i * 0.9, 0.4, 0.7, 1.4);
      put('bench', 0.6, 2.2, W - 1.2, 0.6);
      break;
    case 'bay':
      put('engine', 0.5, 0.6, Math.min(3.4, W - 1), 2.0);
      put('hosereel', W - 1.3, 0.6, 0.9, 0.9);
      break;
    case 'cells':
      for (let i = 0; i < Math.floor(W / 2.4); i++) {
        put('cell', 0.4 + i * 2.4, 0.4, 2.0, H - 0.8);
        put('bed', 0.7 + i * 2.4, 0.7, 1.2, 1.8, { col: '#A9B4BE' });
      }
      break;
    case 'stacks':
      for (let j = 0; j < Math.floor(H / 1.6); j++)
        put('bookshelf', 0.4, 0.4 + j * 1.6, W - 0.8, 1.0, { books: true });
      break;
    case 'reading':
      put('table', 0.6, 0.8, W - 1.2, 1.4);
      for (let i = 0; i < Math.floor(W / 1.4); i++) {
        put('chair', 0.7 + i * 1.4, 0.1, 0.7, 0.6);
        put('chair', 0.7 + i * 1.4, 2.4, 0.7, 0.6);
      }
      put('plant', W - 1.2, H - 1.2, 0.8, 0.8);
      break;
    case 'tellers':
      put('reception', 0.5, 0.4, W - 1.0, 1.0);
      for (let i = 0; i < Math.floor(W / 1.6); i++) put('till', 0.6 + i * 1.6, 1.7, 1.2, 0.7);
      break;
    case 'vault':
      put('vault', 0.6, 0.6, Math.min(2.6, W - 1.2), Math.min(2.6, H - 1.2));
      put('crate', W - 1.4, H - 1.4, 1.0, 1.0);
      break;
    case 'dining':
      for (let j = 0; j < Math.floor(H / 2.3); j++)
        for (let i = 0; i < Math.floor(W / 2.3); i++) {
          put('table', 0.5 + i * 2.3, 0.5 + j * 2.3, 1.3, 1.3);
          put('chair', 0.3 + i * 2.3, 1.0 + j * 2.3, 0.6, 0.6);
          put('chair', 1.9 + i * 2.3, 1.0 + j * 2.3, 0.6, 0.6);
        }
      break;
    default:
      put('table', W / 2 - 0.9, H / 2 - 0.6, 1.8, 1.2);
  }
  return f;
}

function gridRooms(bx, by, iw, ih, purposes) {
  const n = purposes.length;
  const cols = Math.max(1, Math.round(Math.sqrt(n * (iw / Math.max(1, ih)))));
  const rows = Math.max(1, Math.ceil(n / cols));
  const out = [];
  for (let i = 0; i < n; i++) {
    const c = i % cols, r = Math.floor(i / cols);
    const x = bx + Math.round((iw * c) / cols);
    const y = by + Math.round((ih * r) / rows);
    const w = bx + Math.round((iw * (c + 1)) / cols) - x;
    const h = by + Math.round((ih * (r + 1)) / rows) - y;
    if (w < 2 || h < 2) continue;
    out.push({ x, y, w, h, purpose: purposes[i] });
  }
  return out;
}

function makeHome(zid, k, ox, oy) {
  const bx = ox + 1, by = oy + 1, iw = k.w - 2, ih = k.h - 2;
  let rooms;
  if (k.flats) {
    rooms = [];
    for (let f = 0; f < k.flats; f++) {
      const fx = bx + Math.round((iw * f) / k.flats);
      const fw = bx + Math.round((iw * (f + 1)) / k.flats) - fx;
      rooms.push(...gridRooms(fx, by, fw - 1, ih, k.rooms));
    }
  } else {
    rooms = gridRooms(bx, by, iw, ih, k.rooms);
  }
  return {
    zid, style: 'house', home: true, tier: k.tier, homeLabel: k.label,
    capacity: k.capacity, storeys: k.storeys,
    x: ox, y: oy, w: k.w, h: k.h,
    rooms: rooms.map((r, i) => ({ ...r, furniture: furnishRoom(r, r.purpose, r.x + r.y + i) })),
  };
}

function buildPlans() {
  plans.length = 0;
  for (const zid of ZONE_ORDER) {
    const z = ZONES[zid];
    if (!z.style) continue;

    if (z.rooms === 'terrace') {
      const KINDS = [
        { tier: 1, label: '1 BHK', w: 19, h: 9,  storeys: 6, capacity: 24, flats: 4,
          rooms: ['bedroom', 'living', 'kitchen'] },
        { tier: 2, label: '2 BHK', w: 10, h: 8,  storeys: 1, capacity: 1,
          rooms: ['bedroom', 'bedroom', 'living', 'kitchen', 'bath'] },
        { tier: 3, label: '3 BHK', w: 12, h: 10, storeys: 2, capacity: 1,
          rooms: ['bedroom', 'bedroom', 'bedroom', 'living', 'kitchen', 'bath'] },
        { tier: 4, label: 'Modern house', w: 14, h: 11, storeys: 2, capacity: 1,
          rooms: ['bedroom', 'bedroom', 'bedroom', 'living', 'kitchen', 'bath', 'study', 'living'] },
      ];
      const ORDER = [3, 0, 2, 1, 0];
      let gy = 0, row = 0;
      while (gy + 9 <= z.h) {
        const k = KINDS[ORDER[row % ORDER.length]];
        if (gy + k.h > z.h) { row++; if (row > 40) break; continue; }
        let gx = 0;
        while (gx + k.w <= z.w) {
          plans.push(makeHome(zid, k, z.x + gx, z.y + gy));
          gx += k.w + 1;
        }
        gy += k.h + 1;
        row++;
      }
      continue;
    }

    const iw = Math.max(7, Math.round(z.w * 0.62));
    const ih = Math.max(6, Math.round(z.h * 0.62));
    const bx = z.x + Math.floor((z.w - iw) / 2), by = z.y + 1;
    const cw = Math.floor(iw / 2), ch = Math.floor(ih / 2);
    const cells = [
      { x: bx, y: by, w: cw, h: ch },
      { x: bx + cw, y: by, w: iw - cw, h: ch },
      { x: bx, y: by + ch, w: cw, h: ih - ch },
      { x: bx + cw, y: by + ch, w: iw - cw, h: ih - ch },
    ];
    const purposes = ROOM_PLAN[z.rooms] || ROOM_PLAN.hall;
    const tall = ['government', 'college', 'office', 'hospital', 'bank', 'temple'].includes(z.style);
    plans.push({
      zid, style: z.style, roomsKind: z.rooms, storeys: tall ? 3 : 2,
      x: bx, y: by, w: iw, h: ih,
      rooms: cells.map((c, i) => ({
        ...c, purpose: purposes[i % purposes.length],
        furniture: furnishRoom(c, purposes[i % purposes.length], c.x + c.y + i),
      })),
    });
    const ow = Math.max(4, Math.round(iw * 0.4)), oh = Math.max(4, Math.round(ih * 0.35));
    const spots = [
      { x: z.x + 1, y: by + ih + 2 },
      { x: z.x + z.w - ow - 1, y: by + ih + 2 },
    ];
    for (const sp of spots) {
      if (sp.y + oh > z.y + z.h) continue;
      const room = { x: sp.x + 1, y: sp.y + 1, w: ow - 2, h: oh - 2 };
      plans.push({
        zid, style: z.style, roomsKind: z.rooms, storeys: 1,
        x: sp.x, y: sp.y, w: ow, h: oh,
        rooms: [{ ...room, purpose: 'store', furniture: furnishRoom(room, 'store', sp.x + sp.y) }],
      });
    }
  }
}

const FURN_H = {
  bed: 9, nightstand: 11, wardrobe: 34, sofa: 13, armchair: 14, coffee: 8,
  tv: 14, bookshelf: 32, rug: 0.6, plant: 16, counter: 14, stove: 13,
  sink: 12, fridge: 30, table: 12, chair: 15, bathtub: 10, toilet: 12,
  monitor: 12, curtain: 26, board: 22, desk: 12, filing: 22, reception: 15,
  shelf: 24, display: 14, till: 13, crate: 12, bench: 8, tools: 10,
  locker: 30, engine: 20, hosereel: 10, cell: 34, vault: 26, cabinet: 20, cart: 12,
};
const FURN_COL = {
  nightstand: '#B98A56', wardrobe: '#A9825A', coffee: '#C89B62', tv: '#2E3440',
  bookshelf: '#8C6239', plant: '#4F9E52', counter: '#C89B62', stove: '#4A5560',
  sink: '#DDE6EA', fridge: '#E8EDF0', table: '#C89B62', chair: '#8C6239',
  bathtub: '#FFFFFF', toilet: '#FFFFFF', monitor: '#2E3440', curtain: '#78BECD',
  board: '#2F6B4A', desk: '#C08E58', filing: '#9FB0BE', shelf: '#A9825A',
  display: '#A9825A', till: '#B98A56', crate: '#C79A5F', bench: '#9A6E42',
  tools: '#8FA0AE', locker: '#7A93AC', engine: '#C6362A', hosereel: '#C6362A',
  vault: '#9B8A45', cabinet: '#DDE4E8', cart: '#A97C4E',
};

function drawFurniture(ctx, f, accent) {
  const h = FURN_H[f.type] || 12;
  const col = f.col || FURN_COL[f.type] || accent || '#C08E58';

  switch (f.type) {
    case 'rug':
      isoRect(ctx, f.x, f.y, f.w, f.h, f.col || '#D9A6A6');
      return;
    case 'bed': {
      box3(ctx, f.x, f.y, f.w, f.h, 7, '#F0EADC');
      box3(ctx, f.x, f.y + f.h * 0.35, f.w, f.h * 0.65, 10, col);
      box3(ctx, f.x + f.w * 0.12, f.y + 0.05, f.w * 0.72, f.h * 0.28, 11, '#FFFFFF');
      return;
    }
    case 'plant':
      box3(ctx, f.x, f.y, f.w, f.h, 6, '#B5603F');
      box3(ctx, f.x + f.w * 0.1, f.y + f.h * 0.1, f.w * 0.8, f.h * 0.8, 18, '#4F9E52');
      return;
    case 'bookshelf': {
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      if (f.books) {
        const n = Math.max(3, Math.round(f.w * 2));
        for (let i = 0; i < n; i++) {
          const bx = f.x + (i / n) * f.w;
          box3(ctx, bx, f.y, f.w / n * 0.8, f.h * 0.5, h + 2 + (i % 3), BOOKS[i % BOOKS.length]);
        }
      }
      return;
    }
    case 'shelf': case 'display': {
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      if (f.goods) {
        const n = Math.max(3, Math.round(f.w * 1.6));
        for (let i = 0; i < n; i++)
          box3(ctx, f.x + (i / n) * f.w, f.y + f.h * 0.2, f.w / n * 0.7, f.h * 0.5, h + 5, GOODS[i % GOODS.length]);
      }
      return;
    }
    case 'stove':
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      box3(ctx, f.x + 0.1, f.y + 0.1, f.w * 0.3, f.h * 0.3, h + 1.5, '#C4443B');
      box3(ctx, f.x + f.w * 0.55, f.y + 0.1, f.w * 0.3, f.h * 0.3, h + 1.5, '#C4443B');
      return;
    case 'sink': case 'bathtub':
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      isoRect(ctx, f.x + f.w * 0.12, f.y + f.h * 0.12, f.w * 0.76, f.h * 0.76, '#9FC4D4');
      return;
    case 'toilet':
      box3(ctx, f.x, f.y, f.w, f.h * 0.55, h, col);
      box3(ctx, f.x, f.y + f.h * 0.5, f.w, f.h * 0.5, h - 4, '#EDF3F6');
      return;
    case 'monitor':
      box3(ctx, f.x, f.y, f.w, f.h * 0.5, h, col);
      isoRect(ctx, f.x + 0.05, f.y, f.w * 0.9, f.h * 0.3, '#7FC4E0');
      return;
    case 'tv':
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      isoRect(ctx, f.x + 0.08, f.y + 0.05, f.w * 0.84, f.h * 0.6, accent || '#4F7FB5');
      return;
    case 'board':
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      return;
    case 'engine':
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      box3(ctx, f.x + 0.1, f.y + 0.1, f.w * 0.8, f.h * 0.35, h + 5, '#F2E2C6');
      return;
    case 'vault':
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
      isoRect(ctx, f.x + f.w * 0.25, f.y + f.h * 0.25, f.w * 0.5, f.h * 0.5, '#E8DFB8');
      return;
    case 'cell': {
      const n = Math.max(3, Math.round(f.w * 1.4));
      for (let i = 0; i <= n; i++)
        box3(ctx, f.x + (i / n) * f.w, f.y + f.h - 0.12, 0.09, 0.09, h, '#7A8794');
      box3(ctx, f.x, f.y, f.w, 0.1, h, '#7A8794');
      return;
    }
    case 'curtain':
      box3(ctx, f.x, f.y, Math.max(0.1, f.w), f.h, h, col);
      return;
    case 'reception': case 'counter': case 'till':
      box3(ctx, f.x, f.y, f.w, f.h, h, f.type === 'reception' ? (accent || col) : col);
      isoRect(ctx, f.x, f.y, f.w, f.h, 'rgba(255,255,255,.18)');
      return;
    default:
      box3(ctx, f.x, f.y, f.w, f.h, h, col);
  }
}

let ROOFS_ON = false;
function setRoofs(on) { ROOFS_ON = !!on; }

const ROOF_COL = {
  house: ['#C0553F', '#3F6FA8', '#5C9E63', '#C98A4B', '#8E5C93', '#B5603F'],
  hospital: ['#D64545'], police: ['#2F4B7C'], fire: ['#C6362A'],
  school: ['#6E9E4A'], library: ['#7C6248'], bank: ['#9B8A45'],
  bakery: ['#C98A4B'], restaurant: ['#A4485C'], cafe: ['#B5643C'],
  grocery: ['#D9552F'], pharmacy: ['#3FA98C'], government: ['#C9A227'],
  college: ['#6A5A9E'], office: ['#597B92'], market: ['#C8613F'],
  inn: ['#8C6239'], construct: ['#D9992F'],
};

function drawSolid(ctx, plan, lightsOn) {
  const [floor, floorAlt, shell, accent] = ROOM_STYLE[plan.style] || ROOM_STYLE.house;
  const storeys = plan.storeys || (plan.style === 'house' ? 1 : 2);
  const SH = 26;
  const H = storeys * SH;
  const roofs = ROOF_COL[plan.style] || ROOF_COL.house;
  const roofCol = roofs[Math.floor(nz(plan.x, plan.y, 'roof') * roofs.length)];

  isoRect(ctx, plan.x - 0.2, plan.y - 0.2, plan.w + 0.4, plan.h + 0.4, 'rgba(30,50,30,.18)');
  box3(ctx, plan.x, plan.y, plan.w, plan.h, H, shell);

  const winCol = lightsOn ? '#FFDF8A' : '#BCD6E6';
  for (let sIdx = 0; sIdx < storeys; sIdx++) {
    const lift = sIdx * SH + SH * 0.42;
    const wh = SH * 0.34;
    for (let i = 0.6; i < plan.w - 0.5; i += 1.6) {
      const a = [isoX(plan.x + i, plan.y + plan.h), isoY(plan.x + i, plan.y + plan.h) - lift];
      const b = [isoX(plan.x + i + 0.9, plan.y + plan.h), isoY(plan.x + i + 0.9, plan.y + plan.h) - lift];
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
      ctx.lineTo(b[0], b[1] - wh); ctx.lineTo(a[0], a[1] - wh);
      ctx.closePath(); ctx.fillStyle = winCol; ctx.fill();
    }
    for (let j = 0.6; j < plan.h - 0.5; j += 1.6) {
      const a = [isoX(plan.x + plan.w, plan.y + j), isoY(plan.x + plan.w, plan.y + j) - lift];
      const b = [isoX(plan.x + plan.w, plan.y + j + 0.9), isoY(plan.x + plan.w, plan.y + j + 0.9) - lift];
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
      ctx.lineTo(b[0], b[1] - wh); ctx.lineTo(a[0], a[1] - wh);
      ctx.closePath(); ctx.fillStyle = shade(winCol, 0.86); ctx.fill();
    }
  }

  const dx = plan.x + plan.w * 0.5, dy = plan.y + plan.h;
  const d0 = [isoX(dx, dy), isoY(dx, dy)], d1 = [isoX(dx + 0.9, dy), isoY(dx + 0.9, dy)];
  ctx.beginPath();
  ctx.moveTo(d0[0], d0[1]); ctx.lineTo(d1[0], d1[1]);
  ctx.lineTo(d1[0], d1[1] - 17); ctx.lineTo(d0[0], d0[1] - 17);
  ctx.closePath(); ctx.fillStyle = shade(accent, 0.75); ctx.fill();
  if (['shop', 'kitchenset'].includes(plan.roomsKind) || ['market', 'grocery', 'bakery', 'cafe', 'restaurant', 'pharmacy'].includes(plan.style)) {
    box3(ctx, plan.x + 0.4, plan.y + plan.h - 0.1, plan.w - 0.8, 0.7, 20, accent);
  }

  const pitched = plan.style === 'house' || plan.w <= 12;
  if (pitched) {
    const rise = 13 + Math.min(plan.w, plan.h) * 1.1;
    const P4 = (a, b) => [isoX(a, b), isoY(a, b) - H];
    const A = P4(plan.x, plan.y), B = P4(plan.x + plan.w, plan.y);
    const C = P4(plan.x + plan.w, plan.y + plan.h), D = P4(plan.x, plan.y + plan.h);
    const r1 = [isoX(plan.x + plan.w * 0.5, plan.y + 0.25), isoY(plan.x + plan.w * 0.5, plan.y + 0.25) - H - rise];
    const r2 = [isoX(plan.x + plan.w * 0.5, plan.y + plan.h - 0.25), isoY(plan.x + plan.w * 0.5, plan.y + plan.h - 0.25) - H - rise];
    const poly = (pts, fill) => {
      ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
    };
    poly([A, B, r1], shade(roofCol, 0.8));
    poly([D, C, r2], shade(roofCol, 0.8));
    poly([D, r2, r1, A], shade(roofCol, 0.88));
    poly([C, r2, r1, B], roofCol);
    if (nz(plan.x, plan.y, 'chim') > 0.5)
      box3(ctx, plan.x + plan.w * 0.7, plan.y + plan.h * 0.3, 0.7, 0.7, H + rise + 8, '#8C6B58');
  } else {
    box3(ctx, plan.x, plan.y, plan.w, plan.h, H + 4, shade(roofCol, 0.95));
    box3(ctx, plan.x, plan.y, plan.w, 0.3, H + 11, roofCol);
    box3(ctx, plan.x, plan.y, 0.3, plan.h, H + 11, shade(roofCol, 0.9));
    box3(ctx, plan.x + plan.w * 0.55, plan.y + plan.h * 0.3, 1.6, 1.6, H + 16, shade(shell, 0.9));
  }
}

function drawPlan(ctx, plan, lightsOn) {
  if (ROOFS_ON) { drawSolid(ctx, plan, lightsOn); return; }
  const [floor, floorAlt, shell, accent] = ROOM_STYLE[plan.style] || ROOM_STYLE.house;
  const WALL_H = 30;

  isoRect(ctx, plan.x - 0.15, plan.y - 0.15, plan.w + 0.3, plan.h + 0.3, shade(shell, 0.9));

  for (const room of plan.rooms) {
    const wet = room.purpose === 'bath' || room.purpose === 'ward' || room.purpose === 'nurses';
    const a = wet ? '#DCE9EE' : floor;
    const b = wet ? '#CFE0E6' : floorAlt;
    for (let ty = room.y; ty < room.y + room.h; ty++)
      for (let tx = room.x; tx < room.x + room.w; tx++)
        isoTile(ctx, tx, ty, (tx + ty) % 2 ? a : b, null);
  }

  box3(ctx, plan.x, plan.y, plan.w, 0.16, WALL_H, shell);
  box3(ctx, plan.x, plan.y, 0.16, plan.h, WALL_H, shade(shell, 0.94));

  for (const room of plan.rooms) {
    if (room.x > plan.x) box3(ctx, room.x, room.y, 0.1, room.h, 14, shade(shell, 0.88));
    if (room.y > plan.y) box3(ctx, room.x, room.y, room.w, 0.1, 14, shade(shell, 0.88));
  }

  const all = [];
  for (const room of plan.rooms) for (const f of room.furniture) all.push(f);
  all.sort((p, q) => (p.x + p.y) - (q.x + q.y));
  for (const f of all) drawFurniture(ctx, f, accent);

  box3(ctx, plan.x, plan.y + plan.h - 0.16, plan.w, 0.16, 7, shade(shell, 0.8));
  box3(ctx, plan.x + plan.w - 0.16, plan.y, 0.16, plan.h, 7, shade(shell, 0.8));

  if (lightsOn) {
    ctx.globalAlpha = 0.25;
    for (const room of plan.rooms)
      isoRect(ctx, room.x, room.y, room.w, room.h, '#FFDF8A');
    ctx.globalAlpha = 1;
  }
}

const props = [];

function tree(ctx, tx, ty, s, kind) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.beginPath(); ctx.ellipse(x, y + 4, 11 * s, 5 * s, 0, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(30,60,30,.22)'; ctx.fill();
  ctx.fillStyle = '#7A5230';
  ctx.fillRect(x - 2.5 * s, y - 14 * s, 5 * s, 16 * s);
  const pal = kind === 'pine' ? ['#2F7D46', '#3E9457']
            : kind === 'blossom' ? ['#E48FB4', '#F4BFD5'] : ['#3F8F45', '#57A85C'];
  if (kind === 'pine') {
    for (let i = 0; i < 3; i++) {
      ctx.beginPath();
      ctx.moveTo(x, y - (34 - i * 8) * s);
      ctx.lineTo(x - (11 - i * 2) * s, y - (12 + i * 7) * s);
      ctx.lineTo(x + (11 - i * 2) * s, y - (12 + i * 7) * s);
      ctx.closePath(); ctx.fillStyle = pal[i % 2]; ctx.fill();
    }
  } else {
    ctx.beginPath(); ctx.arc(x, y - 20 * s, 11 * s, 0, Math.PI * 2);
    ctx.fillStyle = pal[0]; ctx.fill();
    ctx.beginPath(); ctx.arc(x - 4 * s, y - 24 * s, 7 * s, 0, Math.PI * 2);
    ctx.fillStyle = pal[1]; ctx.fill();
  }
}
function bush(ctx, tx, ty) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.fillStyle = '#4E9E52';
  ctx.beginPath(); ctx.arc(x - 4, y, 6, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(x + 4, y - 2, 7, 0, Math.PI * 2); ctx.fill();
}
function flowers(ctx, tx, ty, seed) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  const cols = ['#E8556D', '#F2C14E', '#EC7FB0', '#FFFFFF', '#B98BE0'];
  for (let i = 0; i < 4; i++) {
    ctx.beginPath();
    ctx.arc(x - 10 + nz(tx, ty, 'f' + i + seed) * 20, y - 3 + nz(tx, ty, 'g' + i) * 9, 2.4, 0, Math.PI * 2);
    ctx.fillStyle = cols[i % cols.length]; ctx.fill();
  }
}
function lamp(ctx, tx, ty, lit) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  if (lit) {
    const g = ctx.createRadialGradient(x, y - 26, 2, x, y - 26, 30);
    g.addColorStop(0, 'rgba(255,224,138,.42)'); g.addColorStop(1, 'rgba(255,224,138,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y - 26, 30, 0, Math.PI * 2); ctx.fill();
  }
  ctx.strokeStyle = '#4A5560'; ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y - 26); ctx.stroke();
  ctx.beginPath(); ctx.arc(x, y - 29, 4, 0, Math.PI * 2);
  ctx.fillStyle = lit ? '#FFE08A' : '#9FB0BE'; ctx.fill();
}
function bench(ctx, tx, ty) { box3(ctx, tx, ty + 0.3, 0.9, 0.35, 8, '#9A6E42'); }
function fountainProp(ctx, tx, ty, t) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.beginPath(); ctx.ellipse(x, y, 40, 20, 0, 0, Math.PI * 2);
  ctx.fillStyle = '#C9BDA2'; ctx.fill();
  ctx.beginPath(); ctx.ellipse(x, y, 31, 15, 0, 0, Math.PI * 2);
  ctx.fillStyle = PAL.water; ctx.fill();
  box3(ctx, tx - 0.1, ty - 0.1, 0.2, 0.2, 22, '#D8CDB4');
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 + t * 1.6;
    ctx.beginPath();
    ctx.arc(x + Math.cos(a) * 13, y - 20 + Math.sin(a * 2) * 5, 2.2, 0, Math.PI * 2);
    ctx.fillStyle = PAL.waterFoam; ctx.fill();
  }
}
function pondProp(ctx, tx, ty) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.beginPath(); ctx.ellipse(x, y, 96, 48, 0, 0, Math.PI * 2);
  ctx.fillStyle = '#6EA84E'; ctx.fill();
  ctx.beginPath(); ctx.ellipse(x, y, 86, 41, 0, 0, Math.PI * 2);
  ctx.fillStyle = PAL.waterDeep; ctx.fill();
  ctx.beginPath(); ctx.ellipse(x, y - 2, 74, 33, 0, 0, Math.PI * 2);
  ctx.fillStyle = PAL.water; ctx.fill();
}
function cropRow(ctx, tx, ty) {
  isoTile(ctx, tx, ty, nz(tx, ty, 'soil') > 0.5 ? PAL.soil : PAL.soilDark, null);
  const x = isoX(tx, ty), y = isoY(tx, ty) + TILE_H / 2;
  ctx.strokeStyle = nz(tx, ty, 'crop') > 0.5 ? '#C8B23F' : '#96A83C';
  ctx.lineWidth = 2.2;
  for (let i = -1; i <= 1; i++) {
    ctx.beginPath();
    ctx.moveTo(x + i * 12, y + 3); ctx.lineTo(x + i * 12 + 1, y - 9); ctx.stroke();
  }
}
function rocks(ctx, tx, ty) {
  const x = isoX(tx, ty), y = isoY(tx, ty) + TILE_H / 2;
  for (let i = 0; i < 3; i++) {
    ctx.beginPath();
    ctx.ellipse(x - 12 + nz(tx, ty, 'r' + i) * 24, y - 4 + nz(tx, ty, 's' + i) * 8,
                6 + nz(tx, ty, 'q' + i) * 4, 4, 0, 0, Math.PI * 2);
    ctx.fillStyle = i % 2 ? PAL.rock : PAL.rockDark; ctx.fill();
  }
}
function car(ctx, tx, ty, seed) {
  const cols = ['#D95D4E', '#4F7FB5', '#F0F2F4', '#E8B33D', '#5C9E63'];
  const c = cols[Math.floor(nz(tx, ty, 'c' + seed) * cols.length)];
  box3(ctx, tx + 0.1, ty + 0.25, 0.8, 0.5, 9, c);
  box3(ctx, tx + 0.25, ty + 0.3, 0.5, 0.4, 15, shade(c, 1.12));
}
function cow(ctx, tx, ty, seed) {
  const dark = nz(tx, ty, 'cow' + seed) > 0.5;
  box3(ctx, tx, ty, 0.85, 0.5, 11, dark ? '#F2EFE8' : '#C9A882');
  if (dark) {
    box3(ctx, tx + 0.1, ty + 0.05, 0.3, 0.4, 11.6, '#4A4238');
    box3(ctx, tx + 0.55, ty + 0.1, 0.22, 0.3, 11.6, '#4A4238');
  }
  box3(ctx, tx + 0.78, ty + 0.12, 0.26, 0.26, 13, dark ? '#F2EFE8' : '#C9A882');
}
function sheep(ctx, tx, ty) {
  const x = isoX(tx + 0.3, ty + 0.25), y = isoY(tx + 0.3, ty + 0.25);
  ctx.beginPath(); ctx.ellipse(x, y + 2, 9, 4, 0, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(30,60,30,.2)'; ctx.fill();
  ctx.fillStyle = '#F4F1E8';
  ctx.beginPath(); ctx.arc(x - 3, y - 6, 6, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(x + 3, y - 7, 5.5, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#3C3630';
  ctx.beginPath(); ctx.arc(x + 8, y - 6, 3, 0, Math.PI * 2); ctx.fill();
}
function chicken(ctx, tx, ty) {
  const x = isoX(tx + 0.4, ty + 0.4), y = isoY(tx + 0.4, ty + 0.4);
  ctx.fillStyle = '#F6F2E8';
  ctx.beginPath(); ctx.ellipse(x, y - 5, 4.5, 4, 0, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(x + 3.5, y - 9, 2.6, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#D6503C';
  ctx.beginPath(); ctx.arc(x + 3.5, y - 11.5, 1.4, 0, Math.PI * 2); ctx.fill();
}
function dog(ctx, tx, ty) {
  const x = isoX(tx + 0.4, ty + 0.4), y = isoY(tx + 0.4, ty + 0.4);
  ctx.fillStyle = '#A9784A';
  ctx.beginPath(); ctx.ellipse(x, y - 5, 6, 3.6, 0, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(x + 5, y - 8, 3, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#A9784A'; ctx.lineWidth = 1.6;
  ctx.beginPath(); ctx.moveTo(x - 6, y - 6); ctx.lineTo(x - 9, y - 10); ctx.stroke();
}
function duck(ctx, tx, ty) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.fillStyle = '#F4F1E8';
  ctx.beginPath(); ctx.ellipse(x, y, 5, 3, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#3E7C4A';
  ctx.beginPath(); ctx.arc(x + 4, y - 3, 2.4, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#E8A33D';
  ctx.fillRect(x + 6, y - 3.6, 3, 1.6);
}
function bicycle(ctx, tx, ty, seed) {
  const x = isoX(tx + 0.5, ty + 0.5), y = isoY(tx + 0.5, ty + 0.5);
  const c = ['#D95D4E', '#4F7FB5', '#59A86B', '#E8B33D'][Math.floor(nz(tx, ty, 'b' + seed) * 4)];
  ctx.strokeStyle = '#3C4654'; ctx.lineWidth = 1.6;
  ctx.beginPath(); ctx.arc(x - 6, y, 4.5, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(x + 6, y - 2, 4.5, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = c; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(x - 6, y); ctx.lineTo(x, y - 7); ctx.lineTo(x + 6, y - 2); ctx.stroke();
}
function boat(ctx, tx, ty) {
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.fillStyle = '#8C6239';
  ctx.beginPath(); ctx.ellipse(x, y, 18, 7, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#C89B62';
  ctx.beginPath(); ctx.ellipse(x, y - 1.5, 14, 4.5, 0, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#7A5230'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(x, y - 4); ctx.lineTo(x, y - 26); ctx.stroke();
  ctx.fillStyle = '#F4F1E8';
  ctx.beginPath(); ctx.moveTo(x, y - 26); ctx.lineTo(x + 13, y - 9); ctx.lineTo(x, y - 6);
  ctx.closePath(); ctx.fill();
}
function jetty(ctx, tx, ty, len) {
  for (let i = 0; i < len; i++) box3(ctx, tx + i, ty, 1, 1.6, 4, '#A9825A');
  for (let i = 0; i < len; i += 2) box3(ctx, tx + i, ty + 1.5, 0.16, 0.16, 9, '#7A5230');
}
function streetTree(ctx, tx, ty) { tree(ctx, tx, ty, 0.85, 'round'); }
function postbox(ctx, tx, ty) {
  box3(ctx, tx + 0.35, ty + 0.35, 0.3, 0.3, 18, '#C4443B');
  box3(ctx, tx + 0.32, ty + 0.32, 0.36, 0.36, 3, '#9E332C');
}
function well(ctx, tx, ty) {
  box3(ctx, tx, ty, 0.9, 0.9, 9, '#9AA1A8');
  isoRect(ctx, tx + 0.15, ty + 0.15, 0.6, 0.6, '#2E5D77');
  box3(ctx, tx + 0.05, ty + 0.05, 0.12, 0.12, 26, '#7A5230');
  box3(ctx, tx + 0.83, ty + 0.83, 0.12, 0.12, 26, '#7A5230');
  const x = isoX(tx + 0.5, ty + 0.5), y = isoY(tx + 0.5, ty + 0.5) - 30;
  ctx.fillStyle = '#A8503C';
  ctx.beginPath(); ctx.moveTo(x, y - 8); ctx.lineTo(x + 22, y + 4);
  ctx.lineTo(x, y + 14); ctx.lineTo(x - 22, y + 4); ctx.closePath(); ctx.fill();
}
function haystack(ctx, tx, ty) {
  const x = isoX(tx + 0.5, ty + 0.5), y = isoY(tx + 0.5, ty + 0.5);
  ctx.fillStyle = '#D9B44A';
  ctx.beginPath(); ctx.ellipse(x, y, 15, 8, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#E8C862';
  ctx.beginPath(); ctx.ellipse(x, y - 7, 12, 6, 0, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.ellipse(x, y - 13, 8, 4, 0, 0, Math.PI * 2); ctx.fill();
}

function swingSet(ctx, tx, ty) {
  box3(ctx, tx, ty, 0.12, 0.12, 26, '#7A93AC');
  box3(ctx, tx + 1.8, ty, 0.12, 0.12, 26, '#7A93AC');
  const x = isoX(tx, ty), y = isoY(tx, ty), x2 = isoX(tx + 1.8, ty), y2 = isoY(tx + 1.8, ty);
  ctx.strokeStyle = '#8FA0AE'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(x, y - 26); ctx.lineTo(x2, y2 - 26); ctx.stroke();
  for (const f of [0.35, 0.7]) {
    const sx = x + (x2 - x) * f, sy = y + (y2 - y) * f;
    ctx.strokeStyle = '#9AA1A8'; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(sx, sy - 26); ctx.lineTo(sx, sy - 10); ctx.stroke();
    ctx.fillStyle = '#E4694E'; ctx.fillRect(sx - 5, sy - 10, 10, 3);
  }
}
function slide(ctx, tx, ty) {
  box3(ctx, tx, ty, 0.5, 0.5, 22, '#4F7FB5');
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.fillStyle = '#F2C14E';
  ctx.beginPath();
  ctx.moveTo(x, y - 20); ctx.lineTo(x + 10, y - 16);
  ctx.lineTo(x + 34, y + 8); ctx.lineTo(x + 24, y + 10); ctx.closePath(); ctx.fill();
}
function sandpit(ctx, tx, ty) {
  isoRect(ctx, tx - 1, ty - 1, 2.4, 2.4, '#E8D9AE');
  box3(ctx, tx - 1, ty - 1, 2.4, 0.12, 5, '#B98A56');
  box3(ctx, tx - 1, ty - 1, 0.12, 2.4, 5, '#A9825A');
}
function gazebo(ctx, tx, ty) {
  isoRect(ctx, tx - 1.2, ty - 1.2, 2.6, 2.6, '#E4D0AE');
  for (const [ox, oy] of [[-1.1, -1.1], [1.3, -1.1], [-1.1, 1.3], [1.3, 1.3]])
    box3(ctx, tx + ox, ty + oy, 0.16, 0.16, 30, '#A9825A');
  const x = isoX(tx + 0.1, ty + 0.1), y = isoY(tx + 0.1, ty + 0.1) - 30;
  ctx.beginPath();
  ctx.moveTo(x, y - 16);
  ctx.lineTo(x + 46, y + 6); ctx.lineTo(x, y + 24); ctx.lineTo(x - 46, y + 6);
  ctx.closePath(); ctx.fillStyle = '#B5603F'; ctx.fill();
}
function picnicTable(ctx, tx, ty) {
  box3(ctx, tx, ty + 0.25, 1.2, 0.6, 11, '#C89B62');
  box3(ctx, tx, ty, 1.2, 0.22, 6, '#A9825A');
  box3(ctx, tx, ty + 0.95, 1.2, 0.22, 6, '#A9825A');
}
function flowerBed(ctx, tx, ty, seed) {
  isoRect(ctx, tx - 0.7, ty - 0.5, 1.6, 1.2, '#8C6239');
  const cols = ['#E8556D', '#F2C14E', '#EC7FB0', '#B98BE0', '#FFFFFF'];
  for (let i = 0; i < 8; i++) {
    const fx = tx - 0.6 + (i % 4) * 0.38, fy = ty - 0.4 + Math.floor(i / 4) * 0.5;
    const x = isoX(fx, fy), y = isoY(fx, fy);
    ctx.beginPath(); ctx.arc(x, y - 3, 2.8, 0, Math.PI * 2);
    ctx.fillStyle = cols[(i + seed) % cols.length]; ctx.fill();
  }
}
function hedge(ctx, tx, ty) { box3(ctx, tx, ty + 0.2, 1, 0.6, 12, '#3F8F45'); }
function statue(ctx, tx, ty) {
  isoRect(ctx, tx - 0.4, ty - 0.4, 1, 1, '#C9BDA2');
  box3(ctx, tx - 0.15, ty - 0.15, 0.35, 0.35, 22, '#AEB6AE');
  const x = isoX(tx, ty), y = isoY(tx, ty);
  ctx.beginPath(); ctx.arc(x, y - 27, 5, 0, Math.PI * 2);
  ctx.fillStyle = '#C2C9C2'; ctx.fill();
}
function lilyPads(ctx, tx, ty) {
  for (let i = 0; i < 6; i++) {
    const px = tx - 1.5 + i * 0.6, py = ty - 0.8 + (i % 2) * 1.4;
    const x = isoX(px, py), y = isoY(px, py);
    ctx.beginPath(); ctx.ellipse(x, y, 8, 4, 0, 0, Math.PI * 2);
    ctx.fillStyle = '#4F9E52'; ctx.fill();
  }
}

function buildVillage() {
  buildPaths();
  buildPlans();
  props.length = 0;

  const pk = ZONES.park;
  const pcx = pk.x + pk.w * 0.5, pcy = pk.y + pk.h * 0.55;
  props.push({ tx: pcx, ty: pcy, kind: 'pond' });
  props.push({ tx: pcx, ty: pcy, kind: 'lily' });
  [[-2.2, -0.6], [1.8, 0.8], [0.2, 1.6]].forEach(([ox, oy]) =>
    props.push({ tx: pcx + ox, ty: pcy + oy, kind: 'duck' }));
  props.push({ tx: pk.x + 5, ty: pk.y + 4, kind: 'gazebo' });
  props.push({ tx: pk.x + pk.w - 9, ty: pk.y + 3, kind: 'swing' });
  props.push({ tx: pk.x + pk.w - 6, ty: pk.y + 7, kind: 'slide' });
  props.push({ tx: pk.x + pk.w - 9, ty: pk.y + 11, kind: 'sandpit' });
  props.push({ tx: pk.x + 6, ty: pk.y + pk.h - 5, kind: 'statue' });
  props.push({ tx: pk.x + 14, ty: pk.y + pk.h - 4, kind: 'dog' });
  [[12, 3], [18, 5], [12, pk.h - 6], [22, pk.h - 4], [30, 6]].forEach(([ox, oy]) =>
    props.push({ tx: pk.x + ox, ty: pk.y + oy, kind: 'picnic' }));
  [[3, 10], [9, 14], [pk.w - 5, pk.h - 6], [3, pk.h - 9], [26, 12]].forEach(([ox, oy], i) =>
    props.push({ tx: pk.x + ox, ty: pk.y + oy, kind: 'flowerbed', seed: i }));
  for (let i = 0; i < pk.w; i += 3)
    props.push({ tx: pk.x + i, ty: pk.y + pk.h - 1, kind: 'hedge' });
  for (let ty = pk.y; ty < pk.y + pk.h; ty++)
    for (let tx = pk.x; tx < pk.x + pk.w; tx++) {
      if (Math.hypot(tx - pcx, ty - pcy) < 7) continue;
      const r = nz(tx, ty, 'park');
      if (r > 0.90) props.push({ tx, ty, kind: 'tree', s: 1.0, treeKind: r > 0.96 ? 'blossom' : 'round' });
      else if (r > 0.84) props.push({ tx, ty, kind: 'bush' });
      else if (r > 0.80) props.push({ tx, ty, kind: 'flowers', seed: r });
    }

  const sq = ZONES.townsquare;
  props.push({ tx: sq.x + sq.w / 2 - 1, ty: sq.y + sq.h / 2 - 1, kind: 'fountain' });
  props.push({ tx: sq.x + 3, ty: sq.y + 3, kind: 'statue' });
  props.push({ tx: sq.x + sq.w - 5, ty: sq.y + 3, kind: 'well' });
  props.push({ tx: sq.x + 2, ty: sq.y + sq.h - 4, kind: 'postbox' });
  [[1, 1], [sq.w - 2, 1], [1, sq.h - 2], [sq.w - 2, sq.h - 2], [sq.w / 2, 1]].forEach(([ox, oy]) =>
    props.push({ tx: sq.x + ox, ty: sq.y + oy, kind: 'lamp' }));
  [[5, 5], [sq.w - 7, 5], [5, sq.h - 6], [sq.w - 7, sq.h - 6], [12, 2]].forEach(([ox, oy]) =>
    props.push({ tx: sq.x + ox, ty: sq.y + oy, kind: 'bench' }));
  [[8, 8], [sq.w - 9, 8], [8, sq.h - 9], [sq.w - 9, sq.h - 9]].forEach(([ox, oy], i) =>
    props.push({ tx: sq.x + ox, ty: sq.y + oy, kind: 'flowerbed', seed: i + 2 }));
  [[4, 10], [sq.w - 4, 12]].forEach(([ox, oy], i) =>
    props.push({ tx: sq.x + ox, ty: sq.y + oy, kind: 'bicycle', seed: i }));

  const fz = ZONES.farmland;
  for (let ty = fz.y; ty < fz.y + fz.h; ty++)
    for (let tx = fz.x; tx < fz.x + fz.w; tx++) props.push({ tx, ty, kind: 'crop' });
  [[2, -2], [12, -2], [26, -2]].forEach(([ox, oy]) =>
    props.push({ tx: fz.x + ox, ty: fz.y + oy, kind: 'haystack' }));
  for (let i = 0; i < 8; i++)
    props.push({ tx: fz.x + 3 + i * 4, ty: fz.y - 1 + (i % 2) * 1.4, kind: 'chicken' });

  const pa = ZONES.pasture;
  for (let ty = pa.y; ty < pa.y + pa.h; ty += 3)
    for (let tx = pa.x; tx < pa.x + pa.w; tx += 4) {
      const r = nz(tx, ty, 'graze');
      if (r > 0.62) props.push({ tx, ty, kind: 'cow', seed: tx + ty });
      else if (r > 0.32) props.push({ tx, ty, kind: 'sheep' });
    }
  for (let i = 0; i < pa.w; i += 2) {
    props.push({ tx: pa.x + i, ty: pa.y - 1, kind: 'hedge' });
    props.push({ tx: pa.x + i, ty: pa.y + pa.h, kind: 'hedge' });
  }
  props.push({ tx: pa.x + 4, ty: pa.y + 3, kind: 'dog' });

  const orc = ZONES.orchard;
  for (let ty = orc.y; ty < orc.y + orc.h; ty += 3)
    for (let tx = orc.x; tx < orc.x + orc.w; tx += 3)
      props.push({ tx, ty, kind: 'tree', s: 0.95, treeKind: 'blossom' });

  const dk = ZONES.docks;
  props.push({ tx: dk.x + 1, ty: dk.y + 4, kind: 'jetty', len: 9 });
  props.push({ tx: dk.x + 1, ty: dk.y + 16, kind: 'jetty', len: 9 });
  props.push({ tx: dk.x + 11, ty: dk.y + 5, kind: 'boat' });
  props.push({ tx: dk.x + 11, ty: dk.y + 17, kind: 'boat' });
  props.push({ tx: dk.x + 3, ty: dk.y + 10, kind: 'crate' });

  const oz = ZONES.oreoutcrop;
  for (let ty = oz.y; ty < oz.y + oz.h; ty++)
    for (let tx = oz.x; tx < oz.x + oz.w; tx++)
      if (nz(tx, ty, 'ore') > 0.4) props.push({ tx, ty, kind: 'rocks' });

  const fo = ZONES.forest;
  for (let ty = fo.y; ty < fo.y + fo.h; ty++)
    for (let tx = fo.x; tx < fo.x + fo.w; tx++)
      if (nz(tx, ty, 'for') > 0.42)
        props.push({ tx, ty, kind: 'tree', s: 1.0 + nz(tx, ty, 'ts') * 0.3, treeKind: 'pine' });

  const along = (pts, every, fn) => {
    let acc = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
      const len = Math.hypot(x1 - x0, y1 - y0);
      for (let d = 0; d < len; d += 0.5) {
        acc += 0.5;
        if (acc < every) continue;
        acc = 0;
        const f = d / len;
        fn(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f);
      }
    }
  };
  const free = (tx, ty) => tx > 1 && ty > 1 && tx < GRID_W - 2 && ty < GRID_H - 2 &&
                           !isRiver(tx, ty) && !ZONE_ORDER.some(z => inZone(ZONES[z], tx, ty));

  for (const [w, pts] of AVENUE_PATHS) {
    along(pts, 9, (x, y) => {
      const tx = Math.round(x), ty = Math.round(y + w / 2 + 1);
      if (free(tx, ty)) props.push({ tx, ty, kind: 'lamp' });
    });
    along(pts, 11, (x, y) => {
      const tx = Math.round(x), ty = Math.round(y - w / 2 - 1);
      if (free(tx, ty)) props.push({ tx, ty, kind: 'streetTree' });
    });

  }
  for (const [w, pts] of STREET_PATHS) {
    along(pts, 15, (x, y) => {
      const tx = Math.round(x), ty = Math.round(y + w / 2 + 1);
      if (free(tx, ty)) props.push({ tx, ty, kind: 'lamp' });
    });
    along(pts, 29, (x, y) => {
      const tx = Math.round(x), ty = Math.round(y);
      if (isRoad(tx, ty) && nz(tx, ty, 'bic') > 0.5) props.push({ tx, ty, kind: 'bicycle', seed: tx });
    });
  }
  for (const [w, pts] of FOOT_PATHS) {
    along(pts, 21, (x, y) => {
      const tx = Math.round(x), ty = Math.round(y + 1.4);
      if (free(tx, ty)) props.push({ tx, ty, kind: 'bench' });
    });
    along(pts, 34, (x, y) => {
      const tx = Math.round(x), ty = Math.round(y - 1.4);
      if (free(tx, ty)) props.push({ tx, ty, kind: 'lamp' });
    });
  }

  for (let ty = 0; ty < GRID_H; ty++)
    for (let tx = 0; tx < GRID_W; tx++) {
      if (isRoad(tx, ty) || isPath(tx, ty) || isRiver(tx, ty) || isBridge(tx, ty) || isBank(tx, ty)) continue;
      if (ZONE_ORDER.some(z => inZone(ZONES[z], tx, ty))) continue;
      const r = nz(tx, ty, 'scatter');
      if (r > 0.905) props.push({ tx, ty, kind: 'tree', s: 0.9, treeKind: r > 0.965 ? 'pine' : 'round' });
      else if (r > 0.855) props.push({ tx, ty, kind: 'bush' });
      else if (r > 0.815) props.push({ tx, ty, kind: 'flowers', seed: r });
    }

  props.sort((a, b) => depth(a.tx, a.ty) - depth(b.tx, b.ty));
}

function drawPlinth(ctx) {
  const D = 46;
  const N = [isoX(0, 0), isoY(0, 0)];
  const E = [isoX(GRID_W, 0), isoY(GRID_W, 0)];
  const S = [isoX(GRID_W, GRID_H), isoY(GRID_W, GRID_H)];
  const W = [isoX(0, GRID_H), isoY(0, GRID_H)];
  const poly = (pts, fill) => {
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  };
  poly([N, E, S, W], '#6BBA50');
  poly([W, S, [S[0], S[1] + D], [W[0], W[1] + D]], '#8A6440');
  poly([S, E, [E[0], E[1] + D], [S[0], S[1] + D]], '#A0764C');
  poly([W, S, [S[0], S[1] + 9], [W[0], W[1] + 9]], '#5F8F45');
  poly([S, E, [E[0], E[1] + 9], [S[0], S[1] + 9]], '#6FA050');
  ctx.fillStyle = 'rgba(80,58,38,.5)';
  for (let i = 0; i < 26; i++) {
    const f = (i + 0.5) / 26;
    const x = W[0] + (S[0] - W[0]) * f, y = W[1] + (S[1] - W[1]) * f;
    ctx.beginPath(); ctx.ellipse(x, y + 16 + (i % 3) * 8, 7, 4, 0, 0, Math.PI * 2); ctx.fill();
    const x2 = S[0] + (E[0] - S[0]) * f, y2 = S[1] + (E[1] - S[1]) * f;
    ctx.beginPath(); ctx.ellipse(x2, y2 + 20 + (i % 4) * 6, 6, 3.5, 0, 0, Math.PI * 2); ctx.fill();
  }
}

function drawGround(ctx, t, view) {
  drawPlinth(ctx);
  const y0 = view ? Math.max(0, view.minY) : 0;
  const y1 = view ? Math.min(GRID_H, view.maxY) : GRID_H;
  const x0 = view ? Math.max(0, view.minX) : 0;
  const x1 = view ? Math.min(GRID_W, view.maxX) : GRID_W;
  for (let ty = y0; ty < y1; ty++) {
    for (let tx = x0; tx < x1; tx++) {
      if (isRiver(tx, ty)) {
        const edge = bankTiles.has(key(tx, ty)) ? 1 : 0;
        const sh = Math.sin(t * 1.3 + tx * 0.6 + ty * 0.45) * 0.5 + 0.5;
        isoTile(ctx, tx, ty, edge ? '#7FC8E4' : (sh > 0.62 ? PAL.water : PAL.waterDeep), null);
        if (!edge && sh > 0.9) {
          const x = isoX(tx, ty), y = isoY(tx, ty) + TILE_H / 2;
          ctx.fillStyle = PAL.waterFoam;
          ctx.beginPath(); ctx.ellipse(x, y, 7, 3, 0, 0, Math.PI * 2); ctx.fill();
        }
        continue;
      }
      if (isBank(tx, ty)) { isoTile(ctx, tx, ty, '#E4D3A2', null); continue; }
      if (isBridge(tx, ty)) { isoTile(ctx, tx, ty, '#B08A5E', '#96754D'); continue; }
      if (isRoad(tx, ty)) { isoTile(ctx, tx, ty, PAL.road, PAL.roadEdge); continue; }
      if (isPath(tx, ty)) { isoTile(ctx, tx, ty, PAL.path, PAL.pathEdge); continue; }
      let paved = null;
      for (const zid of ZONE_ORDER) {
        if (!inZone(ZONES[zid], tx, ty)) continue;
        if (zid === 'townsquare' || zid === 'marketrow') paved = (tx + ty) % 2 ? PAL.path : PAL.pathEdge;
        else if (zid === 'oreoutcrop') paved = PAL.rockDark;
        else if (zid === 'construction') paved = '#E8D9AE';
        break;
      }
      if (paved) { isoTile(ctx, tx, ty, paved, null); continue; }
      const r = nz(tx, ty, 'g');
      isoTile(ctx, tx, ty, r > 0.75 ? PAL.grassA : r > 0.5 ? PAL.grassB : r > 0.25 ? PAL.grassC : PAL.grassD, null);
    }
  }
}

function drawProps(ctx, t, lightsOn, view) {
  const items = [];
  for (const p of props) {
    if (view && (p.tx < view.minX - 3 || p.tx > view.maxX + 3 ||
                 p.ty < view.minY - 3 || p.ty > view.maxY + 3)) continue;
    items.push({ d: depth(p.tx, p.ty), p });
  }
  for (const plan of plans) {
    if (view && (plan.x + plan.w < view.minX || plan.x > view.maxX ||
                 plan.y + plan.h < view.minY || plan.y > view.maxY)) continue;
    items.push({ d: depth(plan.x, plan.y), plan });
  }
  items.sort((a, b) => a.d - b.d);
  for (const it of items) {
    if (it.plan) { drawPlan(ctx, it.plan, lightsOn); continue; }
    const p = it.p;
    switch (p.kind) {
      case 'tree': tree(ctx, p.tx, p.ty, p.s, p.treeKind); break;
      case 'bush': bush(ctx, p.tx, p.ty); break;
      case 'flowers': flowers(ctx, p.tx, p.ty, p.seed); break;
      case 'lamp': lamp(ctx, p.tx, p.ty, lightsOn); break;
      case 'bench': bench(ctx, p.tx, p.ty); break;
      case 'fountain': fountainProp(ctx, p.tx, p.ty, t); break;
      case 'pond': pondProp(ctx, p.tx, p.ty); break;
      case 'crop': cropRow(ctx, p.tx, p.ty); break;
      case 'rocks': rocks(ctx, p.tx, p.ty); break;
      case 'car': car(ctx, p.tx, p.ty, p.seed); break;
      case 'swing': swingSet(ctx, p.tx, p.ty); break;
      case 'slide': slide(ctx, p.tx, p.ty); break;
      case 'sandpit': sandpit(ctx, p.tx, p.ty); break;
      case 'gazebo': gazebo(ctx, p.tx, p.ty); break;
      case 'picnic': picnicTable(ctx, p.tx, p.ty); break;
      case 'flowerbed': flowerBed(ctx, p.tx, p.ty, p.seed | 0); break;
      case 'hedge': hedge(ctx, p.tx, p.ty); break;
      case 'statue': statue(ctx, p.tx, p.ty); break;
      case 'lily': lilyPads(ctx, p.tx, p.ty); break;
      case 'cow': cow(ctx, p.tx, p.ty, p.seed | 0); break;
      case 'sheep': sheep(ctx, p.tx, p.ty); break;
      case 'chicken': chicken(ctx, p.tx, p.ty); break;
      case 'dog': dog(ctx, p.tx, p.ty); break;
      case 'duck': duck(ctx, p.tx, p.ty); break;
      case 'bicycle': bicycle(ctx, p.tx, p.ty, p.seed | 0); break;
      case 'boat': boat(ctx, p.tx, p.ty); break;
      case 'jetty': jetty(ctx, p.tx, p.ty, p.len); break;
      case 'postbox': postbox(ctx, p.tx, p.ty); break;
      case 'well': well(ctx, p.tx, p.ty); break;
      case 'haystack': haystack(ctx, p.tx, p.ty); break;
      case 'streetTree': streetTree(ctx, p.tx, p.ty); break;
    }
  }
}

const TOOL_COLOUR = {
  hoe: '#9AA1A8', axe: '#B9C4CC', pick: '#8FA0AE', hammer: '#9AA1A8',
  broom: '#D8B36A', hose: '#5BB4D8', tray: '#E8DBC2', box: '#C79A5F',
  pan: '#5A6472', cup: '#FFFFFF', book: '#7C6248', quill: '#F4EFE2',
  coin: '#E8C15A', cross: '#D64545', badge: '#C9A227', plan: '#F4EFE2',
};

function drawHat(ctx, sx, y, hat) {
  const cap = (col, brim) => {
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(sx, y - 22.5, 5, Math.PI, Math.PI * 2); ctx.fill();
    if (brim) ctx.fillRect(sx - 6.5, y - 22.5, 13, 1.8);
  };
  switch (hat) {
    case 'helmet-fire': cap('#C6362A', true); ctx.fillStyle = '#F2E2C6'; ctx.fillRect(sx - 2, y - 26, 4, 3); break;
    case 'helmet-work': cap('#E8B33D', true); break;
    case 'helmet-mine': cap('#5A6472', true); ctx.fillStyle = '#FFE08A';
      ctx.beginPath(); ctx.arc(sx, y - 25, 1.8, 0, Math.PI * 2); ctx.fill(); break;
    case 'cap-police': cap('#2F4B7C', true); ctx.fillStyle = '#C9A227'; ctx.fillRect(sx - 2, y - 25.5, 4, 1.6); break;
    case 'toque': ctx.fillStyle = '#FFFFFF';
      ctx.beginPath(); ctx.ellipse(sx, y - 26, 5, 4.5, 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillRect(sx - 5, y - 24, 10, 2.6); break;
    case 'straw': ctx.fillStyle = '#D9B44A';
      ctx.beginPath(); ctx.ellipse(sx, y - 22, 8.5, 3, 0, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(sx, y - 23.5, 4.4, Math.PI, Math.PI * 2); ctx.fill(); break;
    case 'scrub-cap': cap('#5FB7A6', false); break;
    case 'mortar': ctx.fillStyle = '#3C3F4A';
      ctx.beginPath(); ctx.arc(sx, y - 23, 4.6, Math.PI, Math.PI * 2); ctx.fill();
      ctx.fillRect(sx - 7, y - 24.5, 14, 2); break;
    case 'beanie': cap('#8E6BC4', false); break;
    case 'bandana': cap('#D6503C', false); break;
  }
}

function drawPerson(ctx, sx, sy, colour, hair, selected, talking, walking, t, tool, outfit) {
  const bob = walking ? Math.sin(t * 9 + sx * 0.1) * 1.3 : 0;
  const y = sy + bob;
  ctx.beginPath(); ctx.ellipse(sx, sy + 2, 6, 3, 0, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(0,0,0,.24)'; ctx.fill();
  ctx.fillStyle = (outfit && outfit.legs) || '#3C4654';
  ctx.fillRect(sx - 3, y - 7, 2.4, 7);
  ctx.fillRect(sx + 0.6, y - 7, 2.4, 7);
  ctx.fillStyle = colour;
  ctx.beginPath();
  ctx.moveTo(sx - 4.6, y - 7); ctx.lineTo(sx + 4.6, y - 7);
  ctx.lineTo(sx + 3.6, y - 17); ctx.lineTo(sx - 3.6, y - 17);
  ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#F2C9A0';
  ctx.beginPath(); ctx.arc(sx, y - 21, 4.8, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = hair;
  ctx.beginPath(); ctx.arc(sx, y - 22.6, 4.8, Math.PI, Math.PI * 2); ctx.fill();
  if (outfit) {
    if (outfit.coat) {
      ctx.fillStyle = outfit.coat;
      ctx.beginPath();
      ctx.moveTo(sx - 4.6, y - 7); ctx.lineTo(sx - 1.6, y - 7);
      ctx.lineTo(sx - 1.6, y - 17); ctx.lineTo(sx - 3.6, y - 17);
      ctx.closePath(); ctx.fill();
      ctx.beginPath();
      ctx.moveTo(sx + 1.6, y - 7); ctx.lineTo(sx + 4.6, y - 7);
      ctx.lineTo(sx + 3.6, y - 17); ctx.lineTo(sx + 1.6, y - 17);
      ctx.closePath(); ctx.fill();
    }
    if (outfit.hat) drawHat(ctx, sx, y, outfit.hat);
  }
  if (tool) {
    ctx.fillStyle = TOOL_COLOUR[tool] || '#8C8577';
    ctx.beginPath(); ctx.arc(sx + 7, y - 11 + bob, 2.8, 0, Math.PI * 2); ctx.fill();
  }
  if (selected) {
    ctx.strokeStyle = '#FFC94D'; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.ellipse(sx, sy + 2, 13, 7, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(sx, y - 32); ctx.lineTo(sx - 6, y - 42); ctx.lineTo(sx + 6, y - 42);
    ctx.closePath(); ctx.fillStyle = '#FFC94D'; ctx.fill();
  }
  if (talking) {
    ctx.fillStyle = '#FFFFFF';
    ctx.beginPath(); ctx.roundRect(sx + 6, y - 38, 21, 13, 6); ctx.fill();
    ctx.fillStyle = '#5A6472';
    for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.arc(sx + 11 + i * 5, y - 31, 1.5, 0, Math.PI * 2); ctx.fill(); }
  }
}

const BIRDS = [];
for (let i = 0; i < 26; i++) {
  BIRDS.push({
    cx: 20 + (i * 37) % (GRID_W - 40), cy: 14 + (i * 53) % (GRID_H - 30),
    r: 6 + (i % 5) * 3, speed: 0.18 + (i % 7) * 0.035,
    lift: 46 + (i % 6) * 14, phase: i * 0.9,
  });
}
function drawBirds(ctx, t) {
  ctx.strokeStyle = 'rgba(60,70,86,.72)';
  ctx.lineWidth = 1.6;
  for (const b of BIRDS) {
    const a = t * b.speed + b.phase;
    const tx = b.cx + Math.cos(a) * b.r;
    const ty = b.cy + Math.sin(a) * b.r * 0.6;
    const x = isoX(tx, ty), y = isoY(tx, ty) - b.lift;
    const flap = Math.sin(t * 7 + b.phase) * 2.4;
    ctx.beginPath();
    ctx.moveTo(x - 5, y + flap);
    ctx.quadraticCurveTo(x - 2, y - 2.4, x, y);
    ctx.quadraticCurveTo(x + 2, y - 2.4, x + 5, y + flap);
    ctx.stroke();
  }
}

function nightTint(ctx, w, h, hourOfDay) {
  let a = 0;
  if (hourOfDay >= 20 || hourOfDay < 5) a = 0.40;
  else if (hourOfDay >= 18) a = 0.40 * ((hourOfDay - 18) / 2);
  else if (hourOfDay < 7) a = 0.40 * ((7 - hourOfDay) / 2);
  if (a <= 0.01) return;
  ctx.fillStyle = `rgba(24,36,74,${a.toFixed(3)})`;
  ctx.fillRect(0, 0, w, h);
}
