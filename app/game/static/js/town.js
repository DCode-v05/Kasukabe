// The Phaser 3 scene - lifted from smallville's
// environment/frontend_server/templates/demo/main_script.html (preload's
// tileset image list, create()'s tilemap layer construction, the
// per-persona atlas + speech-bubble setup) and adapted from that file's
// poll-driven replay loop to an event-driven one: instead of asking the
// backend "what happened at step N" on a timer, this scene reacts to
// WireEvents pushed over the WebSocket relay (ws_client.js -> bus.js).
//
// Movement in the source repo was frame-stepped (advance exactly
// `movement_speed` pixels per Phaser update() call until the tile target
// is reached, because the backend only ever said "the next tile is
// (x,y)"). world/'s `agent.moved` event is the same shape (one
// destination per event, no intermediate frames), so this file reaches
// the same visual result with a Phaser tween instead of a hand-rolled
// per-frame stepper - fewer moving parts, same "walk from tile A to tile
// B, playing the matching directional animation" behavior.
//
// world.core.movement.MovementSystem (the agent-pathing track) now walks
// an agent to a destination tile by tile across ticks - DESIGN.md's
// "arrival is not instant" - which means one `agent.moved` event per
// tile of the route, not one event per whole journey. A page that just
// called `this.tweens.add()` again on every event (the source repo's
// only ever handled one hop at a time) would start a second tween on the
// same sprite's x/y while the first tile-hop's tween is still finishing,
// and Phaser runs both concurrently rather than queuing them - the
// sprite visibly jitters between two competing targets. moveAgent() below
// queues each tile target per agent instead and only ever runs one tween
// at a time per sprite, draining the queue as fast as events arrive so a
// fast burst of steps (a mock/replay run with no real-time pacing between
// ticks) still plays as one smooth, continuous walk, and a slow, real
// tick cadence (DESIGN.md section 9's ~40s/tick under live Bedrock
// throughput) just shows a tile-hop, a brief idle pose, then the next
// tile-hop, which is the correct, honest depiction of an agent standing
// still between ticks it hasn't yet been granted another step on.
//
// Map-changing events (see KNOWN_ISSUES.md's now-fixed reload loop) are
// handled two different ways, matched to how much of the map actually
// changed:
//   - `world.building.*` (a single new structure) never touches the
//     tilemap at all - the wire payload carries a point, not a
//     footprint, so `addBuildingMarker()` drops an incremental sprite +
//     label into the already-running scene and lets it settle into a
//     permanent map marker. Cheap, additive, no rebuild.
//   - `world.land.expanded` (the Tiled JSON itself grew - new width/
//     height, new sectors) genuinely needs a new tilemap, so
//     `rebuildMapInPlace()` re-fetches `/api/map` and swaps the tilemap
//     layers on the live scene - never `window.location.reload()`, so
//     the Phaser.Game instance, agent sprites, camera state, and every
//     other module's state (inspector selection, connection log)
//     survive untouched.

import { emit } from "./bus.js";
import { dayNightTint } from "./daynight.js";

export const TILE_WIDTH = 32;
const WALK_DURATION_MS = 550;
const UTTERANCE_DISPLAY_MS = 6000;
// How long a freshly-constructed building's name label floats above its
// marker before fading out. The marker square itself (see
// addBuildingMarker()) stays forever - a constructed building is a
// permanent fact about the town, so the map should keep showing it.
const BUILDING_LABEL_DISPLAY_MS = 8000;

// Same 25 names as game/position.py's SPRITE_ROSTER - the Apache-2.0
// character spritesheets lifted from smallville/. Every sprite_key a
// snapshot or WireEvent can name is one of these; town.js does not
// discover the roster dynamically; it preloads all 25 atlases up front
// (25 small PNGs, negligible load cost) exactly like the source repo's
// demo.html preloaded one atlas per named persona.
export const SPRITE_ROSTER = [
  "Abigail_Chen", "Adam_Smith", "Arthur_Burton", "Ayesha_Khan", "Carlos_Gomez",
  "Carmen_Ortiz", "Eddy_Lin", "Francisco_Lopez", "Giorgio_Rossi", "Hailey_Johnson",
  "Isabella_Rodriguez", "Jane_Moreno", "Jennifer_Moore", "John_Lin", "Klaus_Mueller",
  "Latoya_Williams", "Maria_Lopez", "Mei_Lin", "Rajiv_Patel", "Ryan_Park",
  "Sam_Moore", "Tamara_Taylor", "Tom_Moreno", "Wolfgang_Schulz", "Yuriko_Yamamoto",
];

