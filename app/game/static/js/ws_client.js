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
      socket.close();
    });
  }

  open();
}
