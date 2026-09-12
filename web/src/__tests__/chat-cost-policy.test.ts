// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error The shipped SPA uses native JavaScript modules.
import { chatView, initialTurnState, reduceTurn, turnMarkup } from "../../../kairos_web/ui/js/views/chat.js";
// @ts-expect-error The shipped SPA uses native JavaScript modules.
import { ChatClient } from "../../../kairos_web/ui/js/chat-client.js";

type RecordValue = Record<string, any>;
const routing = { data_collection: "allow", require_parameters: false, allow_fallbacks: false };
const selection = { provider: "openrouter", model: "vendor/model", parameters: { routing } };
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
function backend({ persisted = false, messages = [], currentSelection = selection,
  defaults = {}, profiles = [], defaultModel = "vendor/model" }: { persisted?: boolean; messages?: RecordValue[];
    currentSelection?: RecordValue; defaults?: RecordValue; profiles?: RecordValue[]; defaultModel?: string } = {}) {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const path = String(input);
    let body: RecordValue;
    if (path === "/api/providers") body = { providers: [
      { id: "openrouter", configured: true, requires_credential: true },
    ] };
    else if (path === "/api/models") body = {
      default_provider: "openrouter", default_model: defaultModel, default_parameters: defaults,
      models: [{ id: "vendor/model", provider: "openrouter", name: "Vendor", is_free: true }], profiles,
    };
    else if (path.startsWith("/api/sessions?")) body = { sessions: persisted ? [{ id: "saved" }] : [] };
    else if (path === "/api/sessions/saved") body = { id: "saved", selection: currentSelection };
    else if (path === "/api/sessions/saved/messages") body = { messages };
    else if (path === "/api/auth/ws-ticket") body = { ticket: "test-ticket" };
    else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
}
let cleanup: (() => void) | undefined;
const mount = async () => {
  const root = document.createElement("main");
  document.body.append(root);
  cleanup = await chatView(root, {});
  Socket.current.emit("open");
  return root;
};
const submit = (root: HTMLElement) => {
  root.querySelector<HTMLTextAreaElement>("textarea")!.value = "Pergunta";
  root.querySelector("form")!.dispatchEvent(new Event("submit", { cancelable: true }));
};
beforeEach(() => {
  sessionStorage.clear();
  document.body.innerHTML = "";
  location.hash = "#/chat?new=1&session=draft&provider=openrouter&model=vendor%2Fmodel";
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
});
afterEach(() => { cleanup?.(); cleanup = undefined; vi.unstubAllGlobals(); });

