// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { ControlAttempt, cleanExport, visibleElement, getDomDiagnostic, clearDomDiagnostic } from "../../../scripts/e03/ui/collector.js";

const ids = { sessionId: `e03-synthetic-${"a".repeat(32)}`, approvalId: "b".repeat(32), callId: `e03-${"c".repeat(32)}` };
const denied = { type: "tool_result", protocol: 1, session_id: ids.sessionId,
  tool_result: { tool_call_id: ids.callId, is_error: true, content: '{"status":"denied"}' } };
const persisted = { id: 1, role: "tool", tool_call_id: ids.callId, is_error: true, content: '{"status":"denied"}' };
function fixture(extra = {}) {
  let clock = 10;
  const root = document.createElement("div");
  document.body.append(root);
  const attempt = new ControlAttempt({ scenario: "ocioso", ...ids, now: () => clock++,
    postDeny: async () => ({ status: 200, body: { status: "decided" } }),
    readPersisted: async () => persisted,
    root, frames: async () => {}, visible: () => true, timeoutMs: 1000, ...extra });
  return { attempt, root };
}
afterEach(() => { document.body.replaceChildren(); vi.useRealTimers(); });

describe("diagnóstico de visibilidade sem dados da página", () => {
  it("identifica painel fora da área visível sem aceitar o controle", () => {
    clearDomDiagnostic();
    const root = document.createElement("div");document.body.append(root);
    root.textContent = "private-content-must-not-export";
    root.dataset.token = "private-token-must-not-export";
    root.getBoundingClientRect = () => ({ top: innerHeight + 20, bottom: innerHeight + 120, width: 400, height: 100 });
    expect(visibleElement(root, "root")).toBe(false);
    const diagnostic = getDomDiagnostic();
    expect(diagnostic.reason).toBe("outside_viewport");
    expect(diagnostic.phase).toBe("root");
    expect(diagnostic.top).toBe(innerHeight + 20);
    expect(diagnostic.connected).toBe(true);
    expect(JSON.stringify(diagnostic)).not.toMatch(/private-content|private-token|dataset|textContent/);
  });

  it("distingue estado oculto do documento", () => {
    clearDomDiagnostic();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    try {
      const root = document.createElement("div");document.body.append(root);
      expect(visibleElement(root, "root")).toBe(false);
      expect(getDomDiagnostic().reason).toBe("document_not_visible");
      expect(getDomDiagnostic().visibility).toBe("hidden");
    } finally { visibility.mockRestore(); }
  });

  it("limpa evidências anteriores ao iniciar outra rodada", () => {
    visibleElement(null, "root");
    expect(getDomDiagnostic().reason).toBe("detached");
    clearDomDiagnostic();
    expect(getDomDiagnostic()).toBeNull();
  });

  it("preserva a falha DOM e registra exceção sem copiar sua mensagem", async () => {
    clearDomDiagnostic();
    const { attempt } = fixture({ frames: async () => { throw new Error("private-error-must-not-export"); } });
    const pending = attempt.start();await attempt.onEvent(denied);
    const row = await pending;
    expect(row.outcome).toBe("erro");expect(row.error_kind).toBe("dom");
    expect(getDomDiagnostic().reason).toBe("dom_exception");
    expect(JSON.stringify(getDomDiagnostic())).not.toContain("private-error");
  });
});

