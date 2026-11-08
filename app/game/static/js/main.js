// Bootstrap: fetch the initial snapshot, boot the Phaser game with it,
// open the WebSocket relay, and wire the top-bar clock/connection status
// plus the transient town-banner. Nothing else in this file computes
// game state - it only sequences the other modules (app/CONVENTIONS.md:
// "game/ is presentation only").
//
// Map-changing events no longer reload the page (KNOWN_ISSUES.md, now
// deleted - this was the fix): town.js owns applying every WireEvent to
// the live Phaser scene, including new buildings (drawn incrementally,
// see town.js's addBuildingMarker()) and land expansion (tilemap rebuilt
// in place, see town.js's rebuildMapInPlace()). This file's only
// involvement is showing the operator a transient banner for the rare
// land-expansion case, driven by the "map-rebuild-*" bus events town.js
// emits around that call.

import { on } from "./bus.js";
import { fetchSnapshot } from "./api.js";
import { connectEvents } from "./ws_client.js";
import { TownScene } from "./town.js";
import { initInspector } from "./inspector.js";
import { formatGameClock } from "./daynight.js";

const clockEl = document.getElementById("game-clock");
const statusEl = document.getElementById("connection-status");
const bannerEl = document.getElementById("town-banner");

const BANNER_AUTO_HIDE_MS = 3000;

let phaserGame = null;
let sceneReady = false;
let pendingEvents = [];
let bannerHideTimer = null;

function setStatus(status) {
  statusEl.textContent = status;
  statusEl.className = `status status-${status}`;
}

function showBanner(text, { autoHide = false } = {}) {
  if (bannerHideTimer) {
    clearTimeout(bannerHideTimer);
    bannerHideTimer = null;
  }
  bannerEl.textContent = text;
  bannerEl.classList.add("visible");
  if (autoHide) {
    bannerHideTimer = setTimeout(() => bannerEl.classList.remove("visible"), BANNER_AUTO_HIDE_MS);
  }
}

function applyEvent(event) {
  if (!sceneReady) {
    pendingEvents.push(event);
    return;
  }
  phaserGame.scene.getScene("Town").handleWireEvent(event);
}

async function boot() {
  window.__bootCallCount = (window.__bootCallCount || 0) + 1;
  initInspector();
  setStatus("connecting");

  let snapshot;
  try {
    snapshot = await fetchSnapshot();
  } catch (err) {
    showBanner(`Could not load the town: ${err.message}`);
    return;
  }

  clockEl.textContent = formatGameClock(snapshot.tick);

  window.__debugSnapshot = snapshot;
  phaserGame = new Phaser.Game({
    type: Phaser.AUTO,
    parent: "game-container",
    pixelArt: true,
    physics: { default: "arcade", arcade: { gravity: { y: 0 } } },
    scene: new TownScene(snapshot),
    scale: {
      mode: Phaser.Scale.RESIZE,
      parent: "game-container",
      width: "100%",
      height: "100%",
    },
  });
  window.__debugGame = phaserGame;

  on("scene-ready", () => {
    sceneReady = true;
    for (const event of pendingEvents) applyEvent(event);
    pendingEvents = [];
  });

  on("wire-event", (e) => {
    const event = e.detail;
    if (event.type === "world.tick.advanced") {
      clockEl.textContent = formatGameClock(event.tick);
    }
    applyEvent(event);
  });

  // town.js drives the actual land-expansion rebuild (rebuildMapInPlace())
  // and only tells this module how it went, via the bus - this module's
  // job is exactly the transient banner text, nothing about the map
  // itself (see this file's module docstring).
  on("map-rebuild-start", () => showBanner("Kasukabe is growing - redrawing the map..."));
  on("map-rebuild-complete", () => showBanner("Kasukabe has grown.", { autoHide: true }));
  on("map-rebuild-failed", (e) =>
    showBanner(`Could not redraw the grown map: ${e.detail.message}`, { autoHide: true })
  );

  on("connection", (e) => setStatus(e.detail.status));

  connectEvents();
}

boot();
