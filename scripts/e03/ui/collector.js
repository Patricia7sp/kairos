/* Só a página temporária importa este módulo. Nenhum estado ou segredo do Chat real. */
const fields = ["scenario", "outcome", "session_id", "approval_id", "call_id", "send_ms",
  "ack_http_ms", "persist_ms", "stream_ms", "dom_ms", "persisted_message_id", "persisted_call_id",
  "persisted_status", "dom_visible", "dom_mutation_ms", "http_status", "ack_decided", "error_kind"];
const scenarios = new Set(["ocioso", "conversa_painel", "auditoria", "execucao_isolada", "recuperacao"]);
const errors = new Set(["http", "transport", "correlation", "deadline", "persist", "dom", "clock", "cancelled", "workload"]);
const sid = /^e03-synthetic-[a-f0-9]{32}$/;
const aid = /^[a-f0-9]{32}$/;
const cid = /^e03-[a-f0-9]{32}$/;
const finite = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;

export function cleanExport(value) {
  if (!value || !scenarios.has(value.scenario) || !["denied", "timeout", "erro"].includes(value.outcome)
      || !sid.test(value.session_id) || (value.approval_id != null && !aid.test(value.approval_id))
      || (value.call_id != null && !cid.test(value.call_id))
      || (value.persisted_call_id != null && !cid.test(value.persisted_call_id))
      || (value.persisted_status != null && value.persisted_status !== "denied")
      || (value.error_kind != null && !errors.has(value.error_kind))) throw new Error("invalid_sample");
  const row = Object.fromEntries(fields.map((key) => [key, value[key] ?? null]));
  for (const key of ["send_ms", "ack_http_ms", "persist_ms", "stream_ms", "dom_ms", "dom_mutation_ms"]) {
    if (row[key] != null && !finite(row[key])) row[key] = null;
  }
  row.ack_decided = value.ack_decided === true;
  row.dom_visible = value.dom_visible === true;
  if (!Number.isInteger(row.http_status) || row.http_status < 100 || row.http_status > 599) row.http_status = null;
  if (!Number.isInteger(row.persisted_message_id) || row.persisted_message_id <= 0) row.persisted_message_id = null;
  return row;
}

export const twoFrames = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
export function visibleElement(element) {
  if (document.visibilityState !== "visible" || !element?.isConnected) return false;
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return rect.width > 0 && rect.height > 0 && rect.top < innerHeight && rect.bottom > 0
    && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0;
}

export class ControlAttempt {
  constructor({ scenario, sessionId, approvalId, callId, postDeny, readPersisted, root,
    now = () => performance.now(), frames = twoFrames, visible = visibleElement, timeoutMs = 95_000 }) {
    if (!scenarios.has(scenario) || !sid.test(sessionId) || !aid.test(approvalId) || !cid.test(callId)) throw new Error("invalid_control");
    Object.assign(this, { postDeny, readPersisted, root, now, frames, visible, timeoutMs });
    this.row = { scenario, outcome: "erro", session_id: sessionId, approval_id: approvalId, call_id: callId,
      send_ms: null, ack_http_ms: null, persist_ms: null, stream_ms: null, dom_ms: null,
      persisted_message_id: null, persisted_call_id: null, persisted_status: null, dom_visible: false,
      dom_mutation_ms: null, http_status: null, ack_decided: false, error_kind: null };
    this.controller = new AbortController();
    this.started = false;
    this.settled = false;
  }

  start() {
    if (this.started) throw new Error("control_already_started");
    this.started = true;
    this.result = new Promise((resolve) => { this.resolve = resolve; });
    this.row.send_ms = this.now();
    this.timer = setTimeout(() => this.fail("timeout", "deadline"), this.timeoutMs);
    void this.confirmAck().catch(() => this.fail("erro", "transport"));
    return this.result;
  }

  async confirmAck() {
    const response = await this.postDeny(this.controller.signal);
    if (this.settled) return;
    this.row.http_status = response.status;
    this.row.ack_http_ms = this.now();
    if (response.status !== 200 || response.body?.status !== "decided") {
      this.fail("erro", "http"); return;
    }
    this.row.ack_decided = true;
    const receipt = await this.readPersisted(this.controller.signal);
    if (this.settled) return;
    let status;
    try { status = JSON.parse(receipt?.content).status; } catch { this.fail("erro", "persist"); return; }
    if (receipt?.role !== "tool" || receipt.tool_call_id !== this.row.call_id
        || receipt.is_error !== true || status !== "denied" || !Number.isInteger(receipt.id) || receipt.id <= 0) {
      this.fail("erro", "persist"); return;
    }
    this.row.persist_ms = this.now();
    this.row.persisted_message_id = receipt.id;
    this.row.persisted_call_id = receipt.tool_call_id;
    this.row.persisted_status = "denied";
    this.complete();
  }

  async onEvent(event) {
    if (this.settled || event.type !== "tool_result") return;
    if (!this.started || event.protocol !== 1 || event.session_id !== this.row.session_id
        || event.tool_result?.tool_call_id !== this.row.call_id || this.row.stream_ms !== null) {
      this.fail("erro", "correlation"); return;
    }
    let status;
    try { status = JSON.parse(event.tool_result.content).status; } catch { this.fail("erro", "correlation"); return; }
    if (event.tool_result.is_error !== true || status !== "denied") { this.fail("erro", "correlation"); return; }
    this.row.stream_ms = this.now();
    try {
      await this.observeDom();
    } catch { this.fail("erro", "dom"); }
  }

  async observeDom() {
    if (!this.visible(this.root)) { this.fail("erro", "dom"); return; }
    let element;
    await new Promise((resolve, reject) => {
      const observer = new MutationObserver(() => {
        this.row.dom_mutation_ms = this.now();
        observer.disconnect();
        resolve();
      });
      this.controller.signal.addEventListener("abort", () => {
        observer.disconnect(); reject(new Error("cancelled"));
      }, { once: true });
      observer.observe(this.root, { childList: true, subtree: true });
      element = document.createElement("p");
      element.dataset.approvalId = this.row.approval_id;
      element.dataset.callId = this.row.call_id;
      element.textContent = "Negação sintética persistida recebida.";
      this.root.replaceChildren(element);
    });
    await this.frames();
    if (this.settled) return;
    if (!element.isConnected || element.dataset.approvalId !== this.row.approval_id
        || element.dataset.callId !== this.row.call_id || !this.visible(element)) { this.fail("erro", "dom"); return; }
    this.row.dom_ms = this.now();
    this.row.dom_visible = true;
    this.complete();
  }

  complete() {
    if (this.row.persisted_status !== "denied" || this.row.dom_ms === null) return;
    const r = this.row;
    if (![r.send_ms, r.ack_http_ms, r.persist_ms, r.stream_ms, r.dom_mutation_ms, r.dom_ms].every(finite)
        || r.send_ms > r.ack_http_ms || r.send_ms > r.persist_ms || r.send_ms > r.stream_ms
        || r.stream_ms > r.dom_mutation_ms || r.dom_mutation_ms > r.dom_ms) {
      this.fail("erro", "clock"); return;
    }
    this.finish("denied", null);
  }

  fail(outcome = "erro", kind = "cancelled") { this.finish(outcome, kind); }
  finish(outcome, kind) {
    if (this.settled) return;
    this.settled = true;
    clearTimeout(this.timer);
    this.row.outcome = outcome;
    this.row.error_kind = kind;
    this.resolve(cleanExport(this.row));
    this.controller.abort();
  }
}