describe("ACK composto e projeção real no DOM", () => {
  it("não termina só com decided e correlaciona a mutação do resultado", async () => {
    let release;
    const proof = new Promise((resolve) => { release = resolve; });
    const { attempt, root } = fixture({ readPersisted: () => proof });
    let settled = false;
    const result = attempt.start().then((value) => { settled = true; return value; });
    await attempt.onEvent(denied);
    await Promise.resolve();
    expect(settled).toBe(false);
    expect(root.querySelector(`[data-approval-id="${ids.approvalId}"]`)).not.toBeNull();
    release(persisted);
    const row = await result;
    expect(row.outcome).toBe("denied");
    expect(row.persisted_call_id).toBe(ids.callId);
    expect(row.send_ms).toBeLessThanOrEqual(row.stream_ms);
    expect(row.stream_ms).toBeLessThanOrEqual(row.dom_ms);
    expect(row.ack_http_ms).toBeLessThanOrEqual(row.persist_ms);
  });

  it.each([400, 403, 404, 409])("classifica HTTP %s como erro do ensaio", async (status) => {
    const { attempt } = fixture({ postDeny: async () => ({ status, body: { error: "private-text" } }) });
    const row = await attempt.start();
    expect(row.outcome).toBe("erro");
    expect(row.http_status).toBe(status);
    expect(JSON.stringify(row)).not.toContain("private-text");
  });

  it("recusa resultado de outro call_id sem fabricar deny", async () => {
    const { attempt } = fixture();
    const result = attempt.start();
    await attempt.onEvent({ ...denied, tool_result: { ...denied.tool_result, tool_call_id: `e03-${"d".repeat(32)}` } });
    expect((await result).outcome).toBe("erro");
  });

  it("aba oculta não comprova renderização", async () => {
    const { attempt } = fixture({ visible: () => false });
    const result = attempt.start();
    await attempt.onEvent(denied);
    expect((await result).outcome).toBe("erro");
  });

  it("um prazo vencido preserva timeout, mesmo se vier deny depois", async () => {
    vi.useFakeTimers();
    const { attempt } = fixture({ postDeny: () => new Promise(() => {}) });
    const result = attempt.start();
    await vi.advanceTimersByTimeAsync(1001);
    const row = await result;
    expect(row.outcome).toBe("timeout");
    await attempt.onEvent(denied);
    expect(row.outcome).toBe("timeout");
  });

  it("exporta apenas o esquema permitido, descartando dados opacos", async () => {
    const { attempt } = fixture();
    const result = attempt.start();
    await attempt.onEvent(denied);
    const row = await result;
    const exported = cleanExport({ ...row, token: "do-not-export", arbitrary: "private-context" });
    expect(exported.outcome).toBe("denied");
    expect(JSON.stringify(exported)).not.toMatch(/do-not-export|private-context|token/);
  });
});

// O canal recusa frames de outro contexto em vez de transformar erro de protocolo em sucesso.
import { ChatChannel, waitForReport } from "../../../scripts/e03/ui/channel.js";

it("aguarda a consolidação de uma carga que demora mais de 30 segundos", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
  let ready = false, result;
  const pending = waitForReport({ runId: "current", readReport: async () => {
    if (!ready) throw new Error("artifact_unavailable");
    return { run_id: "current", gate: "inconclusive" };
  } }).then(value => { result = value; }, error => { result = error; });
  await vi.advanceTimersByTimeAsync(45_000);
  expect(result).toBeUndefined();
  ready = true;
  await vi.advanceTimersByTimeAsync(500);
  await pending;
  expect(result).toEqual({ run_id: "current", gate: "inconclusive" });
});

it("não aceita relatório de outra rodada e limita a espera", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
  let result;
  const pending = waitForReport({ runId: "current", readReport: async () => ({ run_id: "other" }) })
    .then(value => { result = value; }, error => { result = error; });
  await vi.advanceTimersByTimeAsync(900_000);
  await pending;
  expect(result).toBeInstanceOf(Error);
  expect(result.message).toBe("report_not_ready");
});

it("propaga falha inesperada ao ler o relatório", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
  let result;
  const pending = waitForReport({ runId: "current", readReport: async () => {
    throw new SyntaxError("invalid JSON");
  } }).then(value => { result = value; }, error => { result = error; });
  await vi.advanceTimersByTimeAsync(35_000);
  await pending;
  expect(result).toBeInstanceOf(SyntaxError);
});

it.each([null, { protocol: 2 }, { protocol: 1, session_id: "other", type: "turn_end" }])("recusa frame incompatível %j", async (payload) => {
  const socket = new EventTarget();
  socket.send = vi.fn(); socket.close = vi.fn();
  const channel = new ChatChannel(socket);
  channel.send(ids.sessionId, "e03-synthetic:controle");
  const result = channel.wait("turn_end", 100).then(() => "unexpected", () => "rejected");
  socket.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(payload) }));
  expect(await result).toBe("rejected");
  channel.close();
});
