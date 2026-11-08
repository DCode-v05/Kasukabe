// Reconnecting WebSocket client for game/server.py's `/ws` relay
// endpoint. Every parsed frame is republished on the shared bus as a
// "wire-event" (see bus.js) - this module knows nothing about Phaser,
// the inspector panel, or agent state; it only turns socket frames into
// bus events and reports connection status the same way.

import { emit } from "./bus.js";

const RECONNECT_DELAY_MS = 2000;

export function connectEvents() {
  const url = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;

  function open() {
    emit("connection", { status: "connecting" });
    const socket = new WebSocket(url);

    socket.addEventListener("open", () => {
      emit("connection", { status: "open" });
    });

    socket.addEventListener("message", (message) => {
      let event;
      try {
        event = JSON.parse(message.data);
      } catch (err) {
        console.error("game ws: dropping unparseable frame", err);
        return;
      }
      emit("wire-event", event);
    });

    socket.addEventListener("close", () => {
      emit("connection", { status: "closed" });
      setTimeout(open, RECONNECT_DELAY_MS);
    });

    socket.addEventListener("error", () => {
      // "close" always follows "error" for a browser WebSocket, so the
      // reconnect scheduling above is enough - this listener only exists
      // to stop an unhandled-error console spam on a refused connection.
      socket.close();
    });
  }

  open();
}