describe("Chat optional web search", () => {
  it("sends an explicit boolean outside provider parameters, defaulting to off", () => {
    const client = new ChatClient();
    client.connect({ token: "test", WebSocketImpl: Socket });
    Socket.current.emit("open");
    client.sendMessage({ sessionId: "saved", content: "Olá" });
    client.sendMessage({ sessionId: "saved", content: "Pesquise", webSearch: true });
    expect(Socket.current.sent[0]).toMatchObject({ web_search: false });
    expect(Socket.current.sent[1]).toMatchObject({ web_search: true });
    expect(Socket.current.sent[1]).not.toHaveProperty("parameters.web_search");
    client.close();
  });

  it("keeps the search choice for each conversation and disables it while busy", async () => {
    location.hash = "#/chat?session=saved";
    backend({ persisted: true, currentSelection: { ...selection, parameters: {} } });
    const root = await mount();
    const checkbox = root.querySelector<HTMLInputElement>('[name="web_search"]');
    expect(checkbox).not.toBeNull();
    expect(checkbox!.checked).toBe(false);
    expect(root.textContent).toContain("buscador externo");
    expect(root.textContent).toContain("neste navegador");
    expect(root.querySelector("[data-context-routing]")!.textContent).toContain("treinamento");
    checkbox!.click();
    submit(root);
    expect(Socket.current.sent[0]?.web_search).toBe(true);
    expect(checkbox!.disabled).toBe(true);
    Socket.current.event({ type: "turn_end", session_id: "saved" });
    expect(checkbox!.disabled).toBe(false);
    root.querySelector<HTMLButtonElement>("[data-new-chat]")!.click();
    expect(checkbox!.checked).toBe(false);
    root.querySelector<HTMLButtonElement>('[data-session-id="saved"]')!.click();
    await vi.waitFor(() => expect(location.hash).toBe("#/chat?session=saved"));
    expect(checkbox!.checked).toBe(true);
    cleanup?.();
    const reopened = await mount();
    expect(reopened.querySelector<HTMLInputElement>('[name="web_search"]')!.checked).toBe(true);
  });

  it.each([false, true])("escapes live results and localizes their status, is_error=%s", (isError) => {
    const content = JSON.stringify({ query: "informação", status: "ok", untrusted: true,
      results: [{ title: '<img src=x onerror="alert(1)">',
        url: "javascript:alert(1)", snippet: "Resultado & detalhe" },
      { title: "Fonte segura", url: "https://example.org/source", snippet: "Informação pública" }] });
    let state = reduceTurn(initialTurnState(), { protocol: 1, type: "tool_call",
      tool_call: { id: "search-1", name: "web_search", arguments: '{}' } });
    const root = document.createElement("div");
    root.innerHTML = turnMarkup(state);
    expect(root.textContent).toContain("Buscando");
    state = reduceTurn(state, { protocol: 1, type: "tool_result", tool_result: {
      tool_call_id: "search-1", name: "web_search", content, is_error: isError,
    } });
    root.innerHTML = turnMarkup(state);
    expect(root.textContent).toContain(isError ? "Falha" : "Concluída");
    expect(root.textContent).toContain("Resultado & detalhe");
    expect(root.querySelector("img, script, a[href^='javascript:']")).toBeNull();
    expect(root.querySelector<HTMLAnchorElement>('a')?.href).toBe("https://example.org/source");
    expect(root.querySelector('a')?.rel).toContain("noreferrer");
  });

  it("renders persisted tool results as escaped search output with their error status", async () => {
    location.hash = "#/chat?session=saved";
    backend({ persisted: true, messages: [{ role: "tool", tool_name: "web_search",
      content: '<script>alert(1)</script> busca indisponível', is_error: true }] });
    const root = await mount();
    const tool = root.querySelector(".k-chat-message--tool")!;
    expect(tool.textContent).toContain("Busca web");
    expect(tool.textContent).toContain("Falha");
    expect(tool.textContent).toContain("busca indisponível");
    expect(tool.querySelector("script")).toBeNull();
  });

  it("retains completed search results when another round reuses a tool call ID", () => {
    let state = initialTurnState();
    for (const content of ["Primeira fonte preservada", "Segunda fonte distinta"]) {
      state = reduceTurn(state, { protocol: 1, type: "tool_call", tool_call: {
        id: "generated-search-0", name: "web_search", arguments: '{}',
      } });
      state = reduceTurn(state, { protocol: 1, type: "tool_result", tool_result: {
        tool_call_id: "generated-search-0", content, is_error: false,
      } });
    }
    const root = document.createElement("div");
    root.innerHTML = turnMarkup(state);
    const results = root.querySelectorAll(".k-chat-tool");
    expect(results).toHaveLength(2);
    expect(results[0]!.textContent).toContain("Primeira fonte preservada");
    expect(results[0]!.textContent).not.toContain("Segunda fonte distinta");
    expect(results[1]!.textContent).toContain("Segunda fonte distinta");
  });

  it("shows search errors in readable text and handles empty results", () => {
    for (const [content, expected] of [
      ['{"status":"unavailable","error":"Busca indispon\\u00edvel","results":[]}', "Busca indisponível"],
      ['{"query":"teste","status":"ok","results":[],"untrusted":true}', "Nenhum resultado encontrado"],
    ]) {
      const root = document.createElement("div");
      root.innerHTML = turnMarkup({ ...initialTurnState(), tools: [{ name: "web_search", status: "done", content }] });
      expect(root.textContent).toContain(expected);
    }
  });
});

