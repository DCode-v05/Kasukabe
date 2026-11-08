// Tiny shared pub/sub so ws_client.js, town.js, inspector.js, and main.js
// can talk to each other without reaching into each other's module scope
// or polluting `window`. One EventTarget, string event names, `detail`
// carries the payload - the same shape browsers already use for custom
// DOM events, just not attached to any element.
//
// Events used across this app:
//   "wire-event"      detail: the parsed WireEvent from the game WS
//                      (contracts.WireEvent's JSON shape: type/tick/payload)
//   "connection"      detail: {status: "connecting"|"open"|"closed"}
//   "agent-selected"  detail: {agentId: string}  (town.js -> inspector.js,
//                      fired when a sprite is clicked)
//   "scene-ready"     detail: {}  (town.js -> main.js, fired once the
//                      Phaser scene finished create())
//   "map-rebuild-start"    detail: {}  (town.js -> main.js, a
//                          world.land.expanded rebuild started)
//   "map-rebuild-complete" detail: {}  (town.js -> main.js, the in-place
//                          tilemap rebuild finished)
//   "map-rebuild-failed"   detail: {message: string}  (town.js -> main.js,
//                          the /api/map re-fetch or rebuild threw)

export const bus = new EventTarget();

export function emit(name, detail) {
  bus.dispatchEvent(new CustomEvent(name, { detail }));
}

export function on(name, handler) {
  bus.addEventListener(name, handler);
}
