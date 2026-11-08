/* ============================================================================
   KASUKABE TOWNSHIP — volumetric isometric renderer (v2)
   Frame locked to 2013 x 1089.

   Adapted from the supplied renderer so the simulation in township-v2.js can
   drive it. Three deliberate changes from the original single-file version:

     1. It no longer seeds or steps its own random walkers. Residents come from
        the simulation - real people with schedules, shifts and jobs - through
        Village.setAgents(). The wander model is kept only as a fallback when
        nothing has been supplied.
     2. It publishes a ZONES table and a HOMES list derived from the town plan,
        so the simulation can say "go to the hospital" or "go home" in the same
        coordinates the renderer draws in.
     3. Boot is explicit (Village.mount) rather than on load, because the page
        has to wire the simulation up first.
   ========================================================================= */

// Everything below runs inside an IIFE. Both scripts share one global scope,
// and the renderer and the simulation independently define names like SHIRT,
// PAL and shade - which is a hard SyntaxError at parse time, not a subtle bug.
// Only window.Village and the handful of compat globals at the bottom escape.
(function () {
// The drawing frame. It began as a fixed poster size and the page scaled that
// bitmap to fit, which is why "full screen" never actually filled anything:
// the town was rendered into a 2013x1089 rectangle and then letterboxed into
// whatever shape the window was. `Village.resize()` now sets it to the real
// viewport, so the map is drawn AT the size it is shown.
let FRAME_W = 2013, FRAME_H = 1089;
let sizedByHost = false;   // true once a host (the shell) owns the dimensions

/* --- 1. PALETTE ---------------------------------------------------------- */
const PAL = {
  skyTop:'#8DC2EC', skyBot:'#B9DEF7',
  soil:'#7A5B42', soilDark:'#5E4531', soilEdge:'#8A6A4C',
  grass:'#8BC75E', grassAlt:'#83BF57', grassDark:'#6FA847',
  park:'#7FBE52', pasture:'#9ACF6E',
  road:'#C9BFA8', roadAlt:'#C3B9A2', roadLine:'#EDE6D2', pave:'#D6CDB8',
  plaza:'#DECBA0', water:'#4E93C4', waterAlt:'#5CA0CE', waterDeep:'#3C7BA8',
  farm:'#9C7440', farmAlt:'#8E6938', orchardSoil:'#A88C5A',
  rock:'#8E8A82', rockAlt:'#7F7B73',
  sand:'#D9C79A',
  shadow:'rgba(38,30,22,0.22)',
  walls:['#E2CDB2','#D3B99C','#DCC6AC','#C9B196','#D8C3CE','#B6C9D2','#E0CBA6','#CBB79B','#C4A98D'],
  roofs:['#9A5B45','#7E5241','#8C6A4E','#A6614A','#6E5A4C','#8A4F42'],
  civic:'#DED8C8', civicRoof:'#7E8B93',
  window:'#5E7889', windowLit:'#F2C979',
  trunk:'#5C4531', leaf:'#4E8241', leafHi:'#5F9950', leafLo:'#3D6634',
  wood:'#8A6844', woodDark:'#6E5236',
  steel:'#9AA2A8', canvasA:'#D9584C', canvasB:'#EDE4D2'
};

/* --- 2. ISOMETRIC MATH --------------------------------------------------- */
const TW = 64, TH = 32, ZH = 30;

// --- town frame ----------------------------------------------------------
// CONTENT is what a district actually builds on and is unchanged, so every
// building keeps the size it had. VERGE is new: green space laid between a
// district and the carriageway either side of it. That is what pushes the
// blocks apart and gives the town its room to breathe, without redrawing a
// single building.
const ROAD_W = 2, CONTENT = 6, VERGE = 6;
const PITCH  = ROAD_W + CONTENT + VERGE;            // 14 tiles per district
// The margin of open land between the built town and the shore, on the near
// side. Everything outside the town derives from it, so widening it widens the
// island rather than pushing the town off the edge of it.
const CORE0  = 36;                                  // was 12: three times the belt
const BANDS  = [0,1,2,3,4,5].map(i => CORE0 + i*PITCH);
const CORE1  = BANDS[BANDS.length - 1] + ROAD_W;    // the built edge
const BLOCK_XY = [0,1,2,3,4].map(i => BANDS[i] + ROAD_W + VERGE/2);
const span = n => (n - 1) * PITCH + CONTENT;        // width of n districts
// Equal margins, so the town sits in the middle of its island instead of up in
// one corner. The far side still has to carry the orchard (CORE1+2..+10) and
// the river (G-7) before the woodland starts, and 36 leaves room for both.
const G = CORE1 + 36, PLINTH = 2.2;
const iso = (x, y, z) => ({ x:(x-y)*TW/2, y:(x+y)*TH/2 - (z||0)*ZH });

/* --- 3. TERRAIN ---------------------------------------------------------- */
const T = { GRASS:1, ROAD:2, PAVE:3, PLAZA:4, WATER:5, FARM:6, ROCK:7, PARK:8,
            PASTURE:9, ORCHARD:10, SAND:11, DEEP:12 };
const terrain = new Uint8Array(G*G).fill(T.GRASS);
const tAt  = (x,y) => (x<0||y<0||x>=G||y>=G) ? 0 : terrain[y*G+x];
const tSet = (x,y,v) => { if(x>=0&&y>=0&&x<G&&y<G) terrain[y*G+x]=v; };
const fill = (x0,y0,x1,y1,v) => { for(let y=y0;y<y1;y++) for(let x=x0;x<x1;x++) tSet(x,y,v); };

const inBand = v => BANDS.some(b => v>=b && v<b+2);

for (let y=0;y<G;y++) for (let x=0;x<G;x++) {
  if ((inBand(x)&&y>=CORE0&&y<CORE1) || (inBand(y)&&x>=CORE0&&x<CORE1)) tSet(x,y,T.ROAD);
}
const pq = [];
for (let y=0;y<G;y++) for (let x=0;x<G;x++) {
  if (tAt(x,y)===T.ROAD) continue;
  if (x<CORE0-1||y<CORE0-1||x>CORE1||y>CORE1) continue;
  if (tAt(x+1,y)===T.ROAD||tAt(x-1,y)===T.ROAD||tAt(x,y+1)===T.ROAD||tAt(x,y-1)===T.ROAD) pq.push([x,y]);
}
pq.forEach(([x,y]) => tSet(x,y,T.PAVE));

// Outlying land is stated once as rectangles and reused for the terrain, the
// scenery and the zone table, so the label, the ground and the place the
// simulation sends people to can never disagree.
const FARM_R = { x:CORE0-10, y:CORE0+20, w:8,  h:22 };
const PAST_R = { x:CORE0-10, y:CORE0+44, w:8,  h:18 };
const FORW_R = { x:CORE0-10, y:CORE0-8,  w:8,  h:24 };
const ORE_R  = { x:CORE0+4,  y:CORE1+3,  w:14, h:10 };
const FORS_R = { x:CORE0+22, y:CORE1+3,  w:22, h:12 };
const FORE_R = { x:CORE0+50, y:CORE1+4,  w:16, h:10 };
const ORCH_R = { x:CORE1+2,  y:CORE0-2,  w:8,  h:34 };
const NFLD_R = { x:CORE0+18, y:CORE0-9,  w:26, h:7  };
const fillR = (r,v) => fill(r.x, r.y, r.x+r.w, r.y+r.h, v);
fillR(FARM_R, T.FARM);
fillR(PAST_R, T.PASTURE);
fillR(ORE_R,  T.ROCK);
fillR(ORCH_R, T.ORCHARD);
fillR(NFLD_R, T.FARM);

const rng = (() => { let s = 20260801;
  return () => (s = (s*1103515245 + 12345) & 0x7fffffff) / 0x7fffffff; })();
const choose = a => a[Math.floor(rng()*a.length) % a.length];
const hsh = (x,y,k) => { const n = Math.sin(x*127.1 + y*311.7 + (k||0)*74.7)*43758.5453;
  return n - Math.floor(n); };

// In v2.0 the river drifted west as it ran south and took the corner off the
// last two districts - which is how the traffic ended up in the water. It now
// holds a channel east of CORE1 for every y on the map.
const RIVER_X = G - 7;
const riverAt = y => RIVER_X + Math.sin(y*0.085)*2.2 - (y-8)*0.010;
// From y=0, not y=8. Starting at 8 left the first eight rows dry, and because
// those rows are the map's EAST CORNER in the isometric view, the river visibly
// stopped short of the corner and started again below it - a break in the water
// exactly where the eye follows the shoreline to the edge of the world.
//
// The east bank is the map edge. The channel used to be symmetric and ~7 tiles
// wide, which left a strip of grass - and then woodland - BEYOND the water, so
// the river read as a canal with a park on the far side rather than as the
// edge of the land. It now runs off the eastern edge of the map.
//
// The WEST bank is deliberately unchanged. Everything the quay is made of -
// the piers at cx-4.4, the boats, the crates, the two sheds at cx-9.4 - is
// west of the centre line, and widening that way would have moored the boats
// on dry land and put the piers under water.
for (let y=0;y<G;y++){
  const cx = riverAt(y);
  tSet(Math.round(cx-3.8),y,T.SAND);            // the west beach, as before
  for (let x=Math.floor(cx-3.2); x<G; x++){
    tSet(x, y, Math.abs(x-cx) <= 2.1 ? T.DEEP : T.WATER);
  }
}
// The east-west stream used to run along CORE1+6, which is straight through
// the wood. Removed: the river down the east side is the town's water.

/* --- 4. TOWN PLAN -------------------------------------------------------- */
const PLAN = [
  ['housingN','housingN','park',    'housingE','housingE'],
  ['school',  'hospital','library', 'housingE','housingE'],
  ['college', 'market',  'govt',    'bakecafe','housingE'],
  ['cottages','square',  'retail',  'dining',  'housingE'],
  ['constr',  'emerg',   'tradebank','innblk', 'housingE']
];

const buildings = [], props = [];

function add(bx,by,dx,dy,w,d,h,style,opt){
  const b = Object.assign({
    x:bx+dx, y:by+dy, w, d, h, style,
    wall: style==='civic' ? PAL.civic : choose(PAL.walls),
    roof: choose(PAL.roofs), label:null, icon:''
  }, opt||{});
  buildings.push(b); return b;
}
const tree = (x,y,s,kind) => props.push({kind:kind||'tree', x, y, s: s||(0.85+rng()*0.45)});
const lamp = (x,y) => props.push({kind:'lamp', x, y});
const post = (x,y) => props.push({kind:'post', x, y});
// A district tag. `zid` is what makes it sit on the district it names: the
// anchor is recomputed from the zone rect at draw time (see LABELS()), because
// hardcoded coordinates drift the moment a district changes size - HOUSING
// DISTRICT was pinned to the middle of its FIRST block while the district
// spans two, so its tag sat seven tiles off the place it labelled.
const label = (x,y,text,icon,z,zid) => props.push({kind:'label', x, y, text, icon, z:z||1.4, zid});

/** A row (or grid) of dwellings inside one district block.
 *
 * `span` is the depth actually available, and it matters: a single row was
 * hard-coded to 5.3 deep and three blocks place one at +3.2 inside a
 * 6-unit block, so the HOUSE ran 2.25 units past the block onto the verge,
 * the pavement and the kerb. Residents then slept in a bedroom that was
 * sitting on the road - which is exactly what it looked like. Callers that
 * start a terrace part-way down a block now say how much room is left.
 */
function terrace(bx,by,rows,cols,hbase,tag,span){
  const depth = span === undefined ? 5.3 : span;
  for (let r=0;r<rows;r++) for (let c=0;c<cols;c++){
    const dx = 0.25 + c*(5.5/cols), dy = 0.35 + r*(depth/rows);
    const w = 5.5/cols - 0.45, d = Math.max(0.9, depth/rows - 0.6);
    const b = add(bx,by,dx,dy,w,d, hbase + hsh(bx+c,by+r,1)*0.55, 'house');
    b.home = true;
    b.homeTag = tag || 'housing';
  }
}

function layBlock(bx,by,type){
  switch(type){
    case 'housingN':
      terrace(bx,by,2,3,1.05,'housing');
      tree(bx+5.7,by+2.8); tree(bx+0.1,by+5.7);
      break;
    case 'housingE':
      terrace(bx,by,2,3,1.0,'housingEast');
      tree(bx+5.7,by+0.3); tree(bx+0.2,by+5.6);
      break;
    case 'cottages':
      terrace(bx,by,2,2,0.95,'cottages');
      tree(bx+2.9,by+2.9,1.05); tree(bx+5.6,by+5.5);
      break;

    case 'park':
      fill(bx,by,bx+6,by+6,T.PARK);
      for (let i=0;i<18;i++) tree(bx+0.3+rng()*5.4, by+0.3+rng()*5.4);
      props.push({kind:'pond', x:bx+0.9, y:by+3.4, w:2.8, d:1.9});
      add(bx,by,3.7,0.7,1.7,1.7,0.95,'gazebo');
      props.push({kind:'bench', x:bx+2.2, y:by+5.2});
      label(bx+3, by+3, 'TOWN PARK', '🌳', 2.2, 'park');
      break;

    case 'school':
      add(bx,by,0.4,1.0,5.2,3.4,1.55,'civic',{label:'TOWN SCHOOL',icon:'🏫',zone:'school'});
      add(bx,by,0.8,4.8,3.2,1.0,0.7,'shed');
      tree(bx+5.6,by+5.3); tree(bx+0.2,by+5.4);
      break;
    case 'college':
      add(bx,by,0,0.9,6,3.9,2.05,'civic',{label:'OAK HILL COLLEGE',icon:'🎓',columns:true,zone:'college'});
      tree(bx+1,by+5.5); tree(bx+3,by+5.5); tree(bx+5,by+5.5);
      break;
    case 'hospital':
      add(bx,by,0.4,0.7,5.2,4.0,2.45,'civic',{label:'HOSPITAL',icon:'🏥',wall:'#EFEBE2',zone:'hospital'});
      add(bx,by,0.6,5.1,2.2,0.8,0.55,'shed');
      tree(bx+5.5,by+5.4);
      break;
    case 'library':
      add(bx,by,0.7,1.1,4.6,3.6,1.8,'civic',{label:'LIBRARY',icon:'📚',wall:'#D9CDB8',zone:'library'});
      tree(bx+5.5,by+0.4); tree(bx+0.3,by+5.4); tree(bx+5.5,by+5.4);
      break;
    case 'govt':
      add(bx,by,0,0.5,6,4.6,2.7,'civic',{label:'GOVERNMENT COMPLEX',icon:'🏛️',columns:true,zone:'government'});
      tree(bx+0.8,by+5.6); tree(bx+2.8,by+5.6); tree(bx+4.8,by+5.6);
      break;

    case 'market':
      fill(bx,by,bx+6,by+6,T.PLAZA);
      for (let i=0;i<3;i++) for (let j=0;j<2;j++)
        props.push({kind:'stall', x:bx+0.6+i*1.9, y:by+0.8+j*2.6,
                    col: j?PAL.canvasB:PAL.canvasA});
      add(bx,by,4.6,4.4,1.3,1.3,1.15,'shed');
      label(bx+3, by+3, 'MARKET ROW', '🏪', 1.9, 'marketrow');
      break;

    case 'square':
      fill(bx,by,bx+6,by+6,T.PLAZA);
      props.push({kind:'fountain', x:bx+2.2, y:by+2.2});
      [[0.5,0.5],[5.0,0.5],[0.5,5.0],[5.0,5.0]].forEach(([dx,dy])=>tree(bx+dx,by+dy,0.9));
      props.push({kind:'bench', x:bx+1.2, y:by+4.6});
      props.push({kind:'bench', x:bx+4.2, y:by+1.2});
      add(bx,by,4.3,3.7,1.4,1.4,3.6,'clock',{label:'TOWN SQUARE',icon:'⛲'});
      break;

    case 'retail':
      add(bx,by,0,0.6,4.0,2.5,1.6,'shop',{label:'KASUKABE GROCERY',icon:'🛒',zone:'grocery'});
      add(bx,by,0,3.7,3.1,2.2,1.4,'shop',{label:'KASUKABE PHARMACY',icon:'💊',wall:'#D8C3CE',zone:'pharmacy'});
      add(bx,by,4.4,3.3,1.5,2.5,1.1,'shed');
      break;
    case 'bakecafe':
      add(bx,by,0,0.6,3.0,2.4,1.4,'shop',{label:'BAKERY',icon:'🥖',wall:'#E0CBA6',zone:'bakery'});
      add(bx,by,3.4,0.6,2.5,2.4,1.35,'shop',{label:'KASUKABE CAFE',icon:'☕',zone:'cafe'});
      terrace(bx,by+3.2,1,3,1.0,'housingEast',CONTENT-3.2);
      break;
    case 'dining':
      add(bx,by,0,0.6,3.4,2.6,1.5,'shop',{label:'RESTAURANT',icon:'🍽️',zone:'restaurant'});
      terrace(bx,by+3.4,1,3,1.0,'housingEast',CONTENT-3.4);
      tree(bx+5.6,by+2.0);
      break;
    case 'emerg':
      add(bx,by,0,0.6,3.6,2.6,1.5,'shop',{label:'FIRE STATION',icon:'🚒',wall:'#D08A78',zone:'fire'});
      add(bx,by,0,3.8,3.6,2.2,1.45,'shop',{label:'POLICE STATION',icon:'🚓',wall:'#AFC0D2',zone:'police'});
      add(bx,by,4.4,1.4,1.5,3.0,1.0,'shed');
      break;
    case 'tradebank':
      add(bx,by,0,0.6,4.2,2.8,2.0,'shop',{label:'TRADE OFFICES',icon:'🏢',zone:'tradeoffices'});
      add(bx,by,0,4.0,4.2,2.0,1.9,'civic',{label:'TOWN BANK',icon:'🏦',columns:true,zone:'bank'});
      tree(bx+5.5,by+2.2);
      break;
    case 'innblk':
      add(bx,by,0,0.6,3.6,2.8,1.7,'shop',{label:'THE INN',icon:'🛏️',wall:'#CBB79B',zone:'inn'});
      terrace(bx,by+3.8,1,3,1.0,'housingEast',CONTENT-3.8);
      break;

    case 'constr':
      fill(bx,by,bx+6,by+6,T.SAND);
      add(bx,by,0.3,0.6,3.2,2.6,1.5,'frame');
      add(bx,by,0.3,4.0,2.4,1.8,0.9,'frame');
      props.push({kind:'crane', x:bx+4.2, y:by+1.2});
      props.push({kind:'pile', x:bx+4.0, y:by+4.2});
      props.push({kind:'pile', x:bx+5.0, y:by+3.2});
      label(bx+3, by+3, 'CONSTRUCTION YARD', '🏗️', 2.4, 'construction');
      break;
  }
}
PLAN.forEach((row,j) => row.forEach((t,i) => layBlock(BLOCK_XY[i], BLOCK_XY[j], t)));

const scatter = (r,n,f) => { for(let i=0;i<n;i++) f(r.x+rng()*r.w, r.y+rng()*r.h); };
const mid = r => [r.x + r.w/2, r.y + r.h/2];

for (let y=ORCH_R.y+0.8; y<ORCH_R.y+ORCH_R.h-0.6; y+=1.8)
  for (let x=ORCH_R.x+0.8; x<ORCH_R.x+ORCH_R.w-0.6; x+=1.8) tree(x,y,0.72,'fruit');
label(...mid(ORCH_R), 'ORCHARD', '🍏', 2.0, 'orchard');

// Woodland density is per tile, so the wider map fills out rather than
// thinning the same tree count over a bigger area.
const wood = r => scatter(r, Math.round(r.w*r.h*0.62), (x,y)=>tree(x,y,0.8+rng()*0.5));
wood(FORS_R); wood(FORW_R); wood(FORE_R);
label(...mid(FORS_R), 'FOREST', '🌲', 2.0, 'forest');

add(0,0, FARM_R.x+4.2, FARM_R.y-2.6, 2.4, 1.8, 1.1, 'barn');
label(...mid(FARM_R), 'FARMLAND', '🌾', 1.6, 'farmland');

const P0=PAST_R, PX1=P0.x+P0.w-0.4, PY1=P0.y+P0.h-0.4;
for (let x=P0.x;x<PX1;x+=0.9) { post(x,P0.y); post(x,PY1); }
for (let y=P0.y;y<PY1;y+=0.9) { post(P0.x,y); post(PX1,y); }
add(0,0, P0.x+1.0, P0.y+5.0, 1.8, 1.4, 0.9, 'shed');
add(0,0, P0.x+4.6, P0.y+11.5, 1.6, 1.2, 0.85, 'shed');
label(...mid(PAST_R), 'PASTURE', '🐄', 1.6, 'pasture');

scatter({x:ORE_R.x+1,y:ORE_R.y+1,w:ORE_R.w-2,h:ORE_R.h-2}, 44,
        (x,y)=>props.push({kind:'rock', x, y, s:0.6+rng()*0.7}));
label(...mid(ORE_R), 'ORE OUTCROP', '⛏️', 1.8, 'oreoutcrop');

let DOCKS_R = null;
(function docks(){
  const y0 = Math.round(CORE0 + (CORE1-CORE0)*0.6), cx = riverAt(y0);
  for (let k=0;k<3;k++) props.push({kind:'pier', x:cx-4.4, y:y0+k*2.4, len:4.2});
  add(0,0, cx-6.6, y0-0.6, 2.0, 1.8, 1.2, 'shop', {wall:PAL.wood, roof:'#6E5236'});
  // Three boats moored off the piers, spaced a pier apart so each one reads
  // as tied up at its own berth rather than as a raft of hulls.
  props.push({kind:'boat', x:cx-0.6, y:y0+0.5});
  props.push({kind:'boat', x:cx-0.4, y:y0+3.1});
  props.push({kind:'boat', x:cx-0.7, y:y0+5.6});
  props.push({kind:'crate', x:cx-6.0, y:y0+3.4});
  props.push({kind:'crate', x:cx-5.2, y:y0+4.2});
  props.push({kind:'crate', x:cx-5.6, y:y0+6.0});
  // A working quay needs somewhere to put the catch. Two sheds, tagged to
  // the docks zone so the roof-off view furnishes them as a quay - and so a
  // dock worker has an interior to be inside rather than standing on gravel.
  add(0,0, cx-9.4, y0+1.2, 2.4, 2.0, 1.15, 'shop',
      {wall:'#CBB79B', roof:'#6E5236', zone:'docks'});
  add(0,0, cx-9.0, y0+4.6, 2.0, 1.8, 1.05, 'shop',
      {wall:PAL.wood, roof:'#6E5236'});
  label(cx-4, y0+2, 'RIVER DOCKS', '⚓', 2.0, 'docks');
  DOCKS_R = { x:Math.round(cx)-7, y:y0-1, w:6, h:9 };

  // The approach road. The quay sat twenty-one tiles from the nearest
  // walkable tile with nothing but grass in between, so every dock worker
  // crossed open country to get to work and the walk network could not route
  // anyone here at all. This is the lane that connects it to the town's
  // eastern carriageway, with a pavement either side like every other street.
  const laneY = y0 + 2;
  const townEdge = CORE1;                       // the last band's outer kerb
  const quayX = Math.round(cx) - 6;             // stop short of the piers
  for (let x = townEdge; x <= quayX; x++) {
    if (tAt(x, laneY) === T.WATER || tAt(x, laneY) === T.DEEP) continue;
    tSet(x, laneY, T.ROAD);
    tSet(x, laneY + 1, T.ROAD);
    for (const py of [laneY - 1, laneY + 2]) {
      if (tAt(x, py) === T.GRASS) tSet(x, py, T.PAVE);
    }
  }
  // And a short apron at the quay end so the lane arrives somewhere rather
  // than stopping dead at the last tile.
  for (let x = quayX; x <= quayX + 3; x++)
    for (let y = laneY - 1; y <= laneY + 2; y++)
      if (tAt(x, y) === T.GRASS) tSet(x, y, T.PAVE);
})();

label(BLOCK_XY[0]+3, BLOCK_XY[0]+3, 'HOUSING DISTRICT', '🏘️', 2.4, 'housing');
label(BLOCK_XY[0]+3, BLOCK_XY[3]+3, 'SOUTH COTTAGES', '🏠', 2.2, 'housingSouth');

// The verge. VERGE tiles of grass sit between every district and the
// carriageway either side of it, and they were bare - which is what made the
// gap between a building and the road read as a margin rather than a street.
// Street trees down the kerb line, flowering bushes against the block, placed
// off the terrain so nothing lands on tarmac, a drive or a building.
const FLOWERS = ['#E2607A','#E8B23C','#C86BC4','#EFEFEF','#E8724A','#7FA8E0'];
(function planTheVerges(){
  const free = (x,y) => {
    if (tAt(Math.floor(x), Math.floor(y)) !== T.GRASS) return false;
    return !buildings.some(b =>
      x >= b.x-0.5 && x <= b.x+b.w+0.5 && y >= b.y-0.5 && y <= b.y+b.d+0.5);
  };
  for (const band of BANDS){
    for (let v = CORE0; v < CORE1; v += 1.35){
      for (const off of [-1.5, ROAD_W + 1.5]){
        for (const [px,py] of [[band+off, v], [v, band+off]]){
          if (px < CORE0-2 || px > CORE1+2 || py < CORE0-2 || py > CORE1+2) continue;
          if (!free(px,py)) continue;
          const roll = hsh(Math.floor(px), Math.floor(py), 7);
          if (roll < 0.34) tree(px, py, 0.62 + roll*0.3);
          else if (roll < 0.72) props.push({ kind:'bush', x:px, y:py,
            flower: FLOWERS[Math.floor(roll*97) % FLOWERS.length] });
        }
      }
    }
  }
})();

// Countryside. The land outside the town is not lawn: every leftover grass
// tile beyond the built edge is fair game for woodland, which frames the
// island and fills the margins the wider frame opened up. Clumped off a hash
// rather than scattered evenly, so it reads as copses and not as an orchard.
for (let i=0;i<3200;i++){
  const x = 1.2 + rng()*(G-2.4), y = 1.2 + rng()*(G-2.4);
  if (x>CORE0-3 && x<CORE1+2 && y>CORE0-3 && y<CORE1+2) continue;
  const xi=Math.floor(x), yi=Math.floor(y);
  if (tAt(xi,yi) !== T.GRASS) continue;
  if (hsh(xi,yi,3) < 0.52) continue;
  tree(x, y, 0.72+rng()*0.55);
}

BANDS.forEach((b,bi) => { for (let v=CORE0+5; v<CORE1; v+=17){
  lamp(b-0.4,v); lamp(b+2.4,v);
  const w = v + 8.5; if (w < CORE1){ lamp(w,b-0.4); lamp(w,b+2.4); }
}});

/* ============================================================================
   4b. ZONES AND HOMES — the surface the simulation addresses
   The simulation talks in zone ids ("hospital", "farmland", "housing"). Those
   are derived here from the plan that was just laid, so the two can never
   drift apart: a building moves, its zone moves with it.
   ========================================================================= */
const B = (i) => BLOCK_XY[i];
const blockRect = (i,j) => ({ x:B(i), y:B(j), w:CONTENT, h:CONTENT });

const ZONES = {};
function zone(id, label, icon, rect) { ZONES[id] = Object.assign({ label, icon }, rect); }

// districts that occupy a whole block (or several)
zone('housing',      'Housing District',   '🏘️', { x:B(0), y:B(0), w:span(2), h:CONTENT });
zone('park',         'Town Park',          '🌳', blockRect(2,0));
zone('housingEast',  'East Terraces',      '🏡', { x:B(3), y:B(0), w:span(2), h:span(2) });
zone('school',       'Town School',        '🏫', blockRect(0,1));
zone('library',      'Library',            '📚', blockRect(2,1));
zone('college',      'Oak Hill College',   '🎓', blockRect(0,2));
zone('marketrow',    'Market Row',         '🏪', blockRect(1,2));
zone('government',   'Government Complex', '🏛️', blockRect(2,2));
zone('housingSouth', 'South Cottages',     '🏠', blockRect(0,3));
zone('townsquare',   'Town Square',        '⛲', blockRect(1,3));
// The temple, added after a run reported that agents talked about it and
// the map drew nothing. The world has carried it as a real sector the whole
// time; this file and village.js are what did not. See village.js's own
// note for the DESIGN sections that name it a genesis building.
zone('temple',       'Kasukabe Temple',    '⛩️', blockRect(2,3));
zone('construction', 'Construction Yard',  '🏗️', blockRect(0,4));
// outlying land
zone('farmland',     'Farmland',           '🌾', FARM_R);
zone('pasture',      'Pasture',            '🐄', PAST_R);
zone('oreoutcrop',   'Ore Outcrop',        '⛏️', ORE_R);
zone('forest',       'Forest',             '🌲', FORS_R);
zone('orchard',      'Orchard',            '🍏', ORCH_R);
zone('docks',        'River Docks',        '⚓', DOCKS_R);

// buildings that carry an explicit zone tag become zones at their own footprint
for (const b of buildings) {
  if (!b.zone) continue;
  // Re-homing a zone onto a building's footprint must not erase the district's
  // name. The dock warehouse carries zone:'docks' and no label of its own, and
  // blanking it here dropped "River Docks" out of the map search altogether.
  const prev = ZONES[b.zone];
  zone(b.zone, b.label || (prev && prev.label), b.icon || (prev && prev.icon),
       { x:b.x, y:b.y, w:b.w, h:b.d });
  b.zoneId = b.zone;
}

// every dwelling, for the simulation to hand out as addresses
const HOMES = buildings.filter(b => b.home);

/* ---- two fenced properties out by the woodland --------------------------
 * The land outside the village is not anonymous ground: some of it is the
 * town's and some of it belongs to somebody. Two plots say so out loud - a
 * post-and-rail fence, a board on the road, and a lane in from the nearest
 * street so each one can actually be reached on foot.
 *
 * The private plot is named after a real resident, picked from the cast at
 * build time rather than invented, so the sign matches somebody you can click
 * on the map.
 */
const PLOTS = [];
(function fencedPlots(){
  const GATE = 2;                     // tiles of opening, wide enough to drive through

  /** A fenced plot with a gate, and a board beside the gate.
   *
   *  `gateSide` is the edge the lane arrives at, and `gateAt` how far along
   *  that edge the opening starts. The rail is drawn as two runs either side
   *  of it: a fence with no way in is a pen, and the lane would end at a rail.
   *  Returns where the gate is, so the lane can be run to it. */
  const mkPlot = (x, y, w, h, text, tone, gateSide, gateAt) => {
    /** Take out whatever the scatter passes already planted here.
     *
     *  The copses go down hundreds of lines before this runs, so keeping the
     *  woodland BELT off the plot is not enough on its own: the first version
     *  fenced a rectangle that already had a wood in it, with one tree standing
     *  squarely in front of the government board. A field is a field because
     *  nothing is growing on it. */
    const clear = (x0, y0, x1, y1) => {
      for (let i = props.length - 1; i >= 0; i--) {
        const o = props[i];
        if (o.kind !== 'tree' && o.kind !== 'bush' && o.kind !== 'rock') continue;
        if (o.x >= x0 && o.x <= x1 && o.y >= y0 && o.y <= y1) props.splice(i, 1);
      }
    };
    clear(x - 1, y - 1, x + w + 1, y + h + 1);

    const run = (px, py, len, axis) => {
      if (len > 0.2) props.push({ kind:'fence', x:px, y:py, len, axis });
    };
    // north and south rails, broken by the gate on whichever is the gate side
    for (const side of ['north', 'south']) {
      const yy = side === 'north' ? y : y + h;
      if (gateSide === side) {
        run(x, yy, gateAt, 'x');
        run(x + gateAt + GATE, yy, w - gateAt - GATE, 'x');
      } else run(x, yy, w, 'x');
    }
    // and the two sides, as runs rather than a dotted line of posts
    run(x, y, h, 'y');
    run(x + w, y, h, 'y');

    // The board stands OUTSIDE the fence beside the gate, facing whoever walks
    // up the lane - not on the rail, where it read as part of the fence.
    const gateX = x + gateAt + GATE / 2;
    const sx = gateX + 1.1;
    const sy = gateSide === 'north' ? y - 1.5 : y + h + 1.5;
    clear(sx - 1.6, sy - 1.6, sx + 2.6, sy + 1.6);
    props.push({ kind:'sign', x: sx, y: sy, tone });
    label(sx + 0.7, sy + 0.1, text, '', 2.4);
    PLOTS.push({ x, y, w, h, text, gate:[gateX, gateSide === 'north' ? y : y + h] });
    return { gateX, gateY: gateSide === 'north' ? y - 0.5 : y + h + 0.5 };
  };

  // The government's ground: the far side of the northern approach. Its gate
  // faces the town, which is south of it.
  const gx = CORE0 + 8, gy = CORE0 - 16;
  const govGate = mkPlot(gx, gy, 9, 6, 'PROPERTY OF THE TOWN OF KASUKABE',
                         '#C9A56B', 'south', 3);

  // And one villager's own land, on the river meadow just south of the docks -
  // the ground between the town's eastern kerb and the water, which is where a
  // riverside holding would actually be. The name comes from the cast, so the
  // board and the resident file agree. Its gate faces north, towards the quay
  // and the approach lane that serves it.
  const owner = (typeof AGENTS !== 'undefined' && AGENTS.length)
    ? AGENTS[Math.floor(rng() * Math.min(60, AGENTS.length))] : null;
  const ownerName = owner ? owner.n : 'Ada Gray';
  // Clear of the quay to the north and of the water to the east: DOCKS_R ends
  // at y0+8, and the river's west beach is at riverAt(y)-3.8 ~ 133.
  const px0 = CORE1 + 8, py0 = DOCKS_R ? DOCKS_R.y + DOCKS_R.h + 4 : CORE1 + 9;
  const privGate = mkPlot(px0, py0, 8, 5, ownerName.toUpperCase() + " \u2014 PRIVATE LAND",
                          '#B9C7A0', 'north', 3);

  // A lane to each gate, so neither plot is reachable only across country.
  const laneTo = (fx, fy) => {
    const stepX = fx < CORE0 ? 1 : fx > CORE1 ? -1 : 0;
    const stepY = fy < CORE0 ? 1 : fy > CORE1 ? -1 : 0;
    let x = Math.round(fx), y = Math.round(fy), guard = 0;
    while (guard++ < 90) {
      const t = tAt(x, y);
      if (t === T.ROAD || t === T.PAVE) break;
      if (t === T.GRASS) tSet(x, y, T.PAVE);
      if (stepY && (y <= CORE0 || y >= CORE1 ? true : false)) y += stepY;
      else if (stepX) x += stepX;
      else y += stepY || 1;
      if (x < 1 || y < 1 || x > G - 2 || y > G - 2) break;
    }
  };
  laneTo(govGate.gateX, govGate.gateY);
  laneTo(privGate.gateX, privGate.gateY);
})();

// The woodland fence is planted LAST, after the districts exist. It has to
// know where they are so it can keep clear of them, and ZONES is not built
// until a few lines above this - planting earlier threw "Cannot access
// 'ZONES' before initialization" on load.
// A dense belt of woodland right around the edge of the island, four tiles
// deep. It gives the town a boundary that reads as a boundary - the land does
// not simply stop at a grass hem - and it is the ground the village grows into
// later: clearing a strip of it is a visible act rather than building on
// nothing. Packed far tighter than the scattered copses above (every tile
// rather than half of them, with a second offset tree on most), so the two do
// not look like the same woodland at different densities.
// The fence of woodland around the village. It frames the town and gives it a
// boundary, and it is the ground the village grows into later - but it is a
// FENCE, not a wilderness the town is lost inside. Two things keep it that
// way, both learned by overdoing it:
//
//   THINNED. At full density the belt was a solid green mass; the outlying
//   workers - the herders, the miners, the dock hands - disappeared into it.
//   BELT_DENSITY drops roughly a fifth of it, and the second tree per tile
//   is rarer, so you can see through the canopy to the people.
//
//   HELD BACK. Trees now keep CLEARING tiles away from any district, so
//   nothing grows over the pasture fence, the ore face or the quay, and
//   whoever is working there stays visible.
// A FENCE, not a fill. At 36 tiles the woodland covered every scrap of ground
// outside the town and the village read as lost in a wilderness - and the
// people working the outlying districts vanished into the canopy. The band now
// hugs the shore and leaves open country between itself and the village, which
// is what a fence around a village actually looks like from above.
const EDGE_BELT = 6;             // a fence of trees, not a forest to get lost in
const BELT_DENSITY = 0.50;      // keep half the tiles - you can see between them
const CLEARING = 3;             // tiles of open ground around every district

/** Is this tile within `pad` of any district? Woodland stops here, so the
 *  people working a district are never buried in canopy. The named woodland
 *  zones are skipped - a forest is allowed to have trees in it. */
const NOT_A_CLEARING = new Set(['forest', 'forestWest', 'forestEast']);
function nearAnyZone(x, y, pad) {
  for (const id in ZONES) {
    if (NOT_A_CLEARING.has(id)) continue;
    const z = ZONES[id];
    if (x >= z.x - pad && x <= z.x + z.w + pad &&
        y >= z.y - pad && y <= z.y + z.h + pad) return true;
  }
  return false;
}
for (let y = 0; y < G; y++) {
  for (let x = 0; x < G; x++) {
    const d = Math.min(x, y, G - 1 - x, G - 1 - y);
    if (d >= EDGE_BELT) continue;
    // Never inside the built town. Without this a sixteen-tile belt reaches
    // past CORE0 and plants woodland down the western carriageway.
    if (x > CORE0 - 3 && x < CORE1 + 2 && y > CORE0 - 3 && y < CORE1 + 2) continue;
    if (tAt(x, y) !== T.GRASS) continue;          // never over water, road or field
    if (hsh(x, y, 17) > BELT_DENSITY) continue;  // thinned, so you can see through it
    if (nearAnyZone(x, y, CLEARING)) continue;   // never crowd a working district
    // Nor inside a fenced plot, nor over its board. Somebody's field with a
    // wood growing in it is not a field, and the first pass put a tree
    // squarely in front of the government sign.
    if (PLOTS.some(q => x >= q.x - 2 && x <= q.x + q.w + 2 &&
                        y >= q.y - 2 && y <= q.y + q.h + 3)) continue;
    // Thin the innermost row further so the belt has a ragged edge, not a wall.
    if (d === EDGE_BELT - 1 && hsh(x, y, 11) < 0.42) continue;
    // A clearing here and there, with flowers in it rather than another tree.
    // A wood that is only trunks reads as wallpaper.
    if (hsh(x, y, 23) > 0.955) {
      props.push({ kind:'bush', x: x + 0.3 + rng() * 0.4, y: y + 0.3 + rng() * 0.4,
                   flower: FLOWERS[Math.floor(hsh(x, y, 29) * FLOWERS.length) % FLOWERS.length],
                   farField: true });
      continue;
    }
    tree(x + 0.25 + rng() * 0.5, y + 0.25 + rng() * 0.5, 0.95 + rng() * 0.6);
    props[props.length - 1].farField = true;
    if (hsh(x, y, 13) > 0.82) {                  // a second tree is rare
      tree(x + 0.15 + rng() * 0.7, y + 0.15 + rng() * 0.7, 0.75 + rng() * 0.45);
      props[props.length - 1].farField = true;
    }
  }
}

// Street lighting on both kerbs, but staggered between the two axes and set a
// long stride apart. At one lamp every nine tiles on every band the town read
// as a lamp farm - you could not see the buildings for the posts.


// tile -> building, so "is this person indoors, and whose roof are they under"
// is a lookup rather than 95 rectangle tests per resident per frame
const bIdx = new Int16Array(G*G).fill(-1);
buildings.forEach((b,i) => {
  for (let y=Math.floor(b.y); y<Math.ceil(b.y+b.d); y++)
    for (let x=Math.floor(b.x); x<Math.ceil(b.x+b.w); x++)
      if (x>=0 && y>=0 && x<G && y<G) bIdx[y*G+x] = i;
});
function buildingAt(x,y){
  const xi=Math.floor(x), yi=Math.floor(y);
  if (xi<0||yi<0||xi>=G||yi>=G) return null;
  const i = bIdx[yi*G+xi];
  return i<0 ? null : buildings[i];
}

/* --- 5. SOLIDS ----------------------------------------------------------- */
const LIT=1.0, SIDE_A=0.82, SIDE_B=0.60;
function shade(hex,k){
  if (hex[0] !== '#') return hex;
  const n = parseInt(hex.slice(1),16);
  return `rgb(${Math.min(255,Math.round(((n>>16)&255)*k))},${Math.min(255,Math.round(((n>>8)&255)*k))},${Math.min(255,Math.round((n&255)*k))})`;
}
function poly(ctx,pts,f){
  ctx.beginPath(); ctx.moveTo(pts[0].x,pts[0].y);
  for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i].x,pts[i].y);
  ctx.closePath(); ctx.fillStyle=f; ctx.fill();
}
function prism(ctx,x,y,z,w,d,h,c){
  const P=iso;
  poly(ctx,[P(x,y+d,z+h),P(x+w,y+d,z+h),P(x+w,y+d,z),P(x,y+d,z)],shade(c,SIDE_B));
  poly(ctx,[P(x+w,y,z+h),P(x+w,y+d,z+h),P(x+w,y+d,z),P(x+w,y,z)],shade(c,SIDE_A));
  poly(ctx,[P(x,y,z+h),P(x+w,y,z+h),P(x+w,y+d,z+h),P(x,y+d,z+h)],shade(c,LIT));
}
function faceRect(ctx,face,o,u0,u1,v0,v1,c){
  const m=(u,v)=> face==='x' ? iso(o.x+o.w,o.y+u,o.z+v) : iso(o.x+u,o.y+o.d,o.z+v);
  poly(ctx,[m(u0,v1),m(u1,v1),m(u1,v0),m(u0,v0)],c);
}
function windows(ctx,o,rows,cols,c){
  const gv=o.h/rows, gux=o.d/cols, guy=o.w/cols;
  for(let r=0;r<rows;r++){
    const v0=0.24+r*gv, v1=v0+gv*0.42;
    if(v1>o.h-0.12) continue;
    for(let k=0;k<cols;k++){
      const ax=0.24+k*gux, ay=0.24+k*guy;
      if(ax+gux*0.5<o.d-0.12) faceRect(ctx,'x',o,ax,ax+gux*0.5,v0,v1,shade(c,SIDE_A));
      if(ay+guy*0.5<o.w-0.12) faceRect(ctx,'y',o,ay,ay+guy*0.5,v0,v1,shade(c,SIDE_B));
    }
  }
}
function hipRoof(ctx,w,d,h,rh,col,e){
  e = e===undefined ? 0.18 : e;
  const cx=w/2, cy=d/2, top=h+rh;
  poly(ctx,[iso(-e,d+e,h),iso(w+e,d+e,h),iso(cx,cy,top)],shade(col,SIDE_B));
  poly(ctx,[iso(w+e,-e,h),iso(w+e,d+e,h),iso(cx,cy,top)],shade(col,LIT));
}

