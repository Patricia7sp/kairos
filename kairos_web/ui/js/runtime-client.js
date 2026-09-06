/* Assinatura do Agent Runtime. Sair da tela encerra apenas o WebSocket; o host
 * continua dono do turno e nenhum comando é reenviado automaticamente. */

import { api } from "./api.js";

const defaultDelay = (attempt) => Math.min(10_000, 500 * (2 ** attempt));

export class RuntimeClient {
  constructor({
    ticket = () => api.wsTicket(),
    getCursor = () => null,
    reconnectDelay = defaultDelay,
    WebSocketImpl = globalThis.WebSocket,
    onEvent = () => {},
    onOpen = () => {},
    onClose = () => {},
    onError = () => {},
  } = {}) {
    this.ticket = ticket;
    this.getCursor = getCursor;
    this.reconnectDelay = reconnectDelay;
    this.WebSocketImpl = WebSocketImpl;
    this.onEvent = onEvent;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this.onError = onError;
    this.sessionId = null;
    this.socket = null;
    this.timer = null;
    this.attempt = 0;
    this.disposed = false;
    this.generation = 0;
  }

  async connect(sessionId) {
    if (!sessionId) throw new Error("sessão de runtime obrigatória");
    this.sessionId = sessionId;
    this.disposed = false;
    const generation = ++this.generation;
    const result = await this.ticket();
    if (this.disposed || generation !== this.generation) return null;
    if (!result?.ticket) throw new Error("ticket WebSocket obrigatório");
    const protocol = globalThis.location?.protocol === "https:" ? "wss" : "ws";
    const host = globalThis.location?.host || "localhost";
    const socket = new this.WebSocketImpl(
      `${protocol}://${host}/ws/runtime?token=${encodeURIComponent(result.ticket)}`,
    );
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (this.disposed || socket !== this.socket) return;
      this.attempt = 0;
      const cursor = this.getCursor();
      socket.send(JSON.stringify({
        type: "subscribe",
        session_id: this.sessionId,
        ...(cursor ? { cursor } : {}),
      }));
      this.onOpen();
    });
    socket.addEventListener("message", (message) => {
      if (this.disposed || socket !== this.socket) return;
      let value;
      try {
        value = JSON.parse(message.data);
      } catch {
        return;
      }
      if (value?.error) this.onError(value.error);
      else this.onEvent(value);
    });
    socket.addEventListener("close", (event) => {
      if (socket === this.socket) this.socket = null;
      if (this.disposed || generation !== this.generation) return;
      const reconnectable = event.code !== 4401;
      this.onClose({ code: event.code, reconnectable });
      if (!reconnectable) return;
      const wait = this.reconnectDelay(this.attempt++);
      this.timer = setTimeout(() => {
        this.timer = null;
        void this.connect(this.sessionId).catch((error) => this.onError(error));
      }, wait);
    });
    return socket;
  }

  reconnect() {
    if (this.disposed) return;
    const socket = this.socket;
    if (socket) socket.close(1012, "reconcile");
    else void this.connect(this.sessionId).catch((error) => this.onError(error));
  }

  dispose() {
    this.disposed = true;
    this.generation += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) socket.close(1000, "view closed");
  }
}