describe("Chat cost accounting", () => {
  it("identifies a persisted interrupted response without exposing provider error details", async () => {
    location.hash = "#/chat?session=saved";
    backend({ persisted: true, messages: [{ role: "assistant", content: "Resposta parcial",
      is_interrupted: true, error: '<script>private upstream details</script>' }] });
    const root = await mount();
    const response = root.querySelector(".k-chat-message--assistant")!;
    expect(response.textContent).toContain("Resposta interrompida.");
    expect(response.textContent).toContain("Resposta parcial");
    expect(response.textContent).not.toContain("private upstream details");
    expect(response.querySelector("script")).toBeNull();
  });

  it("restores total turn cost in context while retaining each round's own cost", async () => {
    location.hash = "#/chat?session=saved";
    backend({ persisted: true, messages: [
      { role: "assistant", content: "Vou pesquisar", cost: { estimated_usd: 0.01 },
        turn_cost: { estimated_usd: 0.01 } },
      { role: "tool", tool_name: "web_search", content: '{"results":[]}', is_error: false },
      { role: "assistant", content: "Resposta", cost: { estimated_usd: 0.02 },
        turn_cost: { estimated_usd: 0.03 } },
    ] });
    const root = await mount();
    expect(root.querySelector("[data-context-cost]")!.textContent).toContain("US$ 0,03");
    const responses = root.querySelectorAll(".k-chat-message--assistant");
    expect(responses[0]!.textContent).toContain("US$ 0,01");
    expect(responses[1]!.textContent).toContain("US$ 0,02");
  });

  it.each([
    [{ actual_usd: 0, estimated_usd: 0.9, status: "actual" }, "informado", "US$ 0,00"],
    [{ estimated_usd: 0.000007, status: "estimated" }, "estimado", "US$ 0,000007"],
    [null, "desconhecido", null],
    [{ actual_usd: NaN, estimated_usd: -1, status: "actual" }, "desconhecido", null],
    [{ actual_usd: Infinity, estimated_usd: false }, "desconhecido", null],
  ])("renders cost honestly from usage events: %j", (cost, kind, amount) => {
    const turn = reduceTurn(initialTurnState(), { protocol: 1, type: "usage", cost });
    const root = document.createElement("div");
    root.innerHTML = turnMarkup(turn);
    expect(root.textContent).toContain(`Custo ${kind}`);
    if (amount) expect(root.textContent).toContain(amount);
    else expect(root.textContent).not.toContain("US$");
    expect(root.textContent).not.toMatch(/NaN|Infinity|Gratuito/);
  });

  it("shows live cost in both the response and latest-turn context", async () => {
    backend();
    const root = await mount();
    submit(root);
    Socket.current.event({ type: "turn_start", session_id: "draft", provider: "openrouter", model: "vendor/model" });
    Socket.current.event({ type: "usage", session_id: "draft", cost: { actual_usd: 0.03, status: "actual" } });
    Socket.current.event({ type: "turn_end", session_id: "draft" });
    expect(root.querySelector("[data-chat-messages]")!.textContent).toContain("Custo informado: US$ 0,03");
    expect(root.querySelector("[data-context-cost]")?.textContent).toContain("Custo informado: US$ 0,03");
    root.querySelector<HTMLButtonElement>("[data-new-chat]")!.click();
    expect(root.querySelector("[data-context-cost]")?.textContent).toContain("Nenhum turno");
  });

  it("restores per-response cost and shows unknown for the latest legacy response", async () => {
    location.hash = "#/chat?session=saved";
    backend({ persisted: true, messages: [
      { role: "assistant", content: "Primeira", cost: { estimated_usd: 0.02, status: "estimated" } },
      { role: "user", content: "Seguinte", cost: { actual_usd: 90 } },
      { role: "assistant", content: "Legada" },
    ] });
    const root = await mount();
    const responses = root.querySelectorAll(".k-chat-message--assistant");
    expect(responses[0]!.textContent).toContain("Custo estimado: US$ 0,02");
    expect(responses[1]!.textContent).toContain("Custo desconhecido");
    expect(root.querySelector("[data-context-cost]")?.textContent).toContain("Custo desconhecido");
    expect(root.querySelector("[data-chat-messages]")!.textContent).not.toContain("90");
  });
});