function drawBuilding(ctx,b){
  const o={x:0,y:0,z:0,w:b.w,d:b.d,h:b.h};
  switch(b.style){
    case 'house':
      prism(ctx,0,0,0,b.w,b.d,b.h,b.wall);
      windows(ctx,o,1,2,PAL.window);
      faceRect(ctx,'x',o,b.d/2-0.16,b.d/2+0.16,0,0.44,shade(b.roof,0.6));
      hipRoof(ctx,b.w,b.d,b.h,0.62,b.roof);
      prism(ctx,b.w*0.24,b.d*0.2,b.h+0.34,0.2,0.2,0.34,'#8A7A6E');
      break;
    case 'shop':
      prism(ctx,0,0,0,b.w,b.d,b.h,b.wall);
      windows(ctx,o,Math.max(1,Math.round(b.h/0.72)),Math.max(2,Math.round(b.w)),PAL.window);
      faceRect(ctx,'x',o,0.22,b.d-0.22,0.06,0.5,shade(b.roof,0.92));
      prism(ctx,-0.1,-0.1,b.h,b.w+0.2,b.d+0.2,0.16,shade(b.roof,1.0));
      prism(ctx,b.w*0.3,b.d*0.34,b.h+0.16,0.5,0.5,0.26,'#9A968C');
      break;
    case 'civic':
      prism(ctx,0,0,0,b.w,b.d,b.h,b.wall);
      windows(ctx,o,Math.max(2,Math.round(b.h/0.78)),Math.max(3,Math.round(b.w)),PAL.window);
      if(b.columns) for(let i=0.35;i<b.d-0.3;i+=0.66)
        prism(ctx,b.w,i,0,0.17,0.17,b.h*0.88,shade(b.wall,1.08));
      prism(ctx,-0.14,-0.14,b.h,b.w+0.28,b.d+0.28,0.2,PAL.civicRoof);
      prism(ctx,b.w/2-0.6,b.d/2-0.6,b.h+0.2,1.2,1.2,0.42,shade(b.wall,1.03));
      hipRoof(ctx,1.2,1.2,b.h+0.62,0.5,PAL.civicRoof,0.1);
      break;
    case 'clock':
      prism(ctx,0,0,0,b.w,b.d,b.h*0.8,b.wall||PAL.civic);
      windows(ctx,o,5,2,PAL.window);
      prism(ctx,-0.16,-0.16,b.h*0.8,b.w+0.32,b.d+0.32,0.18,PAL.civicRoof);
      faceRect(ctx,'x',{x:0,y:0,z:0,w:b.w,d:b.d,h:b.h},
               b.d/2-0.3,b.d/2+0.3,b.h*0.55,b.h*0.68,'#F4EDDC');
      hipRoof(ctx,b.w,b.d,b.h*0.98,0.9,b.roof,0.16);
      break;
    case 'barn':
      prism(ctx,0,0,0,b.w,b.d,b.h,'#B0503F');
      hipRoof(ctx,b.w,b.d,b.h,0.55,'#6E5236',0.2);
      faceRect(ctx,'x',{x:0,y:0,z:0,w:b.w,d:b.d,h:b.h},
               b.d*0.3,b.d*0.7,0,b.h*0.7,'#8A3F32');
      break;
    case 'gazebo': {
      for (const [px,py] of [[0.1,0.1],[b.w-0.26,0.1],[0.1,b.d-0.26],[b.w-0.26,b.d-0.26]])
        prism(ctx,px,py,0,0.16,0.16,b.h,'#8A7059');
      hipRoof(ctx,b.w,b.d,b.h,0.5,'#9A6B4F',0.22);
      break; }
    case 'frame': {
      prism(ctx,0,0,0,b.w,b.d,0.14,'#B8B2A4');
      for (let px=0.1; px<b.w; px+=Math.max(0.9,(b.w-0.2)/3))
        for (let py=0.1; py<b.d; py+=Math.max(0.9,(b.d-0.2)/2))
          prism(ctx,px,py,0.14,0.16,0.16,b.h,PAL.steel);
      prism(ctx,0.05,0.05,b.h*0.55,b.w-0.1,b.d-0.1,0.1,'#C6C0B2');
      prism(ctx,0.05,0.05,0.14,b.w*0.45,b.d-0.1,0.1,'#C6C0B2');
      break; }
    default:
      prism(ctx,0,0,0,b.w,b.d,b.h,b.wall);
      hipRoof(ctx,b.w,b.d,b.h,0.34,b.roof,0.14);
  }
}

