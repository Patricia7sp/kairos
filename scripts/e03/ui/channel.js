const hex = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
export async function jsonRequest(path, { body, signal } = {}) {
  const response = await fetch(path, { method: body === undefined ? "GET" : "POST",
    credentials: "same-origin", cache: "no-store", signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
  let value;
  try { value = await response.json(); } catch { throw new Error("invalid_response"); }
  return { status: response.status, body: value };
}

export async function waitForReport({ runId, readReport }) {
  const deadline = performance.now() + 300_000;
  while (performance.now() < deadline) {
    try {
      const value = await readReport();
      if (value.run_id === runId) return value;
    } catch (error) {
      if (error?.message !== "artifact_unavailable") throw error;
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("report_not_ready");
}

export class ChatChannel {
  constructor(socket, onDelta) {
    this.activeSession = null;
    this.socket = socket;
    this.waiters = new Set();
    socket.addEventListener("message", (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch { this.rejectAll(); return; }
      if (!event || typeof event !== "object" || event.protocol !== 1
          || event.session_id !== this.activeSession) {
        this.attempt?.fail("erro", "correlation"); this.rejectAll(); return;
      }
      if (event.type === "turn_error") { this.attempt?.fail("erro", "transport"); this.rejectAll(); return; }
      if (event.type === "delta" && onDelta) onDelta();
      if (this.attempt) void this.attempt.onEvent(event).catch(() => this.attempt?.fail("erro", "correlation"));
      for (const waiter of [...this.waiters]) {
        if (waiter.type === event.type) {
          clearTimeout(waiter.timer); this.waiters.delete(waiter); waiter.resolve(event);
        }
      }
    });
    socket.addEventListener("close", () => { this.attempt?.fail("erro", "transport"); this.rejectAll(); });
    socket.addEventListener("error", () => { this.attempt?.fail("erro", "transport"); this.rejectAll(); });
  }
  static async open(onDelta) {
    const result = await jsonRequest("/api/auth/ws-ticket", { body: {}, signal: AbortSignal.timeout(10_000) });
    if (result.status !== 200 || typeof result.body.ticket !== "string") throw new Error("ticket_unavailable");
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/chat?token=${encodeURIComponent(result.body.ticket)}`);
    const channel = new ChatChannel(socket, onDelta);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { socket.close(); reject(new Error("connection_timeout")); }, 10_000);
      socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("connection_failed")); }, { once: true });
    });
    return channel;
  }
  wait(type, timeout = 95_000) {
    return new Promise((resolve, reject) => {
      const waiter = { type, resolve, reject };
      waiter.timer = setTimeout(() => { this.waiters.delete(waiter); reject(new Error("stream_timeout")); }, timeout);
      this.waiters.add(waiter);
    });
  }
  rejectAll() {
    for (const waiter of this.waiters) { clearTimeout(waiter.timer); waiter.reject(new Error("stream_closed")); }
    this.waiters.clear();
  }
  send(sessionId, content, tools = false) {
    this.activeSession = sessionId;
    this.socket.send(JSON.stringify({ type: "message", protocol: 1, session_id: sessionId,
      content, tools, web_search: false, provider: "openrouter", model: "e03-synthetic", idempotency_key: hex() }));
  }
  async plain(sessionId, content) {
    const end = this.wait("turn_end", 20_000);
    this.send(sessionId, content, false);
    await end;
  }
  close() { this.rejectAll(); this.socket.close(); }
}

