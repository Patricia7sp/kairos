// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error The shipped SPA uses native JavaScript modules.
import { chatView, initialTurnState, reduceTurn, turnMarkup } from "../../../kairos_web/ui/js/views/chat.js";
// @ts-expect-error The shipped SPA uses native JavaScript modules.
import { ChatClient } from "../../../kairos_web/ui/js/chat-client.js";

type RecordValue = Record<string, any>;

class Socket {
  static current: Socket;
  readyState = 0;
  sent: RecordValue[] = [];
  listeners = new Map<string, Array<(event: any) => void>>();
  constructor() { Socket.current = this; }
  addEventListener(name: string, listener: (event: any) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) || []), listener]);
  }
  emit(name: string, event: any = {}) {
    if (name === "open") this.readyState = 1;
    this.listeners.get(name)?.forEach((listener) => listener(event));
  }
  event(payload: RecordValue) {
    this.emit("message", { data: JSON.stringify({ protocol: 1, ...payload }) });
  }
  send(value: string) { this.sent.push(JSON.parse(value)); }
  close() { this.readyState = 3; }
}

const toolsModel = {
  id: "reasoner", provider: "openai", name: "Reasoner", is_free: false,
  capabilities: { chat: true, tools: true },
};
const requests: Array<{ path: string; method: string; body: any }> = [];
function backend() {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ path, method, body });
    let payload: RecordValue;
    if (path === "/api/providers") payload = { providers: [
      { id: "openai", configured: true, requires_credential: true },
    ] };
    else if (path.startsWith("/api/models")) payload = {
      default_provider: "openai", default_model: "reasoner", models: [toolsModel],
    };
    else if (path.startsWith("/api/sessions?")) payload = { sessions: [{ id: "saved" }] };
    else if (path === "/api/sessions/saved") payload = { id: "saved", selection: {
      provider: "openai", model: "reasoner", parameters: {} } };
    else if (path === "/api/sessions/saved/messages") payload = { messages: [] };
    else if (path === "/api/auth/ws-ticket") payload = { ticket: "test-ticket" };
    else if (path === "/api/chat/sessions/saved/tool-approvals/appr-1") payload = { status: "decided" };
    else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  });
}

let cleanup: (() => void) | undefined;
async function mount() {
  const root = document.createElement("main");
  document.body.append(root);
  cleanup = await chatView(root, {});
  Socket.current.emit("open");
  return root;
}
function submit(root: HTMLElement) {
  root.querySelector<HTMLTextAreaElement>("textarea")!.value = "Rode o comando";
  root.querySelector("form")!.dispatchEvent(new Event("submit", { cancelable: true }));
}

beforeEach(() => {
  requests.length = 0;
  sessionStorage.clear();
  document.body.innerHTML = "";
  location.hash = "#/chat?session=saved";
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
});
afterEach(() => { cleanup?.(); cleanup = undefined; vi.unstubAllGlobals(); });