/* --- 5b. ROOF OFF -------------------------------------------------------
   The same volume drawn as an open room: floor, knee walls and the furniture
   that says what the room is for. The two far walls stand full height because
   you look straight at their inside face; the two near ones are cut to a lip
   so they do not block the view in. People standing inside are sorted to draw
   over their own building (see renderTo), so you can watch them work.
   ========================================================================= */
const FLOOR = { house:'#EADCC4', shop:'#E4D8C2', civic:'#E9E5D9', clock:'#E9E5D9',
                barn:'#C7A87E', gazebo:'#D9C39F', frame:'#C6C0B2', shed:'#DCCFB6' };

/* Interiors are laid out the way v1 does them: the footprint is split into
   ROOMS, each room is given a PURPOSE, and each purpose is furnished with its
   own kit. That is the difference between a floor plan and a labelled box - a
   bedroom has a bed and a wardrobe, a kitchen has a counter and a stove, a
   ward has beds with monitors between them, a fire station has an engine in
   the bay and lockers next door. */
const ROOM_PLAN = {
  terrace:    ['bedroom','living','kitchen','bath'],
  bigterrace: ['bedroom','bedroom','living','kitchen','bath','study'],
  ward:       ['ward','ward','nurses','store'],
  hall:       ['classroom','classroom','reception','office'],
  cubicle:    ['office','office','meeting','reception'],
  shop:       ['shopfloor','shopfloor','checkout','store'],
  station:    ['lockers','bay','office','cells'],
  firehouse:  ['bay','lockers','office','store'],
  library:    ['stacks','stacks','reading','reception'],
  bankrooms:  ['tellers','vault','office','reception'],
  kitchenset: ['kitchen','dining','dining','store'],
  yard:       ['store','workshop','office','store'],
  quay:       ['dockstore','netloft','harbouroffice','dockstore'],
};
// [kind, along-x, along-y, height, colour] - tile units, sized for v2's
// footprints, which are a couple of tiles across rather than a dozen.
const KIT = {
  bedroom:   [['bed',0.30,0.44,0.13,null],['wardrobe',0.17,0.28,0.34,'#A9825A']],
  living:    [['sofa',0.42,0.19,0.14,null],['coffee',0.22,0.17,0.08,'#C89B62']],
  kitchen:   [['counter',0.44,0.15,0.15,'#C89B62'],['stove',0.17,0.15,0.14,'#4A5560']],
  bath:      [['bathtub',0.34,0.17,0.11,'#F4F7F8'],['toilet',0.13,0.13,0.13,'#FFFFFF']],
  ward:      [['bed',0.28,0.42,0.12,'#DCE9F2'],['monitor',0.12,0.12,0.16,'#8FA3B2']],
  nurses:    [['desk',0.38,0.19,0.13,'#C08E58'],['cabinet',0.15,0.14,0.22,'#DDE4E8']],
  classroom: [['desk',0.27,0.15,0.13,'#C08E58'],['chair',0.11,0.11,0.16,'#8C6239']],
  office:    [['workstation',0.36,0.20,0.13,'#C08E58'],['filing',0.15,0.17,0.24,'#9FB0BE']],
  study:     [['workstation',0.34,0.19,0.13,'#A9825A'],['bookshelf',0.15,0.40,0.30,'#8C6239']],
  meeting:   [['table',0.46,0.26,0.13,'#C89B62'],['chair',0.11,0.11,0.16,'#8C6239']],
  cells:     [['cell',0.30,0.30,0.36,'#94A3AE']],
  reception: [['reception',0.46,0.19,0.16,'#B98A56'],['plant',0.13,0.13,0.18,'#4F9E52']],
  shopfloor: [['shelf',0.15,0.44,0.26,'#A9825A'],['display',0.26,0.17,0.14,'#A9825A']],
  checkout:  [['till',0.21,0.15,0.14,'#B98A56'],['counter',0.40,0.14,0.15,'#C89B62']],
  store:     [['crate',0.17,0.17,0.13,'#C79A5F'],['shelf',0.14,0.34,0.26,'#A9825A']],
  workshop:  [['bench',0.34,0.15,0.09,'#9A6E42'],['tools',0.24,0.13,0.10,'#8FA0AE']],
  lockers:   [['locker',0.13,0.30,0.32,'#7A93AC']],
  bay:       [['engine',0.60,0.30,0.22,'#C6362A'],['hosereel',0.15,0.15,0.11,'#A82C22']],
  cells:     [['cell',0.30,0.30,0.36,'#94A3AE']],
  stacks:    [['bookshelf',0.15,0.56,0.34,'#8C6239']],
  reading:   [['table',0.40,0.22,0.13,'#C89B62'],['chair',0.11,0.11,0.16,'#8C6239']],
  tellers:   [['counter',0.54,0.17,0.16,'#C89B62'],['chair',0.11,0.11,0.16,'#8C6239']],
  vault:     [['vault',0.32,0.32,0.28,'#9B8A45'],['crate',0.15,0.15,0.12,'#B8A05A']],
  dockstore: [['barrel',0.17,0.17,0.20,'#8A6844'],['crate',0.17,0.17,0.13,'#C79A5F']],
  netloft:   [['net',0.34,0.20,0.10,'#9AA88C'],['barrel',0.17,0.17,0.20,'#7A5A3C']],
  harbouroffice: [['workstation',0.34,0.19,0.13,'#A9825A'],['crate',0.15,0.15,0.12,'#C79A5F']],
  dining:    [['table',0.23,0.23,0.13,'#C89B62'],['chair',0.11,0.11,0.16,'#8C6239']],
};
const BEDDING = ['#E4694E','#4E82C4','#59A86B','#D6A128','#8E6BC4','#DA7BA8','#3FA9A0'];
const SOFA    = ['#C4614E','#4F7FB5','#5C9E63','#9B6BB5','#C08552','#3FA9A0'];
const ROOMTINT = {
  bedroom:'#F1E4D3', living:'#F0E7D5', kitchen:'#E7EEEF', bath:'#E3EFF3',
  ward:'#E8F1F6', nurses:'#E8F1F6', classroom:'#F0EBD9', office:'#EDECE5',
  meeting:'#EDECE5', reception:'#F1ECDE', shopfloor:'#F2E8D5', checkout:'#F2E8D5',
  store:'#E9E3D4', workshop:'#E5E5DF', lockers:'#E5EBF0', bay:'#E0E4E7',
  cells:'#E0E4E7', stacks:'#F0E7D3', reading:'#F0E7D3', tellers:'#EFEBDB',
  vault:'#E7E3CF', dining:'#F2E9D7',
};

