import { emit } from "./bus.js";
import { dayNightTint } from "./daynight.js";

export const TILE_WIDTH = 32;
const WALK_DURATION_MS = 550;
const UTTERANCE_DISPLAY_MS = 6000;
const BUILDING_LABEL_DISPLAY_MS = 8000;

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

    this.nightOverlay = this.add
      .rectangle(0, 0, map.widthInPixels, map.heightInPixels, 0x0b1230, 0)
      .setOrigin(0, 0)
      .setDepth(1000);
    this.resizeNightOverlay = () => {};

    this.buildingMarkers = [];

    for (const summary of this.initialSnapshot.agents) {
      this.spawnAgent(summary);
    }
    this.applyTint(this.initialSnapshot.tick);

    this.input.on("wheel", (pointer, _objects, _dx, dy) => {
      const before = camera.getWorldPoint(pointer.x, pointer.y);
      const next = Phaser.Math.Clamp(camera.zoom - dy * 0.0016, 0.25, 3);
      camera.setZoom(next);
      this.resizeNightOverlay();
      const after = camera.getWorldPoint(pointer.x, pointer.y);
      camera.scrollX += before.x - after.x;
      camera.scrollY += before.y - after.y;
    });

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

    this.mapLayerObjects = layers;
    this.map = map;
    this.mapWidthPixels = map.widthInPixels;
    this.mapHeightPixels = map.heightInPixels;
    return map;
  }

  rebuildMapInPlace() {
    return new Promise((resolve, reject) => {
      this.cache.tilemap.remove("map");

      const cleanup = () => {
        this.load.off(Phaser.Loader.Events.COMPLETE, onComplete);
        this.load.off(Phaser.Loader.Events.FILE_LOAD_ERROR, onError);
      };
      const onComplete = () => {
        cleanup();
        try {
          const camera = this.cameras.main;
          const prevScrollX = camera.scrollX;
          const prevScrollY = camera.scrollY;
          const prevZoom = camera.zoom;

          for (const layer of this.mapLayerObjects) layer.destroy();
          this.mapLayerObjects = [];
          this.map.destroy();

          const map = this.createMapLayers();
          camera.setBounds(0, 0, map.widthInPixels, map.heightInPixels);
          camera.setZoom(prevZoom);
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
      this.load.tilemapTiledJSON("map", `/api/map?rebuild=${Date.now()}`);
      this.load.start();
    });
  }

  addBuildingMarker(payload) {
    const tileX = payload.tile_x;
    const tileY = payload.tile_y;
    if (tileX === undefined || tileX === null || tileY === undefined || tileY === null) {
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

  moveAgent(agentId, tileX, tileY) {
    const entry = this.agents.get(agentId);
    if (!entry) return;
    entry.moveQueue.push({ x: tileX, y: tileY });
    if (!entry.isMoving) {
      this.playNextQueuedMove(entry);
    }
  }

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
    this.nightOverlay.setFillStyle(color);
    this.nightOverlay.setAlpha(alpha);
  }

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
      case "world.building.constructed":
      case "world.building.construction_started":
      case "world.building.completed":
        this.addBuildingMarker(event.payload);
        break;
      case "world.land.expanded":
        emit("map-rebuild-start", {});
        this.rebuildMapInPlace().then(
          () => emit("map-rebuild-complete", {}),
          (err) => emit("map-rebuild-failed", { message: err.message })
        );
        break;
      default:
        break;
    }
  }
}