describe("Chat next-turn routing and profiles", () => {
  it("deep-merges global, profile and draft routing even for a different default model", async () => {
    location.hash += "&profile=partial";
    sessionStorage.setItem("kairos.chat.draft.draft", JSON.stringify({ ...selection, profile: "partial",
      parameters: { routing: { allow_fallbacks: true } } }));
    backend({ defaultModel: "different-model", defaults: { temperature: 0.4, routing }, profiles: [
      { ...selection, name: "partial", parameters: { routing: { require_parameters: true } } },
    ] });
    const root = await mount();
    expect(root.querySelector("[data-context-routing]")?.textContent).toContain("Coleta de dados: permitida");
    expect(root.querySelector("[data-context-routing]")?.textContent).toContain("Parâmetros obrigatórios: sim");
    submit(root);
    expect(Socket.current.sent[0]?.parameters).toEqual({ temperature: 0.4, routing: {
      data_collection: "allow", require_parameters: true, allow_fallbacks: true,
    } });
  });

  it("deep-merges persisted partial routing after global and matching profile settings", async () => {
    location.hash = "#/chat?session=saved";
    backend({ persisted: true, defaultModel: "different-model", defaults: { temperature: 0.4, routing },
      profiles: [{ ...selection, name: "partial", parameters: { routing: { require_parameters: true } } }],
      currentSelection: { ...selection, profile: "partial", parameters: { routing: { allow_fallbacks: true } } },
    });
    const root = await mount();
    expect(root.querySelector("[data-context-routing]")?.textContent).toContain("Coleta de dados: permitida");
    submit(root);
    expect(Socket.current.sent[0]?.parameters).toEqual({ temperature: 0.4, routing: {
      data_collection: "allow", require_parameters: true, allow_fallbacks: true,
    } });
  });

  it("restores draft routing through navigation and sends it with the chosen profile", async () => {
    sessionStorage.setItem("kairos.chat.draft.draft", JSON.stringify({ ...selection, profile: "private" }));
    backend({ profiles: [{ name: "private", ...selection }] });
    const root = await mount();
    expect(root.querySelector("[data-context-routing]")?.textContent).toMatch(/Coleta de dados: permitida/);
    expect(root.querySelector("[data-context-routing]")?.textContent).toMatch(/Parâmetros obrigatórios: não/);
    expect(root.querySelector("[data-context-routing]")?.textContent).toMatch(/Fallbacks: não/);
    expect(root.querySelector("[data-context-profile]")?.textContent).toBe("private");
    expect(root.querySelector(".k-chat__context")!.textContent).toContain("próximo turno");
    submit(root);
    expect(Socket.current.sent[0]).toMatchObject({ ...selection, profile: "private" });
  });

  it("loads route profiles and keeps them when visiting the model catalog", async () => {
    location.hash += "&profile=private";
    backend({ profiles: [{ name: "private", ...selection }] });
    const root = await mount();
    expect(root.querySelector("[data-context-profile]")?.textContent).toBe("private");
    expect(root.querySelector<HTMLAnchorElement>("[data-change-model]")!.hash).toContain("profile=private");
    submit(root);
    expect(Socket.current.sent[0]).toMatchObject({ ...selection, profile: "private" });
  });

  it("uses persisted routing instead of a stale draft when reopening a conversation", async () => {
    location.hash = "#/chat?session=saved";
    sessionStorage.setItem("kairos.chat.draft.saved", JSON.stringify(selection));
    backend({ persisted: true, currentSelection: { ...selection, parameters: {}, profile: "saved-profile" } });
    const root = await mount();
    expect(root.querySelector("[data-context-routing]")?.textContent).toContain("Coleta de dados: negada");
    expect(root.querySelector("[data-context-routing]")?.textContent).toContain("Parâmetros obrigatórios: sim");
    expect(root.querySelector("[data-context-routing]")?.textContent).toContain("Fallbacks: sim");
    expect(root.querySelector("[data-context-profile]")?.textContent).toBe("saved-profile");
    submit(root);
    expect(Socket.current.sent[0]).not.toHaveProperty("parameters.routing.data_collection", "allow");
  });

  it("ignores a different-model draft and uses matching global defaults", async () => {
    sessionStorage.setItem("kairos.chat.draft.draft", JSON.stringify({ ...selection, model: "other" }));
    backend({ defaults: { routing: { data_collection: "deny", require_parameters: true, allow_fallbacks: false } } });
    const root = await mount();
    expect(root.querySelector("[data-context-routing]")?.textContent).toContain("Coleta de dados: negada");
    submit(root);
    expect(Socket.current.sent[0]?.parameters.routing).toEqual({
      data_collection: "deny", require_parameters: true, allow_fallbacks: false,
    });
  });
});