/** Which room list a building gets, from what it is FOR. */
function roomsKindOf(b){
  // A dwelling with room for it gets the fuller plan - two bedrooms, a
  // living room, a kitchen, a bath and a study. Handing every house the
  // same four rooms made a modern house look like a bedsit.
  if (b.home) return (b.w * b.d >= 5.0) ? 'bigterrace' : 'terrace';
  switch (b.zone){
    case 'hospital': case 'inn':            return 'ward';
    case 'school':   case 'college':        return 'hall';
    case 'library':                         return 'library';
    case 'bank':                            return 'bankrooms';
    case 'government': case 'tradeoffices': return 'cubicle';
    case 'police':                          return 'station';
    case 'fire':                            return 'firehouse';
    case 'cafe': case 'restaurant':         return 'kitchenset';
    case 'docks':                           return 'quay';
    case 'grocery': case 'pharmacy': case 'bakery': return 'shop';
  }
  return b.style === 'shop' ? 'shop' : 'yard';
}

/** Split the footprint into as many of the wanted rooms as will fit. */
function gridRooms(W, D, purposes){
  const n = purposes.length;
  let cols = Math.max(1, Math.round(Math.sqrt(n * (W / Math.max(0.01, D)))));
  let rows = Math.max(1, Math.ceil(n / cols));
  // A room under a third of a tile is a smudge, not a room - fold the plan
  // down until each one is big enough to read.
  while (cols > 1 && W / cols < 0.34) cols--;
  while (rows > 1 && D / rows < 0.34) rows--;
  const out = [];
  for (let i = 0; i < Math.min(n, cols * rows); i++){
    const c = i % cols, r = Math.floor(i / cols);
    const x = W * c / cols, y = D * r / rows;
    out.push({ x, y, w: W / cols, d: D / rows, purpose: purposes[i] });
  }
  return out;
}

/** Furnish one room, each kit item in its own strip so nothing overlaps. */
function furnishRoom(room, seed){
  const kit = KIT[room.purpose] || KIT.office;
  const out = [], m = 0.07;
  const W = room.w - m*2, D = room.d - m*2;
  if (W < 0.12 || D < 0.12) return out;
  let y0 = 0;
  kit.forEach(([kind, iw, id, ih, col], idx) => {
    const share = (idx === kit.length - 1) ? D - y0 : D / kit.length;
    // Furnished, not warehoused. Packing every piece the floor would take
    // filled a ward with thirty beds edge to edge; a room reads better with a
    // few well-spaced pieces and some floor showing between them.
    const gx = iw + 0.17, gy = id + 0.15;
    const cols = Math.min(3, Math.floor(W / gx));
    const rws  = Math.min(2, Math.floor(share / gy));
    if (cols >= 1 && rws >= 1){
      const ox = (W - (cols*gx - 0.17)) / 2;          // centred in the room
      const oy = (share - (rws*gy - 0.15)) / 2;
      for (let r = 0; r < rws; r++) for (let c = 0; c < cols; c++){
        let colour = col;
        if (kind === 'bed' && room.purpose === 'bedroom')
          colour = BEDDING[Math.floor(seeded(seed + c*7 + r*13) * BEDDING.length)];
        if (kind === 'sofa')
          colour = SOFA[Math.floor(seeded(seed + c*5 + r*11) * SOFA.length)];
        out.push({ k: kind, x: room.x + m + ox + c*gx, y: room.y + m + y0 + oy + r*gy,
                   w: iw, d: id, h: ih, c: colour || '#B98A56' });
      }
    }
    y0 += share;
  });
  return out;
}
function seeded(n){ const x = Math.sin(n * 127.1) * 43758.5453; return x - Math.floor(x); }

/** The rooms of one building, worked out once and kept on it. */
function roomsOf(b){
  if (b._rooms) return b._rooms;
  const m = 0.16;                                   // wall thickness inset
  const W = b.w - m*2, D = b.d - m*2;
  const plan = ROOM_PLAN[roomsKindOf(b)] || ROOM_PLAN.yard;
  const rooms = (W < 0.3 || D < 0.3) ? [] : gridRooms(W, D, plan);
  rooms.forEach((r, i) => {
    r.x += m; r.y += m;
    r.furniture = furnishRoom(r, Math.round((b.x + b.y) * 37) + i * 91);
  });
  return (b._rooms = rooms);
}

/** One piece of furniture, built from parts. A bed is a frame, a mattress, a
 *  blanket and a pillow against a headboard; a desk has legs and a chair
 *  pulled up to it; a bookcase has bands of books in it. At this size the
 *  silhouette is what tells you what a room is for. */
function drawFurn(ctx,f){
  const x=f.x, y=f.y, w=f.w, d=f.d, h=f.h, c=f.c, z=0.02;
  const P=(a,b2,zz,ww,dd,hh,col)=>prism(ctx,x+a,y+b2,z+zz,ww,dd,hh,col);
  switch(f.k){
    case 'bed':
      P(0,0,0,w,0.045,h*1.7,'#7A5A3C');                       // headboard
      P(0,0.04,0,w,d-0.04,h*0.5,'#8A6844');                   // frame
      P(0.015,0.06,h*0.42,w-0.03,d*0.30,h*0.55,'#FBFBF6');    // pillow
      P(0.015,0.06+d*0.30,h*0.42,w-0.03,d*0.58,h*0.5,c);      // blanket
      break;
    case 'wardrobe':
      P(0,0,0,w,d,h,c);
      P(w*0.46,-0.006,h*0.35,w*0.08,0.012,h*0.28,'#6E5236');
      break;
    case 'sofa':
      P(0,0,0,w,d,h*0.55,c);
      P(0,0,h*0.5,w,d*0.34,h*0.85,shade(c,0.86));
      P(0,0,0,w*0.16,d,h*0.8,shade(c,0.92));
      P(w*0.84,0,0,w*0.16,d,h*0.8,shade(c,0.92));
      break;
    case 'coffee': case 'table':
      P(0.03,0.03,0,0.04,0.04,h*0.72,shade(c,0.7));
      P(w-0.07,d-0.07,0,0.04,0.04,h*0.72,shade(c,0.7));
      P(0.02,0.02,h*0.72,w-0.04,d-0.04,h*0.28,c);
      break;
    case 'workstation':
      // A desk is a slab; a workstation is somebody's job. Legs, a top, a
      // laptop open on it and a chair pulled up - which is what makes an
      // office read as an office rather than as a room full of tables.
      P(0.02,0.02,0,0.05,0.05,h*0.66,shade(c,0.7));
      P(w-0.07,d-0.07,0,0.05,0.05,h*0.66,shade(c,0.7));
      P(0,0,h*0.66,w,d,h*0.30,c);
      P(w*0.30,d*0.22,h*0.96,w*0.40,d*0.34,0.02,'#8E959C');      // laptop base
      P(w*0.30,d*0.20,h*0.96,w*0.40,0.03,h*0.55,'#3A4048');      // screen
      P(w*0.31,d*0.21,h*0.99,w*0.38,0.012,h*0.46,'#7FB6D8');     // lit panel
      P(w*0.34,d+0.03,0,w*0.32,d*0.5,h*1.15,'#8C6239');          // chair
      break;
    case 'barrel':
      P(w*0.08,d*0.08,0,w*0.84,d*0.84,h,c);
      P(w*0.02,d*0.02,h*0.24,w*0.96,d*0.96,h*0.10,shade(c,0.78));  // hoops
      P(w*0.02,d*0.02,h*0.66,w*0.96,d*0.96,h*0.10,shade(c,0.78));
      break;
    case 'net':
      P(0,0,0,w,d,h*0.3,shade(c,0.9));
      for(let i=0;i<3;i++) P(0.02+i*(w-0.04)/3,0.02,h*0.28,(w-0.04)/3-0.02,d-0.04,h*0.5,c);
      break;
    case 'desk':
      P(0.02,0.02,0,0.05,0.05,h*0.68,shade(c,0.7));
      P(w-0.07,d-0.07,0,0.05,0.05,h*0.68,shade(c,0.7));
      P(0,0,h*0.68,w,d,h*0.32,c);
      P(w*0.30,d+0.03,0,w*0.34,d*0.5,h*1.2,'#8C6239');        // chair pulled up
      break;
    case 'chair':
      P(0,0,h*0.5,w,d,h*0.14,c);
      P(0,0,h*0.5,w,0.035,h*0.62,shade(c,0.86));
      break;
    case 'bookshelf': case 'shelf':
      P(0,0,0,w,d,h,shade(c,0.82));
      for(let i=0;i<3;i++)
        P(0.015, 0.02+(d-0.04)*i/3, h*(0.16+i*0.27), w-0.03, (d-0.04)/3-0.012, h*0.2,
          ['#C4443B','#3B6BC4','#3BA05C'][i]);
      break;
    case 'counter': case 'till': case 'reception':
      P(0,0,0,w,d,h*0.85,shade(c,0.9));
      P(-0.008,-0.008,h*0.82,w+0.016,d+0.016,h*0.2,shade(c,1.08));
      break;
    case 'stove':
      P(0,0,0,w,d,h,c);
      P(w*0.12,d*0.16,h,w*0.26,d*0.26,0.014,'#2E3440');
      P(w*0.58,d*0.16,h,w*0.26,d*0.26,0.014,'#2E3440');
      break;
    case 'fridge': case 'cabinet': case 'filing':
      P(0,0,0,w,d,h,c);
      P(0,-0.006,h*0.52,w,0.012,0.014,shade(c,0.62));
      break;
    case 'bathtub':
      P(0,0,0,w,d,h*0.55,'#DDE6EA');
      P(0.02,0.02,h*0.32,w-0.04,d-0.04,h*0.34,'#BFE0EE');
      break;
    case 'toilet':
      P(w*0.2,0,0,w*0.6,d*0.5,h,'#FFFFFF');
      P(0,d*0.5,0,w,d*0.5,h*0.6,'#FFFFFF');
      break;
    case 'monitor':
      P(w*0.35,d*0.35,0,w*0.3,d*0.3,h*0.55,'#9AA2A8');
      P(0,0,h*0.5,w,d*0.42,h*0.6,'#2E3440');
      break;
    case 'locker':
      P(0,0,0,w,d,h,c);
      P(0,-0.006,0,0.014,0.012,h,shade(c,0.6));
      P(0,-0.006,h*0.5,w,0.012,0.014,shade(c,0.6));
      break;
    case 'engine':
      P(0,0,0,w,d,h*0.55,c);
      P(w*0.04,0.02,h*0.5,w*0.42,d-0.04,h*0.5,shade(c,1.10));
      P(0,-0.008,h*0.24,w,0.014,h*0.16,'#F4F4EF');
      P(w*0.5,d*0.25,h*1.02,w*0.44,d*0.3,0.02,'#C9CDD2');
      break;
    case 'cell':
      P(0,0,0,w,d,h*0.14,shade(c,0.8));
      for(let i=0;i<4;i++) P(0.01+i*(w-0.02)/4,0,0,0.02,0.02,h,'#7C8590');
      P(0,d-0.035,0,w,0.035,h,shade(c,0.9));
      break;
    case 'vault':
      P(0,0,0,w,d,h,c);
      P(w*0.5-0.03,-0.008,h*0.45,0.06,0.014,0.06,'#5E5A3A');
      break;
    case 'plant':
      P(w*0.25,d*0.25,0,w*0.5,d*0.5,h*0.32,'#B0764A');
      P(0,0,h*0.3,w,d,h*0.78,c);
      break;
    case 'crate':
      P(0,0,0,w,d,h,c);
      P(0,-0.006,h*0.45,w,0.012,0.016,shade(c,0.72));
      break;
    case 'display':
      P(0,0,0,w,d,h*0.7,shade(c,0.9));
      P(0.01,0.01,h*0.7,w-0.02,d-0.02,h*0.3,'#E8DCC0');
      break;
    case 'bench': case 'tools':
      P(0,0,0,w,d,h*0.72,c);
      P(0,0,h*0.7,w,d,h*0.3,shade(c,1.06));
      break;
    default:
      P(0,0,0,w,d,h,c);
  }
}

function drawOpen(ctx,b){
  const fh=0.09, t=0.13;
  const wh=Math.min(0.50, Math.max(0.26, b.h*0.24));
  prism(ctx,0,0,0,b.w,b.d,fh, FLOOR[b.style]||'#E4D8C2');
  prism(ctx,0,0,fh,b.w,t,wh, b.wall);              // far walls, seen from inside
  prism(ctx,0,t,fh,t,b.d-t,wh, b.wall);
  // rooms back to front, each one floor, partitions, then its furniture
  for (const r of roomsOf(b).slice().sort((p,q)=>(p.x+p.y)-(q.x+q.y))){
    prism(ctx,r.x,r.y,fh,r.w,r.d,0.02, ROOMTINT[r.purpose] || '#F0E7D6');
    prism(ctx,r.x,r.y,fh+0.02,r.w,0.05,wh*0.52, shade(b.wall,0.88));
    prism(ctx,r.x,r.y,fh+0.02,0.05,r.d,wh*0.52, shade(b.wall,0.88));
    for (const f of r.furniture.slice().sort((p,q)=>(p.x+p.y)-(q.x+q.y)))
      drawFurn(ctx, f);
  }
  prism(ctx,0,b.d-t,fh,b.w,t,wh*0.34, shade(b.wall,0.94));   // near walls, a lip
  prism(ctx,b.w-t,0,fh,t,b.d-t,wh*0.34, shade(b.wall,0.94));
}

/* --- props --------------------------------------------------------------- */
function drawTree(ctx,s,fruit){
  prism(ctx,-0.07,-0.07,0,0.14,0.14,0.42*s,PAL.trunk);
  const c = iso(0,0,0.44*s), rx=21*s, ry=18*s;
  const base = fruit ? '#3E7038' : PAL.leafLo;
  const mid  = fruit ? '#4F8A45' : PAL.leaf;
  const hi   = fruit ? '#63A455' : PAL.leafHi;
  ctx.beginPath(); ctx.ellipse(c.x,c.y-ry*0.55,rx,ry,0,0,6.2832); ctx.fillStyle=base; ctx.fill();
  ctx.beginPath(); ctx.ellipse(c.x+rx*0.13,c.y-ry*0.8,rx*0.85,ry*0.85,0,0,6.2832); ctx.fillStyle=mid; ctx.fill();
  ctx.beginPath(); ctx.ellipse(c.x+rx*0.3,c.y-ry*1.04,rx*0.44,ry*0.44,0,0,6.2832); ctx.fillStyle=hi; ctx.fill();
  if (fruit){ ctx.fillStyle='#D0503E';
    for(let i=0;i<4;i++){ ctx.beginPath();
      ctx.arc(c.x+(i-1.5)*rx*0.4, c.y-ry*(0.5+0.2*(i%2)), 1.8*s, 0, 6.2832); ctx.fill(); } }
}
// A real street light: base, column, an arm reaching out over the kerb, a
// hood and a lit lamp under it - not a post with a lump on top.
const drawLamp = ctx => {
  prism(ctx,-0.09,-0.09,0,0.18,0.18,0.08,'#6E7883');
  prism(ctx,-0.04,-0.04,0.08,0.08,0.08,1.00,'#808B96');
  prism(ctx,-0.04,-0.04,1.05,0.34,0.08,0.07,'#808B96');
  prism(ctx, 0.22,-0.08,0.97,0.21,0.15,0.08,'#5D6873');
  prism(ctx, 0.24,-0.06,0.95,0.17,0.11,0.03,'#FFF0C0');
};

const drawBush = (ctx, flower) => {
  prism(ctx,-0.22,-0.22,0,0.44,0.44,0.30,'#4E8241');
  prism(ctx,-0.14,-0.14,0.28,0.28,0.28,0.18,'#5F9950');
  for (const [fx,fy] of [[-0.16,-0.05],[0.05,-0.14],[0.02,0.10],[-0.06,0.02]])
    prism(ctx,fx,fy,0.30,0.09,0.09,0.07,flower||'#E2607A');
};

/** Dogs, cats and squirrels. A town with nobody's dog in it reads as a model
 *  of a town. At this size the silhouette and the colour are the animal. */
function drawCritter(ctx,kind){
  if (kind === 'deer'){
    prism(ctx,0,0.07,0.16,0.46,0.20,0.24,'#A8794E');      // body
    prism(ctx,0.40,0.08,0.34,0.14,0.14,0.20,'#B4855A');   // neck
    prism(ctx,0.40,0.08,0.52,0.16,0.14,0.13,'#B4855A');   // head
    prism(ctx,0.44,0.10,0.63,0.04,0.04,0.13,'#6E5236');   // antlers
    prism(ctx,0.36,0.10,0.63,0.04,0.04,0.11,'#6E5236');
    [[0.04,0.05],[0.04,0.17],[0.34,0.05],[0.34,0.17]].forEach(([x,y])=>
      prism(ctx,x,y,0,0.06,0.06,0.18,'#8E6A45'));         // legs
    prism(ctx,-0.06,0.11,0.30,0.10,0.07,0.08,'#E8DCC8');  // tail
    return;
  }
  if (kind === 'rabbit'){
    prism(ctx,0,0.06,0,0.22,0.13,0.14,'#B9AA97');
    prism(ctx,0.18,0.07,0.10,0.11,0.11,0.12,'#C4B5A2');
    prism(ctx,0.20,0.08,0.21,0.04,0.04,0.12,'#C4B5A2');   // ears
    prism(ctx,0.15,0.08,0.21,0.04,0.04,0.12,'#C4B5A2');
    prism(ctx,-0.07,0.09,0.09,0.07,0.07,0.07,'#EFE7DA');  // scut
    return;
  }
  if (kind === 'fox'){
    prism(ctx,0,0.06,0.02,0.34,0.15,0.15,'#C2703A');
    prism(ctx,0.30,0.07,0.14,0.13,0.13,0.13,'#CE7E45');
    prism(ctx,0.32,0.09,0.26,0.04,0.04,0.07,'#8A4B26');   // ears
    prism(ctx,0.28,0.09,0.26,0.04,0.04,0.07,'#8A4B26');
    prism(ctx,-0.20,0.08,0.10,0.22,0.10,0.09,'#D98F5C');  // brush
    prism(ctx,-0.30,0.08,0.10,0.08,0.09,0.08,'#EFE7DA');
    return;
  }
  if (kind === 'squirrel'){
    prism(ctx,0,0.06,0,0.20,0.13,0.16,'#A9713F');
    prism(ctx,0.16,0.07,0.10,0.10,0.10,0.12,'#A9713F');
    prism(ctx,-0.06,0.07,0.10,0.09,0.09,0.30,'#C08752');
    return;
  }
  if (kind === 'cat'){
    prism(ctx,0,0.06,0,0.30,0.14,0.15,'#6E6660');
    prism(ctx,0.27,0.07,0.12,0.12,0.12,0.13,'#6E6660');
    prism(ctx,-0.14,0.09,0.13,0.16,0.07,0.06,'#7C736C');
    return;
  }
  prism(ctx,0,0.06,0,0.40,0.18,0.20,'#B08A5E');
  prism(ctx,0.36,0.07,0.16,0.16,0.16,0.16,'#B08A5E');
  prism(ctx,-0.12,0.11,0.22,0.14,0.08,0.07,'#9A7550');
  [[0.04,0.05],[0.04,0.19],[0.30,0.05],[0.30,0.19]].forEach(([x,y])=>
    prism(ctx,x,y,0,0.07,0.07,0.09,'#8E6A45'));
}
/** A run of post-and-rail fence, `len` tiles long, along x or along y.
 *
 *  The axis matters. The first version only ran along x, so the two sides of a
 *  plot were faked with one-tile stubs a tile apart - which draws as a dashed
 *  line of loose posts, not a fence. A fence with gaps in it is a fence nobody
 *  believes. */