describe("aprovação por turno das ferramentas do Chat", () => {
  it("marca a chamada mutadora como requerendo aprovação", () => {
    let state = reduceTurn(initialTurnState(), { protocol: 1, type: "tool_call",
      tool_call: { id: "tool-1", name: "bash", arguments: '{"command":"ls"}' } });
    state = reduceTurn(state, { protocol: 1, type: "tool_approval_request", approval_id: "appr-1",
      tool_call: { id: "tool-1", name: "bash", arguments: '{"command":"ls"}' } });
    expect(state.tools[0]).toMatchObject({
      id: "tool-1", name: "bash", status: "awaiting", approvalId: "appr-1",
      arguments: '{"command":"ls"}',
    });
    const root = document.createElement("div");
    root.innerHTML = turnMarkup(state);
    expect(root.textContent).toContain("Requer aprovação");
    expect(root.textContent).toContain("Permitir");
    expect(root.textContent).toContain("Recusar");
    expect(root.querySelector('[data-approval-id="appr-1"]')).not.toBeNull();
  });

  it("conclui uma chamada aprovada e preserva o resultado", () => {
    let state = reduceTurn(initialTurnState(), { protocol: 1, type: "tool_call",
      tool_call: { id: "tool-1", name: "bash", arguments: "{}" } });
    state = reduceTurn(state, { protocol: 1, type: "tool_approval_request", approval_id: "appr-1",
      tool_call: { id: "tool-1", name: "bash", arguments: "{}" } });
    state = reduceTurn(state, { protocol: 1, type: "tool_result",
      tool_result: { tool_call_id: "tool-1", content: '{"ok":true}', is_error: false } });
    expect(state.tools[0]).toMatchObject({ id: "tool-1", status: "done" });
    const root = document.createElement("div");
    root.innerHTML = turnMarkup(state);
    expect(root.textContent).toContain("Concluída");
    expect(root.textContent).not.toContain("Permitir");
  });

  it("rotula como recusada uma chamada negada", () => {
    let state = reduceTurn(initialTurnState(), { protocol: 1, type: "tool_call",
      tool_call: { id: "tool-1", name: "patch", arguments: "{}" } });
    state = reduceTurn(state, { protocol: 1, type: "tool_approval_request", approval_id: "appr-1",
      tool_call: { id: "tool-1", name: "patch", arguments: "{}" } });
    state = { ...state, tools: state.tools.map((tool: any) =>
      tool.id === "tool-1" && tool.status === "awaiting"
        ? { ...tool, status: "running", decision: "deny" } : tool) };
    state = reduceTurn(state, { protocol: 1, type: "tool_result", tool_result: {
      tool_call_id: "tool-1", is_error: true,
      content: '{"status":"denied","error":"Execução recusada pelo usuário.","results":[]}',
    } });
    const root = document.createElement("div");
    root.innerHTML = turnMarkup(state);
    expect(root.textContent).toContain("Recusada");
    expect(root.textContent).toContain("Execução recusada pelo usuário.");
  });

  it("envia a flag tools apenas para modelos com capacidade e permite decidir na rota", async () => {
    backend();
    const root = await mount();
    expect(root.textContent).toContain("Ferramentas");
    submit(root);
    expect(Socket.current.sent[0]).toMatchObject({ tools: true });

    Socket.current.event({ type: "tool_call", session_id: "saved",
      tool_call: { id: "tool-1", name: "write_file", arguments: '{"path":"/tmp/x"}' } });
    Socket.current.event({ type: "tool_approval_request", session_id: "saved", approval_id: "appr-1",
      tool_call: { id: "tool-1", name: "write_file", arguments: '{"path":"/tmp/x"}' } });

    await vi.waitFor(() => {
      expect(root.querySelector('[data-approval-id="appr-1"]')).not.toBeNull();
    });
    const allow = root.querySelector<HTMLButtonElement>('[data-approval="allow"]');
    expect(allow).not.toBeNull();
    allow!.click();
    await vi.waitFor(() => {
      expect(requests).toContainEqual({ path: "/api/chat/sessions/saved/tool-approvals/appr-1",
        method: "POST", body: { decision: "allow" } });
    });
    await vi.waitFor(() => {
      expect(root.textContent).toContain("Aguardando execução…");
    });
    Socket.current.event({ type: "tool_result", session_id: "saved",
      tool_result: { tool_call_id: "tool-1", content: '{"ok":true}', is_error: false } });
    await vi.waitFor(() => expect(root.textContent).toContain("Concluída"));
  });

  it("recusa envia deny e não executa", async () => {
    backend();
    const root = await mount();
    submit(root);
    Socket.current.event({ type: "tool_call", session_id: "saved",
      tool_call: { id: "tool-1", name: "bash", arguments: "{}" } });
    Socket.current.event({ type: "tool_approval_request", session_id: "saved", approval_id: "appr-1",
      tool_call: { id: "tool-1", name: "bash", arguments: "{}" } });
    await vi.waitFor(() => {
      expect(root.querySelector('[data-approval="deny"]')).not.toBeNull();
    });
    root.querySelector<HTMLButtonElement>('[data-approval="deny"]')!.click();
    await vi.waitFor(() => {
      expect(requests).toContainEqual({ path: "/api/chat/sessions/saved/tool-approvals/appr-1",
        method: "POST", body: { decision: "deny" } });
    });
  });
});