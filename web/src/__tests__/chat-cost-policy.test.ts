// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error The shipped SPA uses native JavaScript modules.
import { chatView, initialTurnState, reduceTurn, turnMarkup } from "../../../kairos_web/ui/js/views/chat.js";

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

describe("Chat cost accounting", () => {
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