const drawFence = (ctx, len, axis) => {
  const n = Math.max(1, Math.round(len));
  if (axis === 'y') {
    for (let i = 0; i <= n; i++) prism(ctx, -0.04, i - 0.04, 0, 0.09, 0.09, 0.46, PAL.wood);
    prism(ctx, -0.02, 0, 0.34, 0.05, n, 0.05, PAL.woodDark);   // top rail
    prism(ctx, -0.02, 0, 0.18, 0.05, n, 0.05, PAL.woodDark);   // lower rail
    return;
  }
  for (let i = 0; i <= n; i++) prism(ctx, i - 0.04, -0.04, 0, 0.09, 0.09, 0.46, PAL.wood);
  prism(ctx, 0, -0.02, 0.34, n, 0.05, 0.05, PAL.woodDark);
  prism(ctx, 0, -0.02, 0.18, n, 0.05, 0.05, PAL.woodDark);
};

/** A board on two posts. The words are drawn by drawLabels, not baked in -
 *  a sprite with text baked at one zoom is unreadable at every other. */
const drawSign = (ctx, tone) => {
  // Roughly a tile and a half across and head-height. The first board was
  // three quarters of a tile wide and knee-high, which at any sensible zoom
  // was a tan smudge on the rail rather than a sign.
  prism(ctx, 0.06, -0.05, 0, 0.11, 0.11, 0.98, PAL.wood);      // left post
  prism(ctx, 1.28, -0.05, 0, 0.11, 0.11, 0.98, PAL.wood);      // right post
  prism(ctx, -0.02, -0.04, 0.50, 1.48, 0.09, 0.54, tone || '#C9A56B');
  prism(ctx, -0.06, -0.06, 0.46, 1.56, 0.12, 0.08, PAL.woodDark);  // lower batten
  prism(ctx, -0.06, -0.06, 1.00, 1.56, 0.12, 0.08, PAL.woodDark);  // upper batten
};

const drawPost = ctx => { prism(ctx,-0.06,-0.06,0,0.12,0.12,0.42,PAL.wood);
  prism(ctx,-0.06,-0.06,0.26,0.9,0.05,0.05,PAL.woodDark); };
const drawBench = ctx => { prism(ctx,0,0,0.17,0.9,0.24,0.08,'#8A7059');
  prism(ctx,0,0.02,0,0.08,0.08,0.17,'#6A5646'); prism(ctx,0.82,0.02,0,0.08,0.08,0.17,'#6A5646'); };
function drawFountain(ctx){
  prism(ctx,0,0,0,1.7,1.7,0.24,'#D2C9B6');
  poly(ctx,[iso(0.22,0.22,0.26),iso(1.48,0.22,0.26),iso(1.48,1.48,0.26),iso(0.22,1.48,0.26)],PAL.water);
  prism(ctx,0.7,0.7,0.26,0.3,0.3,0.55,'#DED8C8');
}
function drawPond(ctx,w,d){
  poly(ctx,[iso(0,0,0.02),iso(w,0,0.02),iso(w,d,0.02),iso(0,d,0.02)],PAL.water);
  poly(ctx,[iso(0.32,0.32,0.03),iso(w-0.32,0.32,0.03),iso(w-0.32,d-0.32,0.03),iso(0.32,d-0.32,0.03)],PAL.waterAlt);
}
function drawStall(ctx,col){
  prism(ctx,0,0,0,1.3,1.1,0.55,PAL.wood);
  for (const [px,py] of [[0,0],[1.2,0],[0,1.0],[1.2,1.0]]) prism(ctx,px,py,0,0.1,0.1,1.15,PAL.woodDark);
  prism(ctx,-0.16,-0.16,1.15,1.62,1.42,0.14,col);
  prism(ctx,0.15,0.15,0.55,0.5,0.4,0.22,'#B8734A');
}
function drawCrane(ctx){
  for (const [px,py] of [[0,0],[0.5,0],[0,0.5],[0.5,0.5]]) prism(ctx,px,py,0,0.11,0.11,4.2,'#D8B24A');
  prism(ctx,-1.6,0.2,4.2,4.6,0.22,0.22,'#D8B24A');
  prism(ctx,2.2,0.16,3.1,0.1,0.1,1.1,'#8A8478');
  prism(ctx,2.1,0.05,2.75,0.32,0.32,0.34,'#8A8478');
}
function drawPile(ctx){
  prism(ctx,0,0,0,0.9,0.7,0.22,'#A88C5A');
  prism(ctx,0.12,0.1,0.22,0.66,0.5,0.2,'#B89A66');
  prism(ctx,0.24,0.2,0.42,0.42,0.3,0.18,'#C6A972');
}
function drawRock(ctx,s){
  prism(ctx,-0.3*s,-0.3*s,0,0.6*s,0.6*s,0.3*s,'#948E85');
  prism(ctx,-0.18*s,-0.18*s,0.3*s,0.36*s,0.36*s,0.24*s,'#A49E94');
}
function drawPier(ctx,len){
  prism(ctx,0,0,0.06,len,0.9,0.12,PAL.wood);
  for (let i=0.1;i<len;i+=0.9){ prism(ctx,i,0.05,-0.3,0.1,0.1,0.4,PAL.woodDark);
    prism(ctx,i,0.75,-0.3,0.1,0.1,0.4,PAL.woodDark); }
}
function drawBoat(ctx){
  prism(ctx,0,0,0.04,1.9,0.75,0.3,'#C9553F');
  prism(ctx,0.35,0.15,0.34,0.8,0.45,0.34,'#EDE4D2');
  prism(ctx,1.45,0.33,0.34,0.08,0.08,1.5,'#8A7059');
}
const drawCrate = ctx => { prism(ctx,0,0,0,0.55,0.55,0.5,'#B08A56');
  prism(ctx,0.05,0.05,0.5,0.45,0.45,0.06,'#8A6844'); };

/* --- 6. SPRITE BAKING ---------------------------------------------------- */
let SCALE = 0.5;

function bake(fn, wb, pad){
  const cs=[];
  for(const x of [wb.x0,wb.x1]) for(const y of [wb.y0,wb.y1]) for(const z of [wb.z0,wb.z1]) cs.push(iso(x,y,z));
  let a=Infinity,b=-Infinity,c=Infinity,d=-Infinity;
  for(const p of cs){ if(p.x<a)a=p.x; if(p.x>b)b=p.x; if(p.y<c)c=p.y; if(p.y>d)d=p.y; }
  pad = pad===undefined ? 4 : pad;
  a-=pad; b+=pad; c-=pad; d+=pad;
  const cv=document.createElement('canvas');
  cv.width=Math.max(1,Math.ceil((b-a)*SCALE));
  cv.height=Math.max(1,Math.ceil((d-c)*SCALE));
  const g=cv.getContext('2d');
  g.scale(SCALE,SCALE); g.translate(-a,-c);
  fn(g);
  return {c:cv, ox:a, oy:c};
}
let cache = {};
function cached(key, fn, wb, pad){
  if(!cache[key]) cache[key]=bake(fn,wb,pad);
  return cache[key];
}

/* --- 7. GROUND ----------------------------------------------------------- */
let ground=null;
// Scenery painted into the ground bake rather than drawn per frame. See the
// note where it is filled: it is the outer forest, and nothing walks there.
const farStatics=[];
const TFILL={
  [T.GRASS]:[PAL.grass,PAL.grassAlt], [T.ROAD]:[PAL.road,PAL.roadAlt],
  [T.PAVE]:[PAL.pave,PAL.pave], [T.PLAZA]:[PAL.plaza,PAL.plaza],
  [T.WATER]:[PAL.water,PAL.waterAlt], [T.DEEP]:[PAL.waterDeep,PAL.water],
  [T.FARM]:[PAL.farm,PAL.farmAlt], [T.ROCK]:[PAL.rock,PAL.rockAlt],
  [T.PARK]:[PAL.park,PAL.park], [T.PASTURE]:[PAL.pasture,PAL.pasture],
  [T.ORCHARD]:[PAL.orchardSoil,PAL.orchardSoil], [T.SAND]:[PAL.sand,PAL.sand]
};

// The ground is flat colour: it upscales without anyone noticing, so it bakes
// at its own ceiling rather than at SCALE. On the wider map that is the
// difference between a 33 MB bitmap and a 90 MB one.
const GROUND_MAX = 0.6;
function bakeGround(){
  const s=Math.min(SCALE,GROUND_MAX);
  const w=Math.ceil(G*TW*s), h=Math.ceil((G*TH+PLINTH*ZH+10)*s);
  const cv=document.createElement('canvas'); cv.width=w; cv.height=h;
  const g=cv.getContext('2d');
  const ox=G*TW/2, oy=0;
  g.scale(s,s); g.translate(ox,oy);

  const Bp=iso(G,0,0), C=iso(G,G,0), D=iso(0,G,0), drop=PLINTH*ZH;
  poly(g,[Bp,C,{x:C.x,y:C.y+drop},{x:Bp.x,y:Bp.y+drop}],PAL.soil);
  poly(g,[D,C,{x:C.x,y:C.y+drop},{x:D.x,y:D.y+drop}],PAL.soilDark);
  poly(g,[Bp,C,{x:C.x,y:C.y+8},{x:Bp.x,y:Bp.y+8}],PAL.soilEdge);
  poly(g,[D,C,{x:C.x,y:C.y+8},{x:D.x,y:D.y+8}],shade(PAL.soilEdge,0.82));

  for(let y=0;y<G;y++) for(let x=0;x<G;x++){
    const t=tAt(x,y), pr=TFILL[t]||TFILL[T.GRASS];
    poly(g,[iso(x,y,0),iso(x+1,y,0),iso(x+1,y+1,0),iso(x,y+1,0)],((x+y)&1)?pr[0]:pr[1]);
  }
  BANDS.forEach(bd=>{ for(let v=CORE0;v<CORE1;v+=1.8){
    if(inBand(v)) continue;
    poly(g,[iso(v,bd+1,.01),iso(v+.9,bd+1,.01),iso(v+.9,bd+1.07,.01),iso(v,bd+1.07,.01)],PAL.roadLine);
    poly(g,[iso(bd+1,v,.01),iso(bd+1,v+.9,.01),iso(bd+1.07,v+.9,.01),iso(bd+1.07,v,.01)],PAL.roadLine);
  }});
  const rows=(r,step,col,t)=>{ for(let y=r.y;y<r.y+r.h;y+=step)
    poly(g,[iso(r.x,y+.42,.01),iso(r.x+r.w,y+.42,.01),
            iso(r.x+r.w,y+.42+t,.01),iso(r.x,y+.42+t,.01)],col); };
  rows(FARM_R,1,shade(PAL.farm,.84),.16);
  rows(NFLD_R,1,shade(PAL.farm,.84),.16);
  rows(ORCH_R,1.8,shade(PAL.orchardSoil,.88),.2);

  const sh=(x,y,w2,d2,hh)=>{ const dx=-0.3*hh, dy=0.5*hh;
    poly(g,[iso(x+dx,y+dy,.02),iso(x+w2+dx,y+dy,.02),iso(x+w2+dx,y+d2+dy,.02),iso(x+dx,y+d2+dy,.02)],PAL.shadow); };
  buildings.forEach(b=>sh(b.x,b.y,b.w,b.d,b.h));
  props.forEach(p=>{
    if(p.kind==='tree'||p.kind==='fruit') sh(p.x-.4,p.y-.4,.8,.8,1.05*p.s);
    else if(p.kind==='stall') sh(p.x,p.y,1.4,1.2,1.2);
    else if(p.kind==='rock') sh(p.x-.3*p.s,p.y-.3*p.s,.6*p.s,.6*p.s,.5*p.s);
    else if(p.kind==='crane') sh(p.x,p.y,.7,.7,4.2);
  });
  // The outer forest, in depth order, on top of the finished terrain.
  for(const o of farStatics){
    const q=iso(o.px,o.py,0);
    g.drawImage(o.spr.c, q.x+o.spr.ox, q.y+o.spr.oy,
                o.spr.c.width/SCALE, o.spr.c.height/SCALE);
  }

  ground={c:cv,ox,oy,s};
}

/* --- 8. STATIC DRAW LIST ------------------------------------------------- */
let statics=[];
function bakeAll(){
  // ALL THREE, every time. farStatics was left out of this reset and, being a
  // const array that is only ever pushed to, every rebake appended the whole
  // woodland belt again: 1608 far-field sprites after the first bake, 3216
  // after one zoom change, and so on. Because the duplicates land on exactly
  // the same tiles, each pass re-drew every trunk, canopy and semi-transparent
  // shadow on top of itself, so the belt visibly thickened and crept as you
  // used the map - a forest that grew over time, from a missing `= []`.
  cache={}; statics=[]; farStatics.length=0;
  buildings.forEach(b=>{
    b.spr=bake(g=>drawBuilding(g,b),
      {x0:-0.35,x1:b.w+0.45,y0:-0.35,y1:b.d+0.45,z0:0,z1:b.h+1.8},7);
    b.sprOpen=bake(g=>drawOpen(g,b),
      {x0:-0.35,x1:b.w+0.45,y0:-0.35,y1:b.d+0.45,z0:0,z1:1.1},7);
    b.sortKey=(b.x+b.w)+(b.y+b.d);
    statics.push({key:b.sortKey, px:b.x, py:b.y, spr:b.spr, pick:b});
  });
  props.forEach(p=>{
    let spr=null, glow=false, key=p.x+p.y;
    switch(p.kind){
      case 'tree': case 'fruit': {
        const bk=Math.round(p.s*8), fr=p.kind==='fruit';
        spr=cached('t'+bk+(fr?'f':''), g=>drawTree(g,bk/8,fr),
          {x0:-.9,x1:.9,y0:-.9,y1:.9,z0:0,z1:1.5*bk/8}, 32*bk/8);
        break; }
      case 'lamp': glow=true;
        spr=cached('lamp',drawLamp,{x0:-.3,x1:.5,y0:-.3,y1:.3,z0:0,z1:1.25}); break;
      case 'bush':
        spr=cached('bush'+p.flower, g=>drawBush(g,p.flower),
          {x0:-.4,x1:.4,y0:-.4,y1:.4,z0:0,z1:.6}); break;
      case 'fence':
        spr=cached('fence'+(p.axis||'x')+p.len,g=>drawFence(g,p.len,p.axis),
          p.axis==='y' ? {x0:-.25,x1:.35,y0:-.25,y1:p.len+.35,z0:0,z1:.6}
                       : {x0:-.25,x1:p.len+.35,y0:-.25,y1:.35,z0:0,z1:.6}); break;
      case 'sign':
        spr=cached('sign'+(p.tone||'gov'),g=>drawSign(g,p.tone),
          {x0:-.25,x1:1.75,y0:-.25,y1:.4,z0:0,z1:1.25}); break;
      case 'post':
        spr=cached('post',drawPost,{x0:-.1,x1:1,y0:-.1,y1:.2,z0:0,z1:.5}); break;
      case 'bench':
        spr=cached('bench',drawBench,{x0:-.1,x1:1,y0:-.1,y1:.4,z0:0,z1:.3}); break;
      case 'fountain': key+=1.7;
        spr=cached('fount',drawFountain,{x0:-.1,x1:1.8,y0:-.1,y1:1.8,z0:0,z1:.9}); break;
      case 'pond': spr=bake(g=>drawPond(g,p.w,p.d),
          {x0:-.1,x1:p.w+.1,y0:-.1,y1:p.d+.1,z0:0,z1:.1},3); break;
      case 'stall': key+=1.4;
        spr=cached('stall'+p.col,g=>drawStall(g,p.col),
          {x0:-.3,x1:1.7,y0:-.3,y1:1.5,z0:0,z1:1.4}); break;
      case 'crane': key+=0.7;
        spr=cached('crane',drawCrane,{x0:-1.8,x1:3.2,y0:-.2,y1:.8,z0:0,z1:4.6},8); break;
      case 'pile':
        spr=cached('pile',drawPile,{x0:-.1,x1:1,y0:-.1,y1:.8,z0:0,z1:.7}); break;
      case 'rock': {
        const bk=Math.round(p.s*6);
        spr=cached('rk'+bk,g=>drawRock(g,bk/6),
          {x0:-.5,x1:.5,y0:-.5,y1:.5,z0:0,z1:.7}); break; }
      case 'pier': key+=p.len;
        spr=cached('pier'+p.len,g=>drawPier(g,p.len),
          {x0:-.2,x1:p.len+.2,y0:-.2,y1:1.1,z0:-.4,z1:.3}); break;
      case 'boat': key+=1.9;
        spr=cached('boat',drawBoat,{x0:-.1,x1:2,y0:-.1,y1:.9,z0:0,z1:1.9}); break;
      case 'crate':
        spr=cached('crate',drawCrate,{x0:-.1,x1:.7,y0:-.1,y1:.7,z0:0,z1:.6}); break;
    }
    // The forest belt is thousands of sprites that never move, sit outside the
    // town, and have nothing walking between them and the ground. Drawing them
    // every frame cost three quarters of the frame budget - fps fell from 59
    // to 13 when the belt went in. They are painted into the ground bake
    // instead, once per zoom change rather than sixty times a second.
    if(!spr) return;
    if(p.farField) farStatics.push({key,px:p.x,py:p.y,spr});
    else statics.push({key,px:p.x,py:p.y,spr,glow});
  });
  statics.sort((a,b)=>a.key-b.key);
  farStatics.sort((a,b)=>a.key-b.key);
  bakeGround();
}