function directionOf(dx, dy) {
  if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? "right" : "left";
  if (dy !== 0) return dy > 0 ? "down" : "up";
  return null;
}

export class TownScene extends Phaser.Scene {
  constructor(initialSnapshot) {
    super("Town");
    this.initialSnapshot = initialSnapshot;
    // agent_id -> { sprite, nameLabel, speechBubble, speechText,
    //               utteranceTimer, facing }
    this.agents = new Map();
  }

  preload() {
    window.__preloadCallCount = (window.__preloadCallCount || 0) + 1;
    this.load.crossOrigin = "";

    this.load.image("blocks_1", "assets/the_ville/visuals/map_assets/blocks/blocks_1.png");
    this.load.image("walls", "assets/the_ville/visuals/map_assets/v1/Room_Builder_32x32.png");
    this.load.image("interiors_pt1", "assets/the_ville/visuals/map_assets/v1/interiors_pt1.png");
    this.load.image("interiors_pt2", "assets/the_ville/visuals/map_assets/v1/interiors_pt2.png");
    this.load.image("interiors_pt3", "assets/the_ville/visuals/map_assets/v1/interiors_pt3.png");
    this.load.image("interiors_pt4", "assets/the_ville/visuals/map_assets/v1/interiors_pt4.png");
    this.load.image("interiors_pt5", "assets/the_ville/visuals/map_assets/v1/interiors_pt5.png");
    this.load.image(
      "CuteRPG_Field_B",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Field_B.png"
    );
    this.load.image(
      "CuteRPG_Field_C",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Field_C.png"
    );
    this.load.image(
      "CuteRPG_Harbor_C",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Harbor_C.png"
    );
    this.load.image(
      "CuteRPG_Village_B",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Village_B.png"
    );
    this.load.image(
      "CuteRPG_Forest_B",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Forest_B.png"
    );
    this.load.image(
      "CuteRPG_Desert_C",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Desert_C.png"
    );
    this.load.image(
      "CuteRPG_Mountains_B",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Mountains_B.png"
    );
    this.load.image(
      "CuteRPG_Desert_B",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Desert_B.png"
    );
    this.load.image(
      "CuteRPG_Forest_C",
      "assets/the_ville/visuals/map_assets/cute_rpg_word_VXAce/tilesets/CuteRPG_Forest_C.png"
    );

    // Fetched from game/server.py's /api/map, not the static asset path
    // directly - see that route's docstring: it always reflects world/'s
    // current (possibly land-expanded) map, falling back to the same
    // static file this scene's tileset images live next to when running
    // against MockWorldClient.
    this.load.tilemapTiledJSON("map", "/api/map");

    for (const spriteKey of SPRITE_ROSTER) {
      this.load.atlas(
        spriteKey,
        `assets/characters/${spriteKey}.png`,
        "assets/characters/atlas.json"
      );
    }

    this.load.image("speech_bubble", "assets/speech_bubble/v3.png");
  }

