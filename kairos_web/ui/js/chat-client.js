/* Cliente do protocolo de Chat v1. Eventos futuros são aditivos e ignoráveis. */

const PROTOCOL = 1;
const KNOWN_EVENTS = new Set([
  "turn_start",
  "delta",
  "reasoning_delta",
  "tool_call",
  "tool_result",
  "usage",
  "turn_error",
  "turn_end",
]);

export class ChatClient {
  constructor({ onEvent = () => {}, onAuthLost = () => {}, onClose = () => {} } = {}) {
    this.onEvent = onEvent;
    this.onAuthLost = onAuthLost;
    this.onClose = onClose;
    this.socket = null;
    this.turnStarted = false;
  }

  connect({ token, WebSocketImpl = WebSocket } = {}) {
    if (!token) throw new Error("ticket WebSocket obrigatório");
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${location.host}/ws/chat?token=${encodeURIComponent(token)}`;
    const socket = new WebSocketImpl(url);
    this.socket = socket;
    socket.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      this.accept(payload);
    });
    socket.addEventListener("close", (event) => {
      this.socket = null;
      if (event.code === 4401) this.onAuthLost();
      this.onClose({ code: event.code, reconnectable: !this.turnStarted && event.code !== 4401 });
    });
    return socket;
  }

  accept(event) {
    if (!event || event.protocol !== PROTOCOL || !KNOWN_EVENTS.has(event.type)) return false;
    if (event.type === "turn_start") this.turnStarted = true;
    if (event.type === "turn_end" || event.type === "turn_error") this.turnStarted = false;
    this.onEvent(event);
    return true;
  }

  sendMessage({ sessionId, content, provider, model, profile, activity, parameters } = {}) {
    if (!this.socket || this.socket.readyState !== 1) throw new Error("Chat desconectado");
    const message = {
      type: "message",
      protocol: PROTOCOL,
      session_id: sessionId,
      content,
      ...(provider && model ? { provider, model } : {}),
      ...(profile ? { profile } : {}),
      ...(activity ? { activity } : {}),
      ...(parameters ? { parameters } : {}),
    };
    this.turnStarted = false;
    this.socket.send(JSON.stringify(message));
  }

  close() {
    this.socket?.close(1000, "view closed");
    this.socket = null;
  }
}