/* --- 9. RESIDENTS -------------------------------------------------------- */
// Positions come from the simulation. The fallback wander below only runs if
// nothing has been supplied, so an unwired page still shows a living town.
const roadTiles=[];
// Everything a person on foot may use: the carriageway, the pavement that
// rings it and the two public squares. The simulation routes commutes over
// this, which is what puts residents on the streets instead of straight-lining
// them across gardens.
const walkTiles=[];
for(let y=0;y<G;y++) for(let x=0;x<G;x++){
  const t=tAt(x,y);
  if(t===T.ROAD) roadTiles.push([x,y]);
  // Carriageway and pavement only. The market and the square are paved, but
  // they sit inside their blocks with a verge between them and the kerb, so
  // they are places you arrive at - not part of the network you travel on.
  if(t===T.ROAD||t===T.PAVE) walkTiles.push([x,y]);
}

const SKIN=['#E8BC93','#D09A70','#B07C52','#8E5D3B','#F4D2AE'];
const SHIRT=['#C9584B','#4E7CA8','#5E8C4F','#D0A64E','#8A5EA0','#3F6B78','#BE764C','#5C6B7E','#A8484E','#4E9A8A'];

let agents=[];
let driven=false;               // true once the simulation supplies positions

function seedAgents(n){
  agents=[];
  for(let i=0;i<n;i++){
    const [x,y]=roadTiles[Math.floor(rng()*roadTiles.length)];
    agents.push({x:x+.5,y:y+.5,tx:x+.5,ty:y+.5,
      skin:choose(SKIN),shirt:choose(SHIRT),
      speed:.5+rng()*.55, bob:rng()*6.28});
  }
}
function stepAgent(a,dt){
  const dx=a.tx-a.x, dy=a.ty-a.y, dist=Math.hypot(dx,dy);
  if(dist<.06){
    const cx=Math.floor(a.x), cy=Math.floor(a.y);
    const o=[[1,0],[-1,0],[0,1],[0,-1]].map(([ux,uy])=>[cx+ux,cy+uy])
      .filter(([nx,ny])=>tAt(nx,ny)===T.ROAD);
    if(!o.length){ a.tx=cx+.5; a.ty=cy+.5; return; }
    const [nx,ny]=o[Math.floor(rng()*o.length)];
    a.tx=nx+.32+rng()*.36; a.ty=ny+.32+rng()*.36;
  } else {
    const s=Math.min(dist,a.speed*dt);
    a.x+=dx/dist*s; a.y+=dy/dist*s; a.bob+=dt*9;
  }
}
/* --- 9b. RESIDENTS, IN COSTUME -------------------------------------------
   A resident is drawn from what they DO: the trade sets the shirt, the
   trousers, the coat and the hat, and puts the tool of the job in their hand.
   A police officer in navy with a peaked cap, a doctor in a white coat, a
   miner in a hi-vis jacket and a lamped helmet - all readable at a glance
   without opening anyone's file.
   ========================================================================= */
const HI_VIS = '#F2E14C';

/** Hats and helmets, drawn on top of the head. w is the head width. */
function drawHat(g, kind, x, y, w, k, hair){
  const px = (a,b,c,d,col) => { g.fillStyle=col; g.fillRect(x+a*k, y+b*k, c*k, d*k); };
  switch(kind){
    case 'cap-police':
      px(-w*0.62,-1.9,w*1.24,2.0,'#22385E');                  // crown
      px(-w*0.80,-0.3,w*1.60,0.9,'#1A2C49');                  // peak
      px(-w*0.24,-1.7,w*0.48,0.8,'#D8C063');                  // badge
      break;
    case 'helmet-fire':
      px(-w*0.70,-2.2,w*1.40,2.2,'#C6362A');
      px(-w*0.95,-0.5,w*1.90,0.9,'#A82C22');                  // wide brim
      px(-w*0.18,-2.9,w*0.36,0.8,'#E8C24C');                  // crest
      break;
    case 'helmet-work':
      px(-w*0.66,-2.0,w*1.32,2.0,'#F0A62E');
      px(-w*0.86,-0.4,w*1.72,0.8,'#D8901F');
      break;
    case 'helmet-mine':
      px(-w*0.66,-2.0,w*1.32,2.0,'#E2C24A');
      px(-w*0.86,-0.4,w*1.72,0.8,'#C9A63A');
      px(-w*0.16,-1.9,w*0.34,0.9,'#FFF6C8');                  // lamp
      break;
    case 'straw':
      px(-w*0.58,-1.6,w*1.16,1.6,'#D9BE72');
      px(-w*1.15,-0.3,w*2.30,0.8,'#C9A85C');                  // broad brim
      break;
    case 'toque':
      px(-w*0.52,-3.0,w*1.04,3.0,'#FBFBF6');                  // tall chef's hat
      px(-w*0.62,-0.5,w*1.24,0.8,'#EFEFE8');
      break;
    case 'mortar':
      px(-w*0.46,-1.4,w*0.92,1.4,'#2E3440');
      px(-w*0.95,-2.0,w*1.90,0.7,'#2E3440');                  // board
      px(w*0.30,-1.9,w*0.16,1.6,'#D8C063');                   // tassel
      break;
    case 'scrub-cap':
      px(-w*0.60,-1.5,w*1.20,1.6,'#7FC8BC');
      break;
    case 'bandana':
      px(-w*0.62,-1.1,w*1.24,1.2,'#C4443B');
      px(w*0.30,-0.9,w*0.50,1.0,'#A83A32');                   // knot
      break;
    case 'beanie':
      px(-w*0.58,-1.7,w*1.16,1.8,'#6A8EAE');
      px(-w*0.62,-0.4,w*1.24,0.7,'#587B99');
      break;
    case 'flatcap':
      px(-w*0.60,-1.2,w*1.20,1.2,'#6E6455');
      px(-w*0.86,-0.4,w*1.20,0.7,'#5E5548');                  // short peak
      break;
    case 'visor':
      px(-w*0.58,-0.9,w*1.16,0.9,'#3E4650');                  // band
      px(-w*0.86,-0.3,w*1.72,0.6,'#6FA85E');                  // green eyeshade
      break;
    case 'headset':
      if (hair) { g.fillStyle=hair; g.fillRect(x-w*0.62*k, y-1.2*k, w*1.24*k, 1.4*k); }
      px(-w*0.72,-1.5,w*1.44,0.5,'#3A4048');                  // band over the head
      px(-w*0.86,-1.0,w*0.30,1.0,'#3A4048');                  // ear cup
      px( w*0.56,-1.0,w*0.30,1.0,'#3A4048');
      px(-w*0.80, 0.0,w*0.55,0.28,'#3A4048');                 // boom mic
      break;
    case 'glasses':
      if (hair) { g.fillStyle=hair; g.fillRect(x-w*0.62*k, y-1.2*k, w*1.24*k, 1.4*k); }
      px(-w*0.60, 0.5,w*0.48,0.45,'#EAF2F6');                 // lenses
      px( w*0.12, 0.5,w*0.48,0.45,'#EAF2F6');
      px(-w*0.14, 0.62,w*0.28,0.16,'#3A4048');                // bridge
      break;
    case 'sunhat':
      px(-w*0.52,-1.2,w*1.04,1.2,'#F2DC8A');
      px(-w*1.05,-0.25,w*2.10,0.7,'#E4CB74');                 // soft wide brim
      break;
    case 'tie': case 'apron': case 'satchel':
      // Worn on the body, not the head - drawn by drawAgent - so the hair
      // still shows. Falling through to default would hide it.
      if (hair) { g.fillStyle=hair; g.fillRect(x-w*0.62*k, y-1.2*k, w*1.24*k, 1.4*k); }
      break;
    default:
      if (hair) { g.fillStyle=hair; g.fillRect(x-w*0.62*k, y-1.2*k, w*1.24*k, 1.4*k); }
  }
}

/** The tool of the trade, held at the side. */
function drawTool(g, kind, x, y, k, flip){
  const s = flip ? -1 : 1;
  // The figure grew; the tools have to grow with it or a pick reads as a
  // scratch beside someone's elbow.
  const S = 2.8;
  const px = (a,b,c,d,col) => { g.fillStyle=col;
    g.fillRect(x + s*a*S*k - (s<0 ? c*S*k : 0), y + b*S*k, c*S*k, d*S*k); };
  switch(kind){
    case 'hoe':   px(3.4,-9.5,0.9,10.5,'#8A6844'); px(3.0,-9.8,2.4,1.1,'#9AA2A8'); break;
    case 'pick':  px(3.4,-9.0,0.9,10.0,'#8A6844'); px(1.8,-9.6,4.6,1.0,'#9AA2A8'); break;
    case 'axe':   px(3.4,-8.5,0.9,9.5,'#8A6844');  px(2.6,-9.2,2.6,2.4,'#B8BEC4'); break;
    case 'hammer':px(3.4,-7.0,0.8,7.6,'#8A6844');  px(2.4,-7.6,2.8,1.6,'#7C8590'); break;
    case 'broom': px(3.4,-9.5,0.8,10.0,'#8A6844'); px(2.6,-0.6,2.6,1.8,'#D9B45E'); break;
    case 'tray':  px(2.8,-6.0,4.4,0.9,'#C9A063'); break;
    case 'pan':   px(2.8,-5.8,3.4,1.0,'#4A5560'); px(6.0,-5.6,2.2,0.6,'#2E3440'); break;
    case 'box':   px(2.8,-7.2,3.8,3.6,'#C79A5F'); px(2.8,-5.8,3.8,0.7,'#A8804A'); break;
    case 'book':  px(2.8,-7.0,3.0,2.4,'#C4443B'); px(2.8,-7.0,0.8,2.4,'#8E2F28'); break;
    case 'cup':   px(3.0,-6.6,2.0,2.0,'#FBFBF6'); break;
    case 'coin':  g.fillStyle='#E8C24C'; g.beginPath();
                  g.arc(x+s*5.6*k, y-9.0*k, 1.7*k, 0, 6.2832); g.fill(); break;
    case 'cross': px(2.9,-7.4,3.0,3.0,'#FBFBF6'); px(4.0,-7.0,0.8,2.2,'#C6362A');
                  px(3.2,-6.3,2.4,0.8,'#C6362A'); break;
    case 'badge': g.fillStyle='#D8C063'; g.beginPath();
                  g.arc(x+s*5.0*k, y-11.2*k, 1.6*k, 0, 6.2832); g.fill(); break;
    case 'hose':  px(2.8,-6.4,4.0,1.0,'#C6362A'); break;
    case 'quill': px(3.2,-8.4,0.7,4.0,'#EFE8D6'); break;
    case 'plan':  px(2.8,-7.2,3.6,2.6,'#E8EDF2'); break;
  }
}

function drawAgent(g,a,z,cx,cy){
  const p=iso(a.x,a.y,0), sx=p.x*z+cx, sy=p.y*z+cy;
  // The figure is drawn twice the size it used to be, so a costume - a peaked
  // cap, a white coat, a hi-vis band - is legible rather than guessed at. Held
  // at that size all the way in, though, a resident stood taller than their own
  // cottage. So the boost is spent where it was needed and nowhere else: full
  // 2x when the whole town is fitted on screen, easing back to life size (about
  // a third of a cottage's height) once the camera is close enough to read the
  // costume anyway. Only the figure scales - the position is always the true
  // one, so where a person stands never moves as you zoom.
  const k = Math.max(z*0.5, Math.min(z, 0.62));
  const lift=a.walking ? Math.abs(Math.sin(a.bob||0))*1.8*k : 0;
  const B=sy-lift;                                  // ground line, with the bob
  const HEAD=11.2, BODY=25.2, LEG=12.0;
  const shirt=a.shirt||'#C9584B', legs=a.legs||'#4A4238';
  const px=(x,y,w,h,c)=>{ g.fillStyle=c; g.fillRect(sx+x*k, B+y*k, w*k, h*k); };

  // Asleep in their own bed, or a patient in a ward. A resident standing bolt
  // upright on top of their own mattress at two in the morning was the last
  // thing left in the town that read as a simulation rather than a place.
  if (a.lying){
    const L=34, TK=10, blanket=a.coat||shirt;
    g.globalAlpha=.20; g.fillStyle='#2E241C';
    g.beginPath(); g.ellipse(sx,sy,20*k,8*k,0,0,6.2832); g.fill();
    g.globalAlpha=1;
    px(-L*0.52, -TK,       L*0.74, TK,        blanket);              // under the covers
    px(-L*0.52, -TK,       L*0.74, TK*0.34,   shade(blanket,1.12));  // lit top face
    px(-L*0.52, -TK*0.34,  L*0.74, TK*0.34,   shade(blanket,0.80));  // shaded near side
    px( L*0.15, -TK*0.62,  L*0.13, TK*0.62,   '#EFE7DA');            // pillow
    g.fillStyle=a.skin||'#E8BC93';
    g.beginPath(); g.arc(sx+L*0.30*k, B-TK*0.74*k, 6.4*k, 0, 6.2832); g.fill();
    if(a.hair){ g.fillStyle=a.hair;
      g.beginPath(); g.arc(sx+L*0.33*k, B-TK*0.96*k, 5.4*k, 3.25, 6.35); g.fill(); }
    if(a.selected){
      g.strokeStyle='#F2C979'; g.lineWidth=2.4*k;
      g.beginPath(); g.ellipse(sx,sy,24*k,12*k,0,0,6.2832); g.stroke();
    }
    return;
  }

  g.globalAlpha=.24; g.fillStyle='#2E241C';
  g.beginPath(); g.ellipse(sx,sy,15.2*k,7.6*k,0,0,6.2832); g.fill();
  g.globalAlpha=1;

  const legTop=-LEG, bodyTop=legTop-BODY, headY=bodyTop-HEAD*0.55;
  // A dark silhouette a hair larger than the figure, drawn first. It is what
  // keeps a resident readable against a bright green field or a pale floor.
  g.fillStyle='#2A231C';
  g.fillRect(sx-8.8*k, B+(bodyTop-1.0)*k, 17.6*k, (BODY+LEG+2.0)*k);
  g.beginPath(); g.arc(sx, B+headY*k, HEAD*0.62*k, 0, 6.2832); g.fill();
  // legs, striding while they walk
  const stride = a.walking ? Math.sin(a.bob||0)*1.1 : 0;
  px(-6.2+stride*1.2, legTop, 5.4, LEG, shade(legs,0.88));
  px( 0.8-stride*1.2, legTop, 5.4, LEG, legs);
  px(-6.6, legTop+LEG-2.2, 6.2, 2.4, '#3A3229');   // boots
  px( 0.4, legTop+LEG-2.2, 6.2, 2.4, '#3A3229');
  // torso
  px(-7.8, bodyTop, 15.6, BODY, shirt);
  px(-7.8, bodyTop, 5.2, BODY, shade(shirt,0.74));  // shaded side
  // a hi-vis worker gets the band that makes them hi-vis
  if (a.hat==='helmet-work'||a.hat==='helmet-mine')
    px(-7.8, bodyTop+BODY*0.40, 15.6, 3.8, HI_VIS);
  // The sash of office, worn shoulder to hip. Five residents in three hundred
  // wear one, which is the point: you can pick the government out of a crowd.
  if (a.sash){
    g.save();
    g.beginPath();
    g.moveTo(sx-7.8*k, B+(bodyTop+BODY*0.10)*k);
    g.lineTo(sx-2.6*k, B+(bodyTop+BODY*0.02)*k);
    g.lineTo(sx+7.8*k, B+(bodyTop+BODY*0.62)*k);
    g.lineTo(sx+7.8*k, B+(bodyTop+BODY*0.80)*k);
    g.closePath();
    g.fillStyle=a.sash; g.fill();
    g.fillStyle=shade(a.sash,0.78);
    g.fillRect(sx+4.4*k, B+(bodyTop+BODY*0.58)*k, 3.4*k, 3.4*k);   // the medal
    g.restore();
  }
  // worn on the body rather than the head
  if (a.hat==='apron'){
    px(-5.6, bodyTop+BODY*0.34, 11.2, BODY*0.64, '#F4EFE2');
    px(-5.6, bodyTop+BODY*0.34, 11.2, 1.2, '#DED6C4');        // waistband
  }
  if (a.hat==='tie'){
    px(-1.2, bodyTop+1.6, 2.4, BODY*0.52, '#B23A3A');
  }
  if (a.hat==='satchel'){
    px(-8.6, bodyTop+BODY*0.42, 4.4, BODY*0.34, '#8A6844');   // bag on the hip
    px(-8.0, bodyTop+2.0, 1.6, BODY*0.44, '#6E5236');         // strap
  }
  // a coat over the top: doctors, chefs, shopkeepers
  if (a.coat){
    px(-9.0, bodyTop+BODY*0.26, 4.0, BODY*0.88, a.coat);
    px( 5.0, bodyTop+BODY*0.26, 4.0, BODY*0.88, shade(a.coat,0.90));
    px(-9.0, bodyTop+BODY*0.26, 18.0, 2.8, shade(a.coat,0.96));
  }
  // arms
  px(-10.4, bodyTop+3.2, 3.4, BODY*0.62, shade(shirt,0.80));
  px(  7.0, bodyTop+3.2, 3.4, BODY*0.62, shade(shirt,0.86));
  // head
  g.fillStyle=a.skin||'#E8BC93';
  g.beginPath(); g.arc(sx, B+headY*k, HEAD*0.5*k, 0, 6.2832); g.fill();
  drawHat(g, a.hat, sx, B+(headY-HEAD*0.40)*k, HEAD*0.5, k, a.hair||'#3A2A20');
  // the tool, only while they are actually at work
  if (a.tool && a.atWork) drawTool(g, a.tool, sx, B, k, (a.bob||0) > 3.14);

  // Office marker. The sash reads at close zoom and disappears at any other,
  // and "where is the Prime Minister" is a question you ask of the whole town,
  // not of one building - so the five of them also carry a pin, the same
  // idiom the place labels use. It rides the figure's zoom-aware scale, so it
  // stays findable when the whole town is fitted on screen.
  if(a.sash){
    const R=7.6*k, my=B+(headY-HEAD*0.95)*k-R;
    g.save();
    g.shadowColor='rgba(30,26,22,0.35)'; g.shadowBlur=4*k; g.shadowOffsetY=1.5*k;
    g.fillStyle=a.sash;
    g.beginPath(); g.arc(sx,my,R,0,6.2832); g.fill();
    g.restore();
    g.beginPath(); g.moveTo(sx-3.0*k,my+R*0.72); g.lineTo(sx+3.0*k,my+R*0.72);
    g.lineTo(sx,my+R*1.75); g.closePath(); g.fillStyle=a.sash; g.fill();
    g.strokeStyle='rgba(255,255,255,0.92)'; g.lineWidth=1.5*k;
    g.beginPath(); g.arc(sx,my,R,0,6.2832); g.stroke();
    // a five-pointed star for the Prime Minister, a solid dot for a minister
    g.fillStyle='#FFFFFF';
    if(a.sashRank==='pm'){
      g.beginPath();
      for(let i=0;i<10;i++){
        const rr=i%2? R*0.34 : R*0.72, an=-Math.PI/2 + i*Math.PI/5;
        const px2=sx+Math.cos(an)*rr, py2=my+Math.sin(an)*rr;
        i? g.lineTo(px2,py2) : g.moveTo(px2,py2);
      }
      g.closePath(); g.fill();
    } else {
      g.beginPath(); g.arc(sx,my,R*0.36,0,6.2832); g.fill();
    }
  }
  if(a.selected){
    g.strokeStyle='#F2C979'; g.lineWidth=2.4*k;
    g.beginPath(); g.ellipse(sx,sy,24*k,12*k,0,0,6.2832); g.stroke();
    g.fillStyle='#F2C979';
    g.beginPath(); g.moveTo(sx, B+(headY-9)*k);
    g.lineTo(sx-4.6*k, B+(headY-16)*k); g.lineTo(sx+4.6*k, B+(headY-16)*k);
    g.closePath(); g.fill();
  }
  // Part E.5: mid-workflow. A single small copper dot at the shoulder - not
  // a ring, not a label, not a colour change to the whole figure. The point
  // is that a glance over the town shows what fraction is executing a written
  // procedure; anything louder would compete with the map itself.
  if(a.following){
    g.fillStyle='#C9803C';
    g.beginPath(); g.arc(sx-7.5*k, B+(headY-6)*k, 2.5*k, 0, 6.2832); g.fill();
    g.strokeStyle='rgba(255,236,214,.85)'; g.lineWidth=0.9*k; g.stroke();
  }
  if(a.talking){
    g.fillStyle='#FFFFFF';
    g.beginPath(); g.roundRect(sx+6*k, B+(headY-13)*k, 17*k, 11*k, 4.5*k); g.fill();
    g.fillStyle='#6E6459';
    for(let i=0;i<3;i++){ g.beginPath();
      g.arc(sx+10*k+i*4.2*k, B+(headY-7.4)*k, 1.3*k, 0, 6.2832); g.fill(); }
  }
}