  create() {
    window.__createCallCount = (window.__createCallCount || 0) + 1;
    const map = this.createMapLayers();

    const camera = this.cameras.main;
    camera.setBounds(0, 0, map.widthInPixels, map.heightInPixels);
    camera.centerOn(map.widthInPixels / 2, map.heightInPixels / 2);
    this.cursors = this.input.keyboard.createCursorKeys();

    this.anims_created = new Set();
    for (const spriteKey of SPRITE_ROSTER) {
      this.createWalkAnimations(spriteKey);
    }

    // Day/night overlay: fixed to the camera (scrollFactor 0) so it
    // tints the whole viewport uniformly regardless of where the camera
    // is looking, redrawn on every world.tick.advanced event.
    // scrollFactor(0) pins it to the camera, but Phaser STILL applies camera
    // zoom to scroll-fixed objects: at zoom 0.5 a viewport-sized rectangle
    // covers only the middle quarter of the screen, which is the black block
    // that appeared over the centre of the map when zoomed out. Centring it
    // and re-sizing to viewport/zoom keeps it covering the whole viewport at
    // every zoom level.
    // Covers the WHOLE MAP in world space, not the viewport. The previous
    // screen-fixed version (scrollFactor 0, sized to the viewport) was still
    // scaled by camera zoom, so zooming out shrank it into a dark block
    // sitting over the middle of the town. A world-sized rectangle needs no
    // zoom compensation at all: it tints exactly the terrain, always.
    this.nightOverlay = this.add
      .rectangle(0, 0, map.widthInPixels, map.heightInPixels, 0x0b1230, 0)
      .setOrigin(0, 0)
      .setDepth(1000);
    this.resizeNightOverlay = () => {};

    // Buildings constructed before this page load have no wire event to
    // replay (the marker is drawn live off world.building.* - see
    // addBuildingMarker()), so a fresh page load simply starts without
    // them rather than drawing stale markers from no data. This matches
    // agent.spawned's own "forward-compatible, snapshot is the reliable
    // path" comment below.
    this.buildingMarkers = [];

    for (const summary of this.initialSnapshot.agents) {
      this.spawnAgent(summary);
    }
    this.applyTint(this.initialSnapshot.tick);

    // Zoom about the cursor, Google-Maps style: keep the world point under
    // the pointer fixed instead of always zooming to the camera centre.
    this.input.on("wheel", (pointer, _objects, _dx, dy) => {
      const before = camera.getWorldPoint(pointer.x, pointer.y);
      const next = Phaser.Math.Clamp(camera.zoom - dy * 0.0016, 0.25, 3);
      camera.setZoom(next);
      this.resizeNightOverlay();
      const after = camera.getWorldPoint(pointer.x, pointer.y);
      camera.scrollX += before.x - after.x;
      camera.scrollY += before.y - after.y;
    });

    // Click-drag to pan. Phaser has no built-in map drag, so this tracks the
    // pointer manually and moves the camera by the delta divided by zoom (a
    // 10px screen drag must move 10px of SCREEN, which is 10/zoom world px).
    // dragMoved gates the click-to-inspect handler so a drag never registers
    // as a click on whatever sprite happened to be under the cursor.
    this.isDragging = false;
    this.dragMoved = false;
    this.input.on("pointerdown", (pointer) => {
      this.isDragging = true;
      this.dragMoved = false;
      this.dragFrom = { x: pointer.x, y: pointer.y };
      this.game.canvas.style.cursor = "grabbing";
    });
    this.input.on("pointermove", (pointer) => {
      if (!this.isDragging) return;
      const dx = pointer.x - this.dragFrom.x;
      const dy = pointer.y - this.dragFrom.y;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) this.dragMoved = true;
      camera.scrollX -= dx / camera.zoom;
      camera.scrollY -= dy / camera.zoom;
      this.dragFrom = { x: pointer.x, y: pointer.y };
    });
    const endDrag = () => {
      this.isDragging = false;
      this.game.canvas.style.cursor = "grab";
    };
    this.input.on("pointerup", endDrag);
    this.input.on("pointerupoutside", endDrag);
    this.game.canvas.style.cursor = "grab";

    window.__debugScene = this;