const cars=[];
// A car's run is measured off the terrain rather than assumed to be the whole
// core. Anything that is not carriageway - water, verge, the map edge - ends
// the run, so traffic turns back at the bank instead of driving into the river.
function roadRun(axis, lane, seed){
  const at = t => axis==='x' ? tAt(Math.floor(t), Math.floor(lane))
                             : tAt(Math.floor(lane), Math.floor(t));
  let t = Math.floor(seed);
  if(at(t)!==T.ROAD){                       // walk to the nearest carriageway
    let k=0; while(k<G && at(t-k)!==T.ROAD && at(t+k)!==T.ROAD) k++;
    if(k>=G) return null;
    t = at(t-k)===T.ROAD ? t-k : t+k;
  }
  let a=t, b=t;
  while(a-1>=0   && at(a-1)===T.ROAD) a--;
  while(b+1<G-1  && at(b+1)===T.ROAD) b++;
  return (b-a) < 6 ? null : { t0:a+0.5, t1:b+0.5 };
}
// No traffic. Kasukabe is a walking town: every commute, errand and patrol in
// it is a person on a pavement, and cars sliding along the bands behind them
// said something about the place that none of the rest of the simulation
// does. The machinery is left intact - `cars`, roadRun(), drawCar() and the
// depth-sorted draw all still work - so putting traffic back is populating
// this array again, not rebuilding it.
void roadRun;
function drawCar(g,c,k,cx,cy){
  const x=c.axis==='x'?c.t:c.lane, y=c.axis==='x'?c.lane:c.t;
  const p=iso(x,y,0), sx=p.x*k+cx, sy=p.y*k+cy;
  g.globalAlpha=.2; g.fillStyle='#2E241C';
  g.beginPath(); g.ellipse(sx,sy+2*k,12*k,5.5*k,0,0,6.2832); g.fill();
  g.globalAlpha=1;
  const w=c.axis==='x'?.9:.42, d=c.axis==='x'?.42:.9;
  g.save(); g.translate(sx,sy); g.scale(k,k); g.translate(-p.x,-p.y);
  prism(g,x-w/2,y-d/2,.02,w,d,.28,c.col);
  prism(g,x-w/4,y-d/4,.30,w/2,d/2,.16,'#33414A');
  g.restore();
}

/* --- 10. RENDER ---------------------------------------------------------- */
let cv=null, ctx=null;
let zoom=.5, panX=0, panY=0, night=false, showLabels=true;
// Roof-off mode: every building swaps to its open-room sprite, so you can see
// the layout and the people working in it.
let interiors=false;
let dragging=false, moved=0, lastP=null;
let onPick=null;

function fit(W,H){
  W=W||FRAME_W; H=H||FRAME_H;
  const z=Math.min(W/(G*TW), H/(G*TH+PLINTH*ZH+10)) * 0.995;
  return { z, px: W/2, py: (H-(G*TH+PLINTH*ZH)*z)/2 };
}
function resetView(){ const f=fit(); zoom=f.z; panX=f.px; panY=f.py; }
const screenToWorld=(sx,sy)=>{
  const wx=(sx-panX)/zoom, wy=(sy-panY)/zoom;
  return {x:wx/TW+wy/TH, y:wy/TH-wx/TW};
};

const LABELS = () => {
  const out=[];
  buildings.filter(b=>b.label).forEach(b=>out.push(
    {t:b.label, i:b.icon, zid:b.zone||b.zid||null,
     // Bigger civic buildings outrank small shops for the space, so what you
     // lose first when the town is small on screen is the corner shop, not
     // the hospital.
     rank: 1 + 1/(1 + b.w*b.d),
     at:iso(b.x+b.w/2,b.y+b.d/2,b.h+1.2)}));
  props.filter(p=>p.kind==='label').forEach(p=>{
    // Sit the tag on the middle of the district it names. Falls back to the
    // prop's own coordinates for any tag that has no zone behind it.
    const z = p.zid && ZONES[p.zid];
    const x = z ? z.x + z.w/2 : p.x;
    const y = z ? z.y + z.h/2 : p.y;
    out.push({t:p.text, i:p.icon, at:iso(x,y,p.z), zid:p.zid, rank:0});
  });
  return out;
};

/** Signboards, laid out before any of them is drawn.
 *
 *  Every board used to be printed at its building's anchor with no regard for
 *  its neighbours, so the fire station and the police station - a few tiles
 *  apart - printed one on top of the other, and so did the grocery and the
 *  pharmacy, the bakery and the cafe, the trade offices and the bank. This
 *  does what a map does: place them one at a time, lift a board that collides
 *  until it clears, draw a leader down to the building it belongs to, and
 *  drop the board entirely rather than print it over its neighbour.
 *
 *  Boards are placed front-to-back. A building nearer the camera wins the
 *  space, which matches what the reader is looking at.
 */
// A live state a place can show on its own sign - "SOLD OUT" on the grocery,
// say. The renderer knows nothing about food; the simulation hands it a word
// and a colour per zone and this draws it.
let labelBadges = {};
let lastDrawn = [], lastCrowded = [];
function drawLabels(g,k,cx,cy,scaleUI){
  if(!showLabels) return;
  const fs=Math.round(15*scaleUI);
  g.save();
  g.font=`800 ${fs}px "Archivo", system-ui, sans-serif`;
  g.textBaseline='middle'; g.textAlign='center';

  // The icon and the text are measured SEPARATELY and laid out from the left,
  // instead of measuring "icon + text" as one string and centring it. An emoji
  // does not always draw at the width it measures - variation selectors and
  // font fallback both change it - and when the pill's width came from one
  // measurement while the glyphs were centred by another, the pill ended up
  // with a slab of empty white down one side. Measuring the two pieces and
  // placing both from a known left edge makes the padding symmetric by
  // construction, whatever the emoji does.
  const PAD=11*scaleUI, GAP=7*scaleUI;
  const BADGE_FS=Math.round(10*scaleUI);
  const items=LABELS().map(L=>{
    const icon=String(L.i||''), text=String(L.t||'');
    const iw=icon ? g.measureText(icon).width : 0;
    const tw=g.measureText(text).width;
    const badge=L.zid ? labelBadges[L.zid] : null;
    let bw=0;
    if(badge){
      g.save(); g.font=`900 ${BADGE_FS}px "JetBrains Mono", monospace`;
      bw=g.measureText(badge.text).width + 10*scaleUI;
      g.restore();
    }
    const inner=iw + (icon && text ? GAP : 0) + tw + (badge ? GAP + bw : 0);
    return { icon, text, iw, tw, badge, bw, badgeFs:BADGE_FS, rank:L.rank||0,
      w: inner + PAD*2, h: 28*scaleUI,
      x:L.at.x*k+cx, anchorY:L.at.y*k+cy };
  // Districts first, then buildings largest to smallest, then nearest the
  // camera. Which tag wins a contested spot should be about what the reader
  // needs, not which happens to be drawn first.
  }).sort((a,b)=>(a.rank-b.rank) || (b.anchorY-a.anchorY));

  // Nothing is allowed outside the frame. THE INN sits near the west edge and
  // was drawn half off the canvas, which reads as a rendering fault rather
  // than a label.
  const W=g.canvas.width, H=g.canvas.height, EDGE=6*scaleUI;
  for(const it of items){
    // A building well outside the frame has no business competing for space
    // inside it. Clamping those tags to the edge is what crowded out the
    // pharmacy and the square when zoomed in on the far side of town. Only
    // tags whose building is nearly on screen are nudged into view.
    const off = it.x < -it.w || it.x > W + it.w ||
                it.anchorY < -it.h*2 || it.anchorY > H + it.h*2;
    if(off){ it.dropped = true; continue; }
    it.x = Math.max(it.w/2+EDGE, Math.min(W-it.w/2-EDGE, it.x));
    it.anchorY = Math.max(it.h/2+EDGE, Math.min(H-it.h/2-EDGE, it.anchorY));
  }
  const STEP=Math.max(7,16*scaleUI), TRIES=14, placed=[];
  const clear=(x,y,w,h)=>!placed.some(q =>
    Math.abs(q.x-x) < (q.w+w)/2 - 3*scaleUI &&
    Math.abs(q.y-y) < (q.h+h)/2 + 2*scaleUI);

  // Try above first (a board over its building reads naturally), then below.
  // A tag that could not be placed used to be dropped, and in the tight corner
  // where the grocery, the pharmacy, the market and the square sit on top of
  // one another that meant a real shop simply had no sign on the map. A sign
  // slightly closer to its neighbour than ideal is always better than a
  // building nobody can name, so the last resort is to draw it at its anchor
  // rather than not at all.
  for(const it of items){
    if(it.dropped) continue;                // its building is off screen
    for(let t=0;t<TRIES;t++){
      const dy = (t % 2 === 0 ? -1 : 1) * Math.ceil(t/2) * STEP;
      const y = it.anchorY + dy;
      if(clear(it.x,y,it.w,it.h)){
        it.y=y; placed.push({x:it.x,y,w:it.w,h:it.h}); break;
      }
    }
    // Nowhere clear after fourteen tries. Printing it anyway put "SOUTH
    // COTTAGES" straight across "KASUKABE PHARMACY" once the island grew and
    // the town got smaller on screen. A tag you cannot read is worse than a
    // tag that reappears as you zoom in - which is what a map does.
    if(it.y===undefined) it.dropped=true;
  }

  lastDrawn = items.filter(it => !it.dropped).map(it => it.text);
  lastCrowded = items.filter(it => it.dropped).map(it => it.text);
  for(const it of items){
    if(it.dropped) continue;
    const lift=it.anchorY-it.y;
    if(Math.abs(lift)>1){                   // leader back to the building
      g.strokeStyle='rgba(30,26,22,0.34)';
      g.lineWidth=Math.max(1,1.4*scaleUI);
      g.beginPath();
      g.moveTo(it.x,it.y+it.h/2); g.lineTo(it.x,it.anchorY+4*scaleUI);
      g.stroke();
      g.fillStyle='rgba(30,26,22,0.34)';
      g.beginPath(); g.arc(it.x,it.anchorY+4*scaleUI,1.9*scaleUI,0,6.2832); g.fill();
    }
    g.save();
    g.shadowColor='rgba(30,26,22,0.24)'; g.shadowBlur=6*scaleUI; g.shadowOffsetY=2*scaleUI;
    g.fillStyle='#FFFFFF';
    g.beginPath(); g.roundRect(it.x-it.w/2,it.y-it.h/2,it.w,it.h,it.h/2); g.fill();
    g.restore();
    if(Math.abs(lift)<=1){                  // the pointer only reads right unmoved
      g.beginPath();
      g.moveTo(it.x-5*scaleUI,it.y+it.h/2-1);
      g.lineTo(it.x,it.y+it.h/2+7*scaleUI);
      g.lineTo(it.x+5*scaleUI,it.y+it.h/2-1);
      g.fillStyle='#FFFFFF'; g.fill();
    }
    // Both glyphs from the pill's own left edge, so they sit inside it exactly.
    const left=it.x-it.w/2+PAD, baseY=it.y+scaleUI*0.5;
    g.textAlign='left';
    if(it.icon){ g.fillStyle='#1E1A16'; g.fillText(it.icon,left,baseY); }
    g.fillStyle='#1E1A16';
    const textX = left + it.iw + (it.icon && it.text ? GAP : 0);
    g.fillText(it.text, textX, baseY);
    if(it.badge){
      const bx=textX + it.tw + GAP, bh=15*scaleUI;
      g.fillStyle=it.badge.bg;
      g.beginPath(); g.roundRect(bx, it.y-bh/2, it.bw, bh, bh/2); g.fill();
      g.save();
      g.font=`900 ${it.badgeFs}px "JetBrains Mono", monospace`;
      g.fillStyle=it.badge.fg;
      g.fillText(it.badge.text, bx+5*scaleUI, baseY);
      g.restore();
    }
  }
  g.restore();
}

// Reused between frames so a full render allocates nothing for the draw list.
const movers = [];
let mergedList = [];
/** Two already-sorted lists into one, without sorting either again. */
function mergeByKey(a, b){
  const out = mergedList;
  out.length = 0;
  let i = 0, j = 0;
  while (i < a.length && j < b.length) out.push(a[i].key <= b[j].key ? a[i++] : b[j++]);
  while (i < a.length) out.push(a[i++]);
  while (j < b.length) out.push(b[j++]);
  return out;
}

function renderTo(g,W,H,z,px,py,ui){
  const sky=g.createLinearGradient(0,0,0,H);
  sky.addColorStop(0,night?'#101A28':PAL.skyTop);
  sky.addColorStop(1,night?'#1B2A3C':PAL.skyBot);
  g.fillStyle=sky; g.fillRect(0,0,W,H);
  g.imageSmoothingEnabled=true;

  const gs=z/SCALE, gz=z/ground.s;
  g.drawImage(ground.c,(-ground.ox)*z+px,(-ground.oy)*z+py,
              ground.c.width*gz, ground.c.height*gz);

  // Scenery never moves, so it is sorted once (at the end of the build) and
  // merged with the few hundred things that do. Copying six thousand statics
  // into a new array and re-sorting the whole lot sixty times a second cost
  // three quarters of the frame budget once the forest belt went in - the
  // draw calls were never the problem, the bookkeeping was.
  movers.length = 0;
  agents.forEach(a=>{
    // With the roof off, someone indoors has to draw after the room they are
    // standing in, or the floor paints straight over them.
    let key=a.x+a.y;
    if(interiors){ const b=buildingAt(a.x,a.y); if(b) key=b.sortKey+0.002; }
    movers.push({agent:a,key});
  });
  cars.forEach(c=>movers.push({car:c,key:c.axis==='x'?c.t+c.lane:c.lane+c.t}));
  critters.forEach(c=>movers.push({critter:c,key:c.x+c.y}));
  movers.sort((a,b)=>a.key-b.key);

  const list=mergeByKey(statics, movers);

  for(const o of list){
    if(o.agent){ drawAgent(g,o.agent,z,px,py); continue; }
    if(o.car){ drawCar(g,o.car,z,px,py); continue; }
    if(o.critter){
      const c=o.critter, p=iso(c.x,c.y,0);
      const sx=p.x*z+px, sy=p.y*z+py;
      if(sx<-60||sx>W+60||sy<-60||sy>H+60) continue;
      // The box has to hold the largest animal, not the smallest: a deer with
      // antlers is taller and longer than a cat, and a box cut for the cat
      // clips the antlers off every deer in the wood.
      const spr=cached('crit'+c.kind, gg=>drawCritter(gg,c.kind),
        {x0:-.45,x1:.85,y0:-.45,y1:.65,z0:0,z1:.9}, 6);
      g.drawImage(spr.c,(p.x+spr.ox)*z+px,(p.y+spr.oy)*z+py,
                  spr.c.width*gs, spr.c.height*gs);
      continue;
    }
    const spr=(interiors && o.pick && o.pick.sprOpen) ? o.pick.sprOpen : o.spr;
    const p=iso(o.px,o.py,0);
    const dx=(p.x+spr.ox)*z+px, dy=(p.y+spr.oy)*z+py;
    const dw=spr.c.width*gs, dh=spr.c.height*gs;
    // Most of a 104-tile island is off screen at any useful zoom. Skipping
    // what does not land in the frame is what pays for the extra scenery.
    if(dx+dw<0 || dy+dh<0 || dx>W || dy>H) continue;
    g.drawImage(spr.c,dx,dy,dw,dh);
  }

  if(night){
    g.save();
    g.globalCompositeOperation='multiply';
    g.fillStyle='rgba(52,72,112,0.60)'; g.fillRect(0,0,W,H);
    g.globalCompositeOperation='lighter';
    for(const o of statics){
      if(!o.glow) continue;
      const p=iso(o.px,o.py,1.1), x=p.x*z+px, y=p.y*z+py, r=36*z;
      const rg=g.createRadialGradient(x,y,0,x,y,r);
      rg.addColorStop(0,'rgba(255,206,128,0.55)'); rg.addColorStop(1,'rgba(255,206,128,0)');
      g.fillStyle=rg; g.beginPath(); g.arc(x,y,r,0,6.2832); g.fill();
    }
    for(const b of buildings){
      const p=iso(b.x+b.w/2,b.y+b.d/2,b.h*0.5), x=p.x*z+px, y=p.y*z+py;
      const r=26*z*Math.max(b.w,b.d)*0.5;
      const rg=g.createRadialGradient(x,y,0,x,y,r);
      rg.addColorStop(0,'rgba(255,196,110,0.26)'); rg.addColorStop(1,'rgba(255,196,110,0)');
      g.fillStyle=rg; g.beginPath(); g.arc(x,y,r,0,6.2832); g.fill();
    }
    g.restore();
  }
  drawLabels(g,z,px,py,ui);
}

// Nobody's dog, somebody's cat, and the squirrels in the park. Each keeps to
// the ground it belongs on and to a short leash around where it lives, so the
// town has movement in it that is not a commute. Seeded last so it takes no
// numbers out of the stream the buildings and traffic were laid out from.
//
// The last three are wild, and belong to the belt rather than to the town: deer,
// rabbits and a fox in the open country and the clearings outside the houses.
// Their ground test needs the tile's POSITION as well as its type, because
// grass in the square and grass under the trees are the same tile and only one
// of them is somewhere a deer would stand.
const critters=[];
const outsideTheTown = (x,y) =>
  x <= CORE0 - 4 || x >= CORE1 + 3 || y <= CORE0 - 4 || y >= CORE1 + 3;
const CRIT_OK = {
  dog:      t => t===T.ROAD||t===T.PAVE||t===T.GRASS||t===T.PLAZA,
  cat:      t => t===T.GRASS||t===T.PAVE||t===T.PARK,
  squirrel: t => t===T.PARK||t===T.GRASS,
  deer:     (t,x,y) => t===T.GRASS && outsideTheTown(x,y),
  rabbit:   (t,x,y) => (t===T.GRASS||t===T.PASTURE) && outsideTheTown(x,y),
  fox:      (t,x,y) => t===T.GRASS && outsideTheTown(x,y),
};
(function seedCritters(){
  const spots={}; for(const k in CRIT_OK) spots[k]=[];
  for(let y=0;y<G;y++) for(let x=0;x<G;x++){
    const t=tAt(x,y);
    for(const k in CRIT_OK) if(CRIT_OK[k](t,x,y)) spots[k].push([x,y]);
  }
  // Deer come in ones and twos and rabbits in numbers; a fox is a fox, and one
  // wood does not hold three of them.
  const want={dog:16,cat:13,squirrel:11,deer:9,rabbit:14,fox:3};
  // How far each is willing to wander from where it was born. A deer crosses a
  // field; a squirrel works one stand of trees.
  const range={squirrel:5,cat:9,dog:9,rabbit:7,deer:16,fox:14};
  const pace={squirrel:.95,cat:.55,dog:.8,rabbit:1.15,deer:.75,fox:.9};
  for(const kind in want){
    const pool=spots[kind]; if(!pool.length) continue;
    for(let i=0;i<want[kind];i++){
      const [hx,hy]=pool[Math.floor(rng()*pool.length)];
      critters.push({kind, hx:hx+.5, hy:hy+.5, x:hx+.5, y:hy+.5, tx:hx+.5, ty:hy+.5,
        r: range[kind], speed: pace[kind], wait: rng()*4});
    }
  }
})();

function stepCritters(dt){
  for(const c of critters){
    if(c.wait>0){ c.wait-=dt; continue; }
    const dx=c.tx-c.x, dy=c.ty-c.y, d=Math.hypot(dx,dy);
    if(d<.08){
      c.wait = .6 + rng()*3.4;        // animals stand about far more than people do
      for(let k=0;k<12;k++){
        const a=rng()*6.2832, r=1+rng()*c.r;
        const nx=c.hx+Math.cos(a)*r, ny=c.hy+Math.sin(a)*r;
        const gx=Math.floor(nx), gy=Math.floor(ny);
        if(CRIT_OK[c.kind](tAt(gx,gy),gx,gy)){ c.tx=nx; c.ty=ny; break; }
      }
      continue;
    }
    const s=c.speed*dt/d;
    c.x+=dx*s; c.y+=dy*s;
  }
}

let last=0;
function frame(now){
  const dt=Math.min(.05,(now-last)/1000); last=now;
  if(!driven) agents.forEach(a=>stepAgent(a,dt));
  else agents.forEach(a=>{ if(a.walking) a.bob+=dt*9; });
  cars.forEach(c=>{
    c.t += c.v*dt*(c.axis==='x'?1:-1);
    if(c.t>c.t1) c.t=c.t0;
    if(c.t<c.t0) c.t=c.t1;
  });
  stepCritters(dt);
  if(ctx){
    ctx.setTransform(1,0,0,1,0,0);
    renderTo(ctx,FRAME_W,FRAME_H,zoom,panX,panY,1);
  }
  requestAnimationFrame(frame);
}

/** What is under this screen point: a building if there is one, otherwise the
 *  district. Returning only buildings meant the forest, the pasture, the ore
 *  outcrop, the farmland, the orchard, the park, the square and the market
 *  were all unclickable - nine of thirteen districts, most of the map's area,
 *  where a click simply did nothing. A district is a place too, and the people
 *  working in it are exactly who you want to see when you click it. */
function pickAt(sx,sy){
  const w=screenToWorld(sx,sy);
  for(let i=buildings.length-1;i>=0;i--){
    const b=buildings[i];
    if(w.x>=b.x&&w.x<=b.x+b.w&&w.y>=b.y&&w.y<=b.y+b.d) return b;
    const lift=b.h*ZH/TH;
    if(w.x>=b.x-lift&&w.x<=b.x+b.w-lift&&w.y>=b.y-lift&&w.y<=b.y+b.d-lift) return b;
  }
  return zoneAt(w.x,w.y);
}

/** The district containing a world point, as a building-shaped object the
 *  place card can render without knowing the difference. The smallest
 *  containing zone wins, so a building-backed zone inside a wider district
 *  (the school inside its block) is preferred over the district itself. */
function zoneAt(wx,wy){
  let best=null, bestArea=Infinity;
  for(const id in ZONES){
    const z=ZONES[id];
    if(wx<z.x||wx>z.x+z.w||wy<z.y||wy>z.y+z.h) continue;
    const area=z.w*z.h;
    if(area<bestArea){ bestArea=area; best={id,z}; }
  }
  if(!best) return null;
  const {id,z}=best;
  return { district:true, zone:id, zid:id, label:z.label, icon:z.icon,
           x:z.x, y:z.y, w:z.w, d:z.h, h:0 };
}
/** Nearest resident to a screen point, for clicking people rather than places. */
function pickAgent(sx,sy){
  let best=null, bd=26*26;
  for(const a of agents){
    const p=iso(a.x,a.y,0), x=p.x*zoom+panX, y=p.y*zoom+panY-14*zoom;
    const d=(x-sx)*(x-sx)+(y-sy)*(y-sy);
    if(d<bd){ bd=d; best=a; }
  }
  return best;
}

function canvasXY(e){
  const r=cv.getBoundingClientRect();
  return { x:(e.clientX-r.left)*(FRAME_W/r.width),
           y:(e.clientY-r.top)*(FRAME_H/r.height) };
}

function cssFit(){
  if(sizedByHost) return;   // the host sets the size; do not letterbox over it
  if(!cv) return;
  const host=cv.parentElement;
  const availW=(host&&host.clientWidth) ? host.clientWidth : window.innerWidth-28;
  const s=Math.min(availW/FRAME_W, (window.innerHeight-150)/FRAME_H, 1);
  cv.style.width=(FRAME_W*s)+'px';
  cv.style.height=(FRAME_H*s)+'px';
}

let rebakeTimer=null;
function scheduleRebake(){
  clearTimeout(rebakeTimer);
  rebakeTimer=setTimeout(()=>{
    const want=Math.max(.3,Math.min(1.0,zoom));
    if(Math.abs(want-SCALE)<0.12) return;
    SCALE=want; bakeAll();
  },220);
}

function mount(canvasEl, opts){
  cv=canvasEl; ctx=cv.getContext('2d');
  cv.width=FRAME_W; cv.height=FRAME_H;
  onPick = (opts&&opts.onPick) || null;

  window.addEventListener('resize',cssFit);
  cv.addEventListener('pointerdown',e=>{
    dragging=true; moved=0; lastP=canvasXY(e);
    cv.setPointerCapture(e.pointerId); cv.style.cursor='grabbing';
  });
  cv.addEventListener('pointermove',e=>{
    if(!dragging) return;
    const p=canvasXY(e);
    panX+=p.x-lastP.x; panY+=p.y-lastP.y;
    moved+=Math.abs(p.x-lastP.x)+Math.abs(p.y-lastP.y);
    lastP=p;
  });
  cv.addEventListener('pointerup',e=>{
    dragging=false; cv.releasePointerCapture(e.pointerId); cv.style.cursor='grab';
    if(moved>8) return;
    const p=canvasXY(e);
    // A person takes precedence over the building behind them.
    const who=pickAgent(p.x,p.y);
    if(onPick) onPick(who?{agent:who}:{building:pickAt(p.x,p.y)}, e);
  });
  cv.addEventListener('wheel',e=>{
    e.preventDefault();
    const p=canvasXY(e), before=screenToWorld(p.x,p.y);
    zoom=Math.max(.2,Math.min(2.2,zoom*(e.deltaY>0?.9:1.111)));
    const q=iso(before.x,before.y,0);
    panX=p.x-q.x*zoom; panY=p.y-q.y*zoom;
    scheduleRebake();
  },{passive:false});

  cssFit();
  resetView();
  SCALE=Math.max(.3,Math.min(1.0,zoom));
  bakeAll();
  last=performance.now();
  requestAnimationFrame(frame);
}

/* --- the surface township-v2.js talks to --------------------------------- */
window.Village = {
  mount,
  fit(){ resetView(); scheduleRebake(); },
  zoomBy(f){
    const before=screenToWorld(FRAME_W/2,FRAME_H/2);
    zoom=Math.max(.2,Math.min(2.2,zoom*f));
    const q=iso(before.x,before.y,0);
    panX=FRAME_W/2-q.x*zoom; panY=FRAME_H/2-q.y*zoom;
    scheduleRebake();
  },
  get zoom(){ return zoom; },
  fitZoom(){ return fit().z; },
  setNight:v=>{night=!!v;},
  setLabels:v=>{showLabels=!!v;},
  setInteriors:v=>{interiors=!!v;},
  get interiors(){ return interiors; },
  setXray:v=>{interiors=!!v;},          // v2.0 name, kept so nothing breaks
  get xray(){ return interiors; },
  /** Put the camera at an exact zoom and world point, in one step.
   *
   *  `focus` pans without changing zoom and the +/- buttons ease towards their
   *  target over several frames, so neither can be used to photograph a named
   *  corner of the map: a headless screenshot fires long before the ease
   *  finishes and catches the camera in transit. This lands it immediately. */
  setView(z,x,y){ zoom=z; const p=iso(x,y,0); panX=FRAME_W/2-p.x*z; panY=FRAME_H/2-p.y*z; },
  /** Centre a named district at `factor` times the fitted zoom, in one step.
   *  ZONES and fit() are private to this file, so a caller that wants "the
   *  government complex, closer" asks here. False for an unknown district. */
  viewZone(id, factor){
    const a = ZONES[id];
    if(!a) return false;
    this.setView(fit().z * (factor || 2), a.x + a.w/2, a.y + a.h/2);
    // Rebake at the new zoom, exactly as fit() does. setView only moves the
    // camera; the sprites are baked for a scale, so a jump from the fitted
    // zoom to 2.4x left the town drawn at the old scale until something else
    // happened to schedule one. The recorder's tour made that visible - the
    // camera moved and the frames did not.
    scheduleRebake();
    return true;
  },
  focus(x,y){ const p=iso(x,y,0); panX=FRAME_W/2-p.x*zoom; panY=FRAME_H/2-p.y*zoom; },
  /** Draw at this CSS size, at the display's pixel density. Called by the
   *  shell on load and on every window resize, so the map genuinely fills the
   *  window instead of being a fixed bitmap scaled into it. */
  resize(cssW, cssH){
    if(!cv) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(320, Math.round(cssW)), h = Math.max(240, Math.round(cssH));
    const pw = Math.round(w*dpr), ph = Math.round(h*dpr);
    if(pw===FRAME_W && ph===FRAME_H && sizedByHost) return;
    sizedByHost = true;
    FRAME_W = pw; FRAME_H = ph;
    cv.width = FRAME_W; cv.height = FRAME_H;
    cv.style.width = w+'px'; cv.style.height = h+'px';
    resetView();
    scheduleRebake();
  },
  /** Positions from the simulation. Each entry: {id,x,y,shirt,skin,hair,
   *  walking,talking,selected}. */
  setAgents(list){ driven=true; agents=list; },
  seedAgents,
  screenOf(x,y){ const p=iso(x,y,0); return {x:p.x*zoom+panX, y:p.y*zoom+panY}; },
  exportPNG(){
    const out=document.createElement('canvas');
    out.width=FRAME_W; out.height=FRAME_H;
    const g=out.getContext('2d');
    const f=fit(FRAME_W,FRAME_H);
    renderTo(g,FRAME_W,FRAME_H,f.z,f.px,f.py,1);
    out.toBlob(blob=>{
      const a=document.createElement('a');
      a.href=URL.createObjectURL(blob);
      a.download='kasukabe-township-2013x1089.png';
      a.click();
      setTimeout(()=>URL.revokeObjectURL(a.href),2000);
    },'image/png');
  },
  ZONES, HOMES, buildings, props,
  GRID:G, TILE_W:TW, TILE_H:TH,
  ZONE_ORDER: Object.keys(ZONES),
  iso,
  size:{w:FRAME_W,h:FRAME_H},
  roadTiles, walkTiles, terrainAt:tAt, T,
  /** Every signboard as the renderer computes it: the text, the measured box,
   *  and where it was placed. A layout fault in the tags is invisible from
   *  outside the canvas without this. */
  /** Which tags the layout actually drew on the last frame, and which of them
   *  had to be crowded in. Nothing is ever dropped, so `drawn` should always
   *  equal the full label count - if it does not, a building has no sign. */
  /** Show a live word on a place's sign: {zoneId: {text, fg, bg}}. Passing an
   *  empty object clears every badge. */
  setLabelBadges(map){ labelBadges = map || {}; },
  /** What lives out there, by kind - the counterpart of debugLabels(). */
  /** How many sprites are baked into the ground layer. Should equal the
   *  far-field prop count and STAY there: it doubling across a rebake is the
   *  signature of the belt being re-planted. */
  farCount(){ return farStatics.length; },
  critterCensus(){ const o={}; for(const c of critters){ (o[c.kind]=o[c.kind]||{n:0,r:c.r}).n++; } return o; },
  get labelReport(){ return { drawn: lastDrawn.slice(), crowded: lastCrowded.slice() }; },
  debugLabels(){
    if(!ctx) return [];
    const g=ctx, scaleUI=1, fs=Math.round(15*scaleUI);
    g.save();
    g.font=`800 ${fs}px "Archivo", system-ui, sans-serif`;
    const out=LABELS().map(L=>{
      const icon=String(L.i||''), text=String(L.t||'');
      const iw=icon?g.measureText(icon).width:0, tw=g.measureText(text).width;
      const inner=iw+(icon&&text?7*scaleUI:0)+tw;
      return { text, icon, zid:L.zid||null,
               iconWidth:iw, textWidth:tw, boxWidth:inner+22*scaleUI,
               leftPad:11*scaleUI, rightPad:11*scaleUI,
               x:L.at.x*zoom+panX, y:L.at.y*zoom+panY };
    });
    g.restore();
    return out;
  },
};


/* --- compatibility surface for the v1 simulation -------------------------
   township-v2.js is the v1 simulation with only its renderer swapped out. It
   addresses the world through these names, so they are published here rather
   than forcing edits through the whole of that file. */
window.GRID_W = G;
window.GRID_H = G;
window.TILE_W = TW;
window.TILE_H = TH;
window.ZONES = ZONES;
window.ZONE_ORDER = Object.keys(ZONES);
// `plans` is what the simulation calls the list of buildings it can be inside
// of. Dwellings carry the fields the home allocator needs.
window.plans = buildings.map(b => Object.assign(b, {
  rooms: b.rooms || [],
  capacity: b.home ? (b.w * b.d > 3.2 ? 4 : 3) : 0,
  tier: b.home ? (b.w * b.d > 3.6 ? 3 : (b.w * b.d > 3.0 ? 2 : 1)) : 0,
  homeLabel: b.home ? (b.w * b.d > 3.6 ? '3 BHK' : (b.w * b.d > 3.0 ? '2 BHK' : '1 BHK')) : null,
}));

})();