    emit("scene-ready", {});
  }

  // Builds a fresh Tilemap + every layer from whatever Tiled JSON is
  // currently sitting in the "map" cache entry, and points this.map at
  // it. Called once from create() and again from rebuildMapInPlace()
  // after a world.land.expanded re-fetch - the only difference between
  // "first draw" and "redraw after land growth" is which JSON was in the
  // cache when this ran, so both paths share this one method rather than
  // risking the two drifting apart.
  createMapLayers() {
    const map = this.make.tilemap({ key: "map" });

    const collisions = map.addTilesetImage("blocks", "blocks_1");
    const walls = map.addTilesetImage("Room_Builder_32x32", "walls");
    const interiors_pt1 = map.addTilesetImage("interiors_pt1", "interiors_pt1");
    const interiors_pt2 = map.addTilesetImage("interiors_pt2", "interiors_pt2");
    const interiors_pt3 = map.addTilesetImage("interiors_pt3", "interiors_pt3");
    const interiors_pt4 = map.addTilesetImage("interiors_pt4", "interiors_pt4");
    const interiors_pt5 = map.addTilesetImage("interiors_pt5", "interiors_pt5");
    const CuteRPG_Field_B = map.addTilesetImage("CuteRPG_Field_B", "CuteRPG_Field_B");
    const CuteRPG_Field_C = map.addTilesetImage("CuteRPG_Field_C", "CuteRPG_Field_C");
    const CuteRPG_Harbor_C = map.addTilesetImage("CuteRPG_Harbor_C", "CuteRPG_Harbor_C");
    const CuteRPG_Village_B = map.addTilesetImage("CuteRPG_Village_B", "CuteRPG_Village_B");
    const CuteRPG_Forest_B = map.addTilesetImage("CuteRPG_Forest_B", "CuteRPG_Forest_B");
    const CuteRPG_Desert_C = map.addTilesetImage("CuteRPG_Desert_C", "CuteRPG_Desert_C");
    const CuteRPG_Mountains_B = map.addTilesetImage("CuteRPG_Mountains_B", "CuteRPG_Mountains_B");
    const CuteRPG_Desert_B = map.addTilesetImage("CuteRPG_Desert_B", "CuteRPG_Desert_B");
    const CuteRPG_Forest_C = map.addTilesetImage("CuteRPG_Forest_C", "CuteRPG_Forest_C");

    const tilesetGroup1 = [
      CuteRPG_Field_B, CuteRPG_Field_C, CuteRPG_Harbor_C, CuteRPG_Village_B,
      CuteRPG_Forest_B, CuteRPG_Desert_C, CuteRPG_Mountains_B, CuteRPG_Desert_B, CuteRPG_Forest_C,
      interiors_pt1, interiors_pt2, interiors_pt3, interiors_pt4, interiors_pt5, walls,
    ];
    const layers = [];
    layers.push(map.createLayer("Bottom Ground", tilesetGroup1, 0, 0));
    layers.push(map.createLayer("Exterior Ground", tilesetGroup1, 0, 0));
    layers.push(map.createLayer("Exterior Decoration L1", tilesetGroup1, 0, 0));
    layers.push(map.createLayer("Exterior Decoration L2", tilesetGroup1, 0, 0));
    layers.push(map.createLayer("Interior Ground", tilesetGroup1, 0, 0));
    layers.push(map.createLayer("Wall", [CuteRPG_Field_C, walls], 0, 0));
    layers.push(map.createLayer("Interior Furniture L1", tilesetGroup1, 0, 0));
    layers.push(map.createLayer("Interior Furniture L2 ", tilesetGroup1, 0, 0));
    const foregroundL1 = map.createLayer("Foreground L1", tilesetGroup1, 0, 0);
    const foregroundL2 = map.createLayer("Foreground L2", tilesetGroup1, 0, 0);
    foregroundL1.setDepth(2);
    foregroundL2.setDepth(2);
    layers.push(foregroundL1, foregroundL2);

    const collisionsLayer = map.createLayer("Collisions", collisions, 0, 0);
    collisionsLayer.setCollisionByProperty({ collide: true });
    collisionsLayer.setDepth(-1);
    layers.push(collisionsLayer);

    // Tracked explicitly (not just left to map.destroy()'s own cleanup)
    // so rebuildMapInPlace() can guarantee every old layer GameObject is
    // gone before the new set is created - belt-and-suspenders against
    // leaking display-list entries across many land-expansion events
    // over a multi-hour run.
    this.mapLayerObjects = layers;
    this.map = map;
    this.mapWidthPixels = map.widthInPixels;
    this.mapHeightPixels = map.heightInPixels;
    return map;
  }

  // Re-fetches the Tiled JSON from /api/map and swaps in a brand new set
  // of tilemap layers on this already-running scene - the in-place
  // alternative to window.location.reload() that world.land.expanded
  // used to trigger (KNOWN_ISSUES.md). Returns a Promise so callers
  // (handleWireEvent below) can tell success from failure and report it,
  // instead of a reload that either silently worked or silently didn't.
  rebuildMapInPlace() {
    return new Promise((resolve, reject) => {
      // Drop the stale cache entry first: Phaser's tilemap cache keys on
      // "map" regardless of URL, so without this the loader below would
      // see the key already populated and skip the fetch entirely.
      this.cache.tilemap.remove("map");

      const cleanup = () => {
        this.load.off(Phaser.Loader.Events.COMPLETE, onComplete);
        this.load.off(Phaser.Loader.Events.FILE_LOAD_ERROR, onError);
      };
      const onComplete = () => {
        cleanup();
        try {
          const camera = this.cameras.main;
          // Preserve exactly what the operator was looking at - the
          // whole point of "in place" over a reload.
          const prevScrollX = camera.scrollX;
          const prevScrollY = camera.scrollY;
          const prevZoom = camera.zoom;

          for (const layer of this.mapLayerObjects) layer.destroy();
          this.mapLayerObjects = [];
          this.map.destroy();

          const map = this.createMapLayers();
          camera.setBounds(0, 0, map.widthInPixels, map.heightInPixels);
          camera.setZoom(prevZoom);
          // Camera bounds clamp scrollX/scrollY automatically on the next
          // render pass, so simply restoring the previous scroll (even if
          // it now sits outside the grown map's old edge) lands the
          // camera back where the operator was, not recentered.
          camera.scrollX = prevScrollX;
          camera.scrollY = prevScrollY;
          resolve(map);
        } catch (err) {
          reject(err);
        }
      };
      const onError = (file) => {
        cleanup();
        reject(new Error(`failed to reload the tilemap (${file.key}) from /api/map`));
      };

      this.load.once(Phaser.Loader.Events.COMPLETE, onComplete);
      this.load.once(Phaser.Loader.Events.FILE_LOAD_ERROR, onError);
      // Cache-busted so an intermediate proxy/browser cache can't hand
      // back the pre-expansion map on a rapid second land.expanded.
      this.load.tilemapTiledJSON("map", `/api/map?rebuild=${Date.now()}`);
      this.load.start();
    });
  }

  // Draws a new building onto the already-running scene without
  // touching the tilemap - see this file's module docstring for why a
  // marker-at-a-point is the correct incremental update given the wire
  // payload's actual shape (a point, never a footprint). The small
  // square is permanent (a constructed building is a lasting fact about
  // the town); only the floating name label above it fades out.
  addBuildingMarker(payload) {
    const tileX = payload.tile_x;
    const tileY = payload.tile_y;
    if (tileX === undefined || tileX === null || tileY === undefined || tileY === null) {
      // No coordinate in this payload (e.g. world/'s real
      // construction_started/completed events - see module docstring).
      // Nothing to draw at; still not a reload, which is the actual bug
      // this handles.
      return;
    }
    const pixel = this.tilePixel(tileX, tileY);
    const name = payload.name ?? payload.purpose ?? "a new building";

    const marker = this.add
      .rectangle(pixel.x, pixel.y - 16, 22, 22, 0x7c9dff, 0.9)
      .setStrokeStyle(2, 0xffffff, 0.9)
      .setDepth(2.5)
      .setAlpha(0);
    const label = this.add
      .text(pixel.x, pixel.y - 30, name, {
        font: "10px sans-serif",
        color: "#ffffff",
        backgroundColor: "#0b1230cc",
        padding: { x: 4, y: 2 },
      })
      .setOrigin(0.5, 1)
      .setDepth(4)
      .setAlpha(0);

    this.buildingMarkers.push(marker);

    this.tweens.add({ targets: [marker, label], alpha: 1, duration: 400, ease: "Sine.easeOut" });
    this.time.delayedCall(BUILDING_LABEL_DISPLAY_MS, () => {
      this.tweens.add({
        targets: label,
        alpha: 0,
        duration: 600,
        ease: "Sine.easeIn",
        onComplete: () => label.destroy(),
      });
    });
  }

  update() {
    const speed = 8;
    const camera = this.cameras.main;
    if (this.cursors.left.isDown) camera.scrollX -= speed;
    if (this.cursors.right.isDown) camera.scrollX += speed;
    if (this.cursors.up.isDown) camera.scrollY -= speed;
    if (this.cursors.down.isDown) camera.scrollY += speed;
  }

  createWalkAnimations(spriteKey) {
    if (this.anims_created.has(spriteKey)) return;
    this.anims_created.add(spriteKey);
    const anims = this.anims;
    for (const dir of ["left", "right", "down", "up"]) {
      anims.create({
        key: `${spriteKey}-${dir}-walk`,
        frames: anims.generateFrameNames(spriteKey, {
          prefix: `${dir}-walk.`,
          start: 0,
          end: 3,
          zeroPad: 3,
        }),
        frameRate: 4,
        repeat: -1,
      });
    }
  }

  tilePixel(tileX, tileY) {
    return {
      x: tileX * TILE_WIDTH + TILE_WIDTH / 2,
      y: tileY * TILE_WIDTH + TILE_WIDTH,
    };
  }

  spawnAgent(summary) {
    if (this.agents.has(summary.agent_id)) return;
    const { tile_x, tile_y } = summary.position;
    if (tile_x === null || tile_x === undefined || tile_y === null || tile_y === undefined) {
      // No coordinate to draw at all (world/ contract not fully wired -
      // see contracts.py's CONTRACT). Skip rather than draw at (0, 0),
      // which would silently stack every under-specified agent in the
      // map's corner.
      return;
    }
    const pixel = this.tilePixel(tile_x, tile_y);
    const spriteKey = SPRITE_ROSTER.includes(summary.sprite_key)
      ? summary.sprite_key
      : SPRITE_ROSTER[0];

    const sprite = this.physics.add
      .sprite(pixel.x, pixel.y, spriteKey, "down")
      .setSize(30, 40)
      .setOffset(0, 0)
      .setInteractive({ useHandCursor: true });
    sprite.displayWidth = 40;
    sprite.scaleY = sprite.scaleX;

    const nameLabel = this.add
      .text(pixel.x, pixel.y - 46, summary.display_name, {
        font: "12px sans-serif",
        color: "#ffffff",
        backgroundColor: "#00000099",
        padding: { x: 4, y: 2 },
      })
      .setOrigin(0.5, 1)
      .setDepth(4);

    const speechBubble = this.add
      .image(pixel.x, pixel.y - 30, "speech_bubble")
      .setDepth(3)
      .setVisible(false);
    speechBubble.displayWidth = 130;
    speechBubble.displayHeight = 58;

    const speechText = this.add
      .text(pixel.x, pixel.y - 42, "", {
        font: "11px sans-serif",
        color: "#000000",
        align: "center",
        wordWrap: { width: 110 },
      })
      .setOrigin(0.5, 0.5)
      .setDepth(4)
      .setVisible(false);

    // pointerUP, not pointerDOWN: a click that was actually the start of a
    // map drag must not select whatever sprite happened to be under it.
    sprite.on("pointerup", () => {
      if (this.dragMoved) return;
      emit("agent-selected", { agentId: summary.agent_id });
      this.cameras.main.pan(sprite.x, sprite.y, 300, "Sine.easeInOut");
    });

    this.agents.set(summary.agent_id, {
      sprite,
      nameLabel,
      speechBubble,
      speechText,
      utteranceTimer: null,
      facing: "down",
      // Queue of pending {x, y} tile targets not yet started, plus
      // whether a tween is currently running toward one of them - see
      // moveAgent()'s own comment for why this exists (one `agent.moved`
      // per tile now, not per whole journey).
      moveQueue: [],
      isMoving: false,
    });

    if (summary.current_utterance) {
      this.showUtterance(summary.agent_id, summary.current_utterance);
    }
  }

  removeAgent(agentId) {
    const entry = this.agents.get(agentId);
    if (!entry) return;
    // A death mid-walk (health.agent.died can land while a queued
    // tile-hop tween is still running) must not leave a tween driving a
    // destroyed sprite's x/y - stop it and drop anything still queued
    // before tearing the sprite down.
    this.tweens.killTweensOf(entry.sprite);
    entry.moveQueue.length = 0;
    entry.isMoving = false;
    entry.sprite.destroy();
    entry.nameLabel.destroy();
    entry.speechBubble.destroy();
    entry.speechText.destroy();
    if (entry.utteranceTimer) entry.utteranceTimer.remove();
    this.agents.delete(agentId);
  }

  // Enqueues one tile-hop and kicks off the queue if nothing is already
  // playing. Never starts a second tween on the same sprite while one is
  // in flight - see this file's module docstring for why that matters
  // now that a multi-tile walk (world.core.movement.MovementSystem) sends
  // one `agent.moved` per tile instead of one per whole journey.
  moveAgent(agentId, tileX, tileY) {
    const entry = this.agents.get(agentId);
    if (!entry) return;
    entry.moveQueue.push({ x: tileX, y: tileY });
    if (!entry.isMoving) {
      this.playNextQueuedMove(entry);
    }
  }

  // Runs exactly one tile-hop's tween, then either immediately starts the
  // next queued hop (a fast burst of ticks arriving back to back plays as
  // one continuous walk cycle with no idle flicker in between) or, once
  // the queue is empty, stops the walk animation and settles the sprite
  // into a standing pose facing the direction it last moved - the same
  // "walked, then waits for the next tick's step" idle state a slow, real
  // tick cadence produces naturally between events.
  playNextQueuedMove(entry) {
    const next = entry.moveQueue.shift();
    if (!next) {
      entry.isMoving = false;
      entry.sprite.anims.stop();
      entry.sprite.setTexture(entry.sprite.texture.key, entry.facing);
      return;
    }
    entry.isMoving = true;

    const target = this.tilePixel(next.x, next.y);
    const dx = target.x - entry.sprite.x;
    const dy = target.y - entry.sprite.y;
    const dir = directionOf(dx, dy);

    if (dir) {
      entry.facing = dir;
      entry.sprite.anims.play(`${entry.sprite.texture.key}-${dir}-walk`, true);
    }

    this.tweens.add({
      targets: entry.sprite,
      x: target.x,
      y: target.y,
      duration: WALK_DURATION_MS,
      ease: "Linear",
      onUpdate: () => {
        entry.nameLabel.setPosition(entry.sprite.x, entry.sprite.y - 46);
        entry.speechBubble.setPosition(entry.sprite.x + 40, entry.sprite.y - 39);
        entry.speechText.setPosition(entry.sprite.x + 40, entry.sprite.y - 42);
      },
      onComplete: () => {
        this.playNextQueuedMove(entry);
      },
    });
  }

  showUtterance(agentId, content) {
    const entry = this.agents.get(agentId);
    if (!entry) return;
    entry.speechText.setText(content);
    entry.speechBubble.setVisible(true);
    entry.speechText.setVisible(true);
    if (entry.utteranceTimer) entry.utteranceTimer.remove();
    entry.utteranceTimer = this.time.delayedCall(UTTERANCE_DISPLAY_MS, () => {
      entry.speechBubble.setVisible(false);
      entry.speechText.setVisible(false);
    });
  }

  applyTint(tick) {
    const { color, alpha } = dayNightTint(tick);
    // THE BLACK CANVAS BUG lived here. Empirically verified (readPixels
    // on the live WebGL drawing buffer, not just a screenshot): a
    // Rectangle GameObject whose only alpha was set via
    // `setFillStyle(color, fillAlpha)` rendered fully OPAQUE in this
    // Phaser 3.55.2/WebGL combination no matter what fillAlpha was -
    // this overlay (screen-covering, scrollFactor 0, depth 1000, i.e.
    // drawn dead last, on top of the map and every sprite) was painting
    // solid navy over the entire view on every single page load,
    // regardless of tick/time of day, which is exactly the reported
    // "black canvas, zero console errors, scene otherwise fully built"
    // symptom. Driving the SAME strength through `setAlpha` (the
    // GameObject's own Components.Alpha opacity, a completely different
    // property from fillAlpha) is what actually made the WebGL
    // compositing respect it - confirmed fixed the same way (readPixels
    // went from solid (0,0,0) to the real map's colors). Fill color is
    // left fully opaque (fillAlpha's default, 1) so setAlpha alone is
    // the one and only knob controlling how strong the day/night dim
    // reads - no double-multiplying the two.
    this.nightOverlay.setFillStyle(color);
    this.nightOverlay.setAlpha(alpha);
  }

  // The one entry point main.js calls for every WireEvent once the scene
  // is ready. Kept as a plain switch (not a dispatch table) - the event
  // vocabulary this scene cares about is small and unlikely to grow much
  // past what DESIGN.md section 9 already names ("positions, utterances,
  // day/night tint, new buildings").
  handleWireEvent(event) {
    switch (event.type) {
      case "world.tick.advanced":
        this.applyTint(event.tick);
        break;
      case "agent.moved": {
        const { agent_id, tile_x, tile_y } = event.payload;
        if (tile_x !== undefined && tile_y !== undefined) {
          this.moveAgent(agent_id, tile_x, tile_y);
        }
        break;
      }
      case "agent.spoke": {
        const { agent_id, content } = event.payload;
        if (agent_id && content) this.showUtterance(agent_id, content);
        break;
      }
      case "agent.spawned": {
        // Forward-compatible: only draws the new agent if world/ sent a
        // full summary-shaped payload (agent_id, display_name,
        // sprite_key, tile_x/tile_y). A world/ build that only sends
        // {"agent_id": "..."} is intentionally a no-op here rather than
        // a crash - main.js's snapshot-driven bootstrap is the reliable
        // path for a fresh page load either way.
        const p = event.payload;
        if (p.agent_id && p.display_name && p.sprite_key && p.tile_x !== undefined) {
          this.spawnAgent({
            agent_id: p.agent_id,
            display_name: p.display_name,
            sprite_key: p.sprite_key,
            position: { tile_x: p.tile_x, tile_y: p.tile_y, location_id: p.location_id ?? null },
            current_action: p.current_action ?? null,
            current_utterance: null,
          });
        }
        break;
      }
      case "agent.died":
      case "health.agent.died": {
        const { agent_id } = event.payload;
        if (agent_id) this.removeAgent(agent_id);
        break;
      }
      // world.building.constructed is mock_world.py's synthetic event
      // name; world/'s real construction.py primitive emits
      // construction_started/completed instead (see this file's module
      // docstring). Handled under all three names so this scene behaves
      // the same incremental, never-reload way regardless of which world
      // client is wired up - addBuildingMarker() itself no-ops cleanly
      // when a payload (like the real backend's) carries no coordinate.
      case "world.building.constructed":
      case "world.building.construction_started":
      case "world.building.completed":
        this.addBuildingMarker(event.payload);
        break;
      case "world.land.expanded":
        // Never a page reload (KNOWN_ISSUES.md) - rebuild the tilemap on
        // this live scene and let main.js's bus listener turn the
        // outcome into a banner message.
        emit("map-rebuild-start", {});
        this.rebuildMapInPlace().then(
          () => emit("map-rebuild-complete", {}),
          (err) => emit("map-rebuild-failed", { message: err.message })
        );
        break;
      default:
        // Every other domain (economy, business, crime, politics,
        // religion, broker, research) has no visual representation in
        // this scene - ignored, not an error.
        break;
    }
  }
}
