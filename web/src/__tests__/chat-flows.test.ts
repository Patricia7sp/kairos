// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { chatView } from "../../../kairos_web/ui/js/views/chat.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { modelosView } from "../../../kairos_web/ui/js/views/modelos.js";

type JsonRecord = Record<string, any>;

const credentialedProviders = [
  {
    id: "openai", provider: "openai", name: "OpenAI", auth_methods: ["api_key"],
    requires_credential: true, configured: true, credential_state: "configured",
  },
  {
    id: "openrouter", provider: "openrouter", name: "OpenRouter", auth_methods: ["api_key"],
    requires_credential: true, configured: true, credential_state: "configured",
  },
];

const models = [
  {
    id: "gpt-test", provider: "openai", name: "GPT Test", kind: "chat",
    stability: "stable", is_free: false,
    pricing: { prompt: "0.1", completion: "0.2", request: null },
    capabilities: { chat: true, tools: true, vision: false, streaming: true,
      context_length: 128000, max_output_tokens: 4096 },
    origins: ["curated"], supported_parameters: ["temperature"],
    input_modalities: ["text"], output_modalities: ["text"], expiration_date: null,
  },
  {
    id: "vendor/model:free", provider: "openrouter", name: "Vendor Free", kind: "chat",
    stability: "stable", is_free: true,
    pricing: { prompt: "0", completion: "0", request: "0" },
    capabilities: { chat: true, tools: false, vision: false, streaming: true,
      context_length: 64000, max_output_tokens: 2048 },
    origins: ["dynamic"], supported_parameters: ["temperature"],
    input_modalities: ["text"], output_modalities: ["text"], expiration_date: null,
  },
  {
    id: "llama-test", provider: "ollama", name: "Llama Test", kind: "chat",
    stability: "stable", is_free: true,
    pricing: { prompt: "0", completion: "0", request: "0" },
    capabilities: { chat: true, tools: false, vision: false, streaming: true,
      context_length: 8192, max_output_tokens: 1024 },
    origins: ["dynamic"], supported_parameters: ["temperature"],
    input_modalities: ["text"], output_modalities: ["text"], expiration_date: null,
  },
];

const oldSession = {
  id: "existing-session", source: "web", title: "Conversa antiga", model: "gpt-test",
  started_at: 1, ended_at: null, end_reason: null, status: "aberta", archived: false,
  pinned: false, hidden: false, message_count: 1, tool_call_count: 0,
  input_tokens: 0, output_tokens: 0, execution_kind: "model",
};

const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  closeCalls: Array<[number?, string?]> = [];
  listeners = new Map<string, Array<(event: any) => void>>();

  constructor(readonly url: string) { FakeSocket.instances.push(this); }
  addEventListener(name: string, listener: (event: any) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) || []), listener]);
  }
  emit(name: string, event: any = {}) {
    if (name === "open") this.readyState = 1;
    if (name === "close") this.readyState = 3;
    for (const listener of this.listeners.get(name) || []) listener(event);
  }
  send(value: string) { this.sent.push(value); }
  close(code?: number, reason?: string) {
    this.closeCalls.push([code, reason]);
    this.readyState = 3;
    this.emit("close", { code: code ?? 1000 });
  }
}

type BackendOptions = {
  providers?: JsonRecord[];
  sessions?: JsonRecord[];
  details?: Record<string, JsonRecord>;
  messages?: Record<string, JsonRecord[]>;
  probeResults?: Array<JsonRecord | Promise<JsonRecord>>;
  overrides?: (path: string, method: string, init?: RequestInit) => Response | Promise<Response> | undefined;
};

function installBackend(options: BackendOptions = {}) {
  const requests: Array<{ path: string; method: string; body: any }> = [];
  const probeResults = [...(options.probeResults || [])];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ path, method, body });
    const overridden = options.overrides?.(path, method, init);
    if (overridden) return await overridden;
    if (path === "/api/providers") return jsonResponse({ providers: options.providers ?? credentialedProviders });
    if (path.startsWith("/api/models?" ) || path === "/api/models") return jsonResponse({
      default_provider: "openai", default_model: "gpt-test", models,
    });
    if (path.startsWith("/api/sessions?")) return jsonResponse({
      sessions: options.sessions || [], total: (options.sessions || []).length,
      offset: 0, limit: 50, has_more: false,
    });
    if (path === "/api/auth/ws-ticket") return jsonResponse({ ticket: "ws-ticket", expires_in: 30 });
    const detail = path.match(/^\/api\/sessions\/([^/]+)$/);
    if (detail) {
      const id = decodeURIComponent(detail[1]!);
      return jsonResponse(options.details?.[id] || { error: "session_not_found" },
        options.details?.[id] ? 200 : 404);
    }
    const transcript = path.match(/^\/api\/sessions\/([^/]+)\/messages$/);
    if (transcript) {
      const id = decodeURIComponent(transcript[1]!);
      return jsonResponse({ session_id: id, execution_kind: "model", messages: options.messages?.[id] || [] });
    }
    if (/^\/api\/providers\/[^/]+\/test$/.test(path)) {
      return jsonResponse(await (probeResults.shift() || { connected: false, state: "unavailable" }));
    }
    if (path === "/api/models/selection" && method === "POST") {
      return jsonResponse({ status: "updated", selection: body });
    }
    throw new Error(`requisição inesperada: ${method} ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { requests, fetchMock };
}

const submit = (root: HTMLElement, content: string) => {
  const form = root.querySelector<HTMLFormElement>("[data-chat-form]")!;
  const textarea = form.elements.namedItem("content") as HTMLTextAreaElement;
  textarea.value = content;
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
};

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

describe("fluxos reais do Chat", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    location.hash = "#/chat";
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true, value() { this.open = true; },
    });
    Object.defineProperty(HTMLDialogElement.prototype, "close", {
      configurable: true, value() { this.open = false; },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("cria a primeira conversa com UUID v4 quando randomUUID não existe", async () => {
    installBackend();
    vi.stubGlobal("crypto", {
      getRandomValues(bytes: Uint8Array) { bytes.fill(0); return bytes; },
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    submit(root, "Olá");

    expect(JSON.parse(socket.sent[0]!)).toEqual({
      type: "message", protocol: 1,
      session_id: "00000000-0000-4000-8000-000000000000",
      content: "Olá", provider: "openai", model: "gpt-test",
    });
    cleanup();
  });

  it("honra nova conversa e seleção da rota mesmo com histórico existente", async () => {
    location.hash = "#/chat?provider=openrouter&model=vendor%2Fmodel%3Afree&new=1";
    installBackend({
      sessions: [oldSession],
      details: { "existing-session": { ...oldSession, selection: {
        provider: "openai", model: "gpt-test", parameters: {}, reason: "global_default",
      } } },
      messages: { "existing-session": [{ id: "m-old", role: "user", content: "old message", created_at: 1 }] },
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const draftHash = location.hash;
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    submit(root, "nova mensagem");

    const sent = JSON.parse(socket.sent[0]!);
    expect(sent.session_id).not.toBe("existing-session");
    expect(sent.provider).toBe("openrouter");
    expect(sent.model).toBe("vendor/model:free");
    expect(root.querySelector("[data-chat-messages]")!.textContent).not.toContain("old message");
    expect(draftHash).toContain("new=1");
    expect(draftHash).toContain(`session=${encodeURIComponent(sent.session_id)}`);
    expect(location.hash).toBe(draftHash);
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_start", session_id: sent.session_id,
      provider: "openrouter", model: "vendor/model:free", selection_reason: "message_override",
    }) });
    expect(location.hash).toBe(`#/chat?session=${encodeURIComponent(sent.session_id)}`);
    cleanup();
  });

  it("preserva o rascunho quando a admissão falha antes de turn_start", async () => {
    location.hash = "#/chat?provider=openrouter&model=vendor%2Fmodel%3Afree&new=1";
    installBackend();
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const draftHash = location.hash;
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    submit(root, "mensagem que precisa ser recuperada");
    const sessionId = JSON.parse(socket.sent[0]!).session_id;
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_error", session_id: sessionId,
      error: "não foi possível admitir o turno", error_kind: "persistence", retryable: true,
    }) });

    expect(location.hash).toBe(draftHash);
    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.value)
      .toBe("mensagem que precisa ser recuperada");
    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(false);
    cleanup();
  });

  it("mantém a mensagem do rascunho disponível se o socket desconecta antes de turn_start", async () => {
    installBackend();
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const draftHash = location.hash;
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    submit(root, "mensagem preservada na desconexão");
    socket.emit("close", { code: 1006 });

    expect(location.hash).toBe(draftHash);
    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.value)
      .toBe("mensagem preservada na desconexão");
    cleanup();
  });

  it("restaura a seleção persistida da conversa existente", async () => {
    location.hash = "#/chat?session=existing-session";
    installBackend({
      sessions: [oldSession],
      details: { "existing-session": { ...oldSession, selection: {
        provider: "openrouter", model: "vendor/model:free",
        parameters: { temperature: 0.25 }, reason: "conversation_override",
      } } },
      messages: { "existing-session": [{ id: "m-old", role: "assistant", content: "histórico", created_at: 1 }] },
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    submit(root, "continue");

    expect(JSON.parse(socket.sent[0]!)).toEqual({
      type: "message", protocol: 1, session_id: "existing-session", content: "continue",
      provider: "openrouter", model: "vendor/model:free", parameters: { temperature: 0.25 },
    });
    expect(root.textContent).toContain("histórico");
    cleanup();
  });

  it("aplica uma troca à conversa atual sem alterar o padrão global", async () => {
    location.hash = "#/modelos?session=existing-session";
    const backend = installBackend({ details: { "existing-session": { ...oldSession,
      selection: { provider: "openai", model: "gpt-test", parameters: {} },
    } } });
    const root = document.createElement("main");
    await modelosView(root, {}, { signal: new AbortController().signal });
    root.querySelector<HTMLButtonElement>(
      '[data-choose-model="openrouter/vendor/model:free"]',
    )!.click();

    await vi.waitFor(() => expect(root.querySelector<HTMLButtonElement>("[data-apply-model]")!.disabled).toBe(false));

    root.querySelector<HTMLButtonElement>("[data-apply-model]")!.click();
    await flush();

    const writes = backend.requests.filter((request) => request.path === "/api/models/selection");
    expect(writes).toEqual([{ path: "/api/models/selection", method: "POST", body: {
      provider: "openrouter", model: "vendor/model:free", scope: "conversation",
      session_id: "existing-session",
      profile: "",
      parameters: { routing: { data_collection: "deny", require_parameters: true, allow_fallbacks: true } },
    } }]);
    expect(writes[0]!.body.scope).not.toBe("global");
  });

  it("mantém a seleção de um rascunho na rota sem fingir que a sessão existe", async () => {
    location.hash = "#/modelos?session=draft-1&provider=openai&model=gpt-test&new=1";
    const backend = installBackend();
    const root = document.createElement("main");
    await modelosView(root, {}, { signal: new AbortController().signal });
    root.querySelector<HTMLButtonElement>(
      '[data-choose-model="openrouter/vendor/model:free"]',
    )!.click();

    root.querySelector<HTMLButtonElement>("[data-apply-model]")!.click();
    await flush();

    expect(backend.requests.some((request) => request.path === "/api/models/selection")).toBe(false);
    expect(location.hash).toBe(
      "#/chat?provider=openrouter&model=vendor%2Fmodel%3Afree&new=1&session=draft-1",
    );
  });

  it("bloqueia o provider selecionado indisponível mesmo se outro estiver configurado", async () => {
    location.hash = "#/chat?provider=openai&model=gpt-test&new=1";
    installBackend({ providers: [
      { ...credentialedProviders[0], configured: false, credential_state: "missing" },
      { id: "ollama", provider: "ollama", name: "Ollama", auth_methods: [],
        requires_credential: false, configured: true, credential_state: "not_required" },
    ] });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    FakeSocket.instances[0]!.emit("open");

    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(true);
    expect(root.querySelector("[data-chat-readiness]")!.textContent).toContain("openai");
    cleanup();
  });

  it("permite repetir a descoberta do Ollama após falha", async () => {
    location.hash = "#/chat?provider=ollama&model=llama-test&new=1";
    const backend = installBackend({
      providers: [{ id: "ollama", provider: "ollama", name: "Ollama", auth_methods: [],
        requires_credential: false, configured: true, credential_state: "not_required" }],
      probeResults: [
        { provider: "ollama", connected: false, state: "unavailable", message: "Ollama offline." },
        { provider: "ollama", connected: true, state: "available", message: "Ollama conectado." },
      ],
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    FakeSocket.instances[0]!.emit("open");

    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(true);
    expect(root.querySelector("[data-chat-readiness]")!.textContent).toContain("Ollama offline");
    root.querySelector<HTMLButtonElement>("[data-chat-retry]")!.click();
    await vi.waitFor(() => {
      expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(false);
    });

    expect(root.querySelector("[data-chat-readiness]")!.textContent).toContain("Ollama conectado");
    expect(backend.requests.filter((request) => request.path === "/api/providers/ollama/test"))
      .toHaveLength(2);
    cleanup();
  });

  it("mantém o compositor bloqueado enquanto outra conversa carrega e ignora a descoberta anterior", async () => {
    location.hash = "#/chat?provider=openai&model=gpt-test&new=1";
    const ollamaSession = { ...oldSession, id: "ollama-session", title: "Ollama" };
    const routerSession = { ...oldSession, id: "router-session", title: "OpenRouter" };
    const ollamaProbe = deferred<JsonRecord>();
    const routerHistory = deferred<Response>();
    installBackend({
      providers: [
        ...credentialedProviders,
        { id: "ollama", provider: "ollama", name: "Ollama", auth_methods: [],
          requires_credential: false, configured: true, credential_state: "not_required" },
      ],
      sessions: [ollamaSession, routerSession],
      details: {
        "ollama-session": { ...ollamaSession, selection: { provider: "ollama", model: "llama-test",
          parameters: {}, reason: "conversation_override" } },
        "router-session": { ...routerSession, selection: { provider: "openrouter",
          model: "vendor/model:free", parameters: {}, reason: "conversation_override" } },
      },
      probeResults: [ollamaProbe.promise],
      overrides: (path) => path === "/api/sessions/router-session/messages"
        ? routerHistory.promise : undefined,
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    root.querySelector<HTMLButtonElement>('[data-session-id="ollama-session"]')!.click();
    await vi.waitFor(() => {
      expect(root.querySelector("[data-chat-readiness]")!.textContent).toContain("Testando conexão");
    });
    root.querySelector<HTMLButtonElement>('[data-session-id="router-session"]')!.click();
    ollamaProbe.resolve({ provider: "ollama", connected: true, state: "available" });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(true);
    submit(root, "não enviar para Ollama");
    expect(socket.sent).toHaveLength(0);

    routerHistory.resolve(jsonResponse({
      session_id: "router-session", execution_kind: "model",
      messages: [{ id: "router-history", role: "assistant", content: "histórico B", created_at: 2 }],
    }));
    await vi.waitFor(() => {
      expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(false);
    });
    submit(root, "mensagem B");

    expect(JSON.parse(socket.sent[0]!)).toMatchObject({
      session_id: "router-session", provider: "openrouter", model: "vendor/model:free",
    });
    expect(root.querySelector("[data-chat-messages]")!.textContent).toContain("histórico B");
    cleanup();
  });

  it("recupera a conversa atual quando o carregamento de outra sessão falha", async () => {
    location.hash = "#/chat?provider=openai&model=gpt-test&new=1";
    const brokenSession = { ...oldSession, id: "broken-session", title: "Quebrada" };
    installBackend({
      sessions: [brokenSession],
      details: { "broken-session": { ...brokenSession, selection: {
        provider: "openrouter", model: "vendor/model:free",
        parameters: {}, reason: "conversation_override",
      } } },
      overrides: (path) => path === "/api/sessions/broken-session/messages"
        ? jsonResponse({ error: "falha ao carregar histórico" }, 500) : undefined,
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const originalHash = location.hash;
    FakeSocket.instances[0]!.emit("open");

    root.querySelector<HTMLButtonElement>('[data-session-id="broken-session"]')!.click();

    await vi.waitFor(() => {
      expect(root.querySelector("[data-chat-status]")!.textContent).toContain("falha ao carregar histórico");
      expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(false);
    });
    expect(location.hash).toBe(originalHash);
    cleanup();
  });

  it("bloqueia envio após falha inicial e recarrega a seleção persistida no retry", async () => {
    location.hash = "#/chat?session=existing-session";
    let detailAttempts = 0;
    installBackend({
      sessions: [oldSession],
      details: { "existing-session": { ...oldSession, selection: {
        provider: "openrouter", model: "vendor/model:free",
        parameters: { temperature: 0.3 }, reason: "conversation_override",
      } } },
      messages: { "existing-session": [
        { id: "persisted", role: "assistant", content: "histórico recuperado", created_at: 1 },
      ] },
      overrides: (path) => {
        if (path === "/api/sessions/existing-session" && detailAttempts++ === 0) {
          return jsonResponse({ error: "falha ao carregar seleção persistida" }, 500);
        }
        return undefined;
      },
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    expect(root.querySelector("[data-chat-status]")!.textContent)
      .toContain("falha ao carregar seleção persistida");
    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(true);
    submit(root, "não usar o padrão global");
    expect(socket.sent).toHaveLength(0);

    root.querySelector<HTMLButtonElement>("[data-chat-retry]")!.click();
    await vi.waitFor(() => {
      expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(false);
      expect(root.querySelector("[data-chat-messages]")!.textContent).toContain("histórico recuperado");
    });
    submit(root, "usar o override persistido");

    expect(JSON.parse(socket.sent[0]!)).toEqual({
      type: "message", protocol: 1, session_id: "existing-session",
      content: "usar o override persistido", provider: "openrouter",
      model: "vendor/model:free", parameters: { temperature: 0.3 },
    });
    cleanup();
  });

  it("atualiza preço e capacidades junto com a seleção da conversa", async () => {
    location.hash = "#/chat?provider=openai&model=gpt-test&new=1";
    const routerSession = { ...oldSession, id: "router-session", title: "OpenRouter" };
    installBackend({
      sessions: [routerSession],
      details: { "router-session": { ...routerSession, selection: {
        provider: "openrouter", model: "vendor/model:free",
        parameters: {}, reason: "conversation_override",
      } } },
      messages: { "router-session": [] },
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    FakeSocket.instances[0]!.emit("open");

    root.querySelector<HTMLButtonElement>('[data-session-id="router-session"]')!.click();
    await vi.waitFor(() => {
      expect(location.hash).toBe("#/chat?session=router-session");
    });

    const context = root.querySelector(".k-chat__context")!;
    expect(context.textContent).toContain("Vendor Free");
    expect(context.textContent).toContain("Gratuito");
    expect(context.textContent).toContain("Chat");
    expect(context.textContent).not.toContain("Conforme catálogo");
    expect(context.textContent).not.toContain("Ferramentas");
    cleanup();
  });

  it("não sobrescreve outra rota se as APIs iniciais terminarem após abort", async () => {
    const providers = deferred<Response>();
    installBackend({ overrides: (path) => path === "/api/providers" ? providers.promise : undefined });
    const controller = new AbortController();
    const root = document.createElement("main");
    const mounting = chatView(root, {}, { signal: controller.signal });
    await flush();
    controller.abort();
    root.innerHTML = "<p>outra rota</p>";
    providers.resolve(jsonResponse({ providers: credentialedProviders }));

    const cleanup = await mounting;

    expect(root.textContent).toBe("outra rota");
    expect(FakeSocket.instances).toHaveLength(0);
    cleanup();
  });

  it("descarta histórico atrasado depois da troca de rota", async () => {
    location.hash = "#/chat?session=existing-session";
    const history = deferred<Response>();
    installBackend({
      sessions: [oldSession],
      details: { "existing-session": { ...oldSession, selection: {
        provider: "openai", model: "gpt-test", parameters: {}, reason: "global_default",
      } } },
      overrides: (path) => path.endsWith("/messages") ? history.promise : undefined,
    });
    const controller = new AbortController();
    const root = document.createElement("main");
    const mounting = chatView(root, {}, { signal: controller.signal });
    await flush();
    controller.abort();
    root.innerHTML = "<p>outra rota</p>";
    history.resolve(jsonResponse({ session_id: "existing-session", execution_kind: "model",
      messages: [{ id: "late", role: "user", content: "atrasada", created_at: 1 }] }));

    const cleanup = await mounting;

    expect(root.textContent).toBe("outra rota");
    expect(FakeSocket.instances).toHaveLength(0);
    cleanup();
  });

  it("descarta descoberta atrasada depois da troca de rota", async () => {
    location.hash = "#/chat?provider=ollama&model=llama-test&new=1";
    const probe = deferred<JsonRecord>();
    installBackend({
      providers: [{ id: "ollama", provider: "ollama", name: "Ollama", auth_methods: [],
        requires_credential: false, configured: true, credential_state: "not_required" }],
      probeResults: [probe.promise],
    });
    const controller = new AbortController();
    const root = document.createElement("main");
    const mounting = chatView(root, {}, { signal: controller.signal });
    await flush();
    controller.abort();
    root.innerHTML = "<p>outra rota</p>";
    probe.resolve({ provider: "ollama", connected: true, state: "available" });

    const cleanup = await mounting;

    expect(root.textContent).toBe("outra rota");
    expect(FakeSocket.instances).toHaveLength(0);
    cleanup();
  });

  it("fecha o socket, cancela o frame e ignora eventos tardios no cleanup", async () => {
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.set(++frameId, callback);
      return frameId;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
    installBackend();
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");
    submit(root, "pergunta");
    socket.emit("message", { data: JSON.stringify({ protocol: 1, type: "delta", text: "parcial" }) });

    cleanup();
    socket.emit("message", { data: JSON.stringify({ protocol: 1, type: "delta", text: "tardia" }) });
    for (const callback of frames.values()) callback(0);

    expect(socket.closeCalls).toEqual([[1000, "view closed"]]);
    expect(frames.size).toBe(0);
    expect(root.textContent).not.toContain("parcial");
    expect(root.textContent).not.toContain("tardia");
  });

  it("não renderiza a resposta concluída na conversa criada em seguida", async () => {
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.set(++frameId, callback);
      return frameId;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
    installBackend();
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");
    submit(root, "pergunta antiga");
    const firstSession = JSON.parse(socket.sent[0]!).session_id;
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_start", session_id: firstSession,
      provider: "openai", model: "gpt-test", selection_reason: "message_override",
    }) });
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "delta", session_id: firstSession, text: "resposta antiga",
    }) });
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_end", session_id: firstSession, finish_reason: "stop",
    }) });

    root.querySelector<HTMLButtonElement>("[data-new-chat]")!.click();
    for (const callback of [...frames.values()]) callback(0);

    expect(root.querySelector("[data-chat-messages]")!.textContent).not.toContain("resposta antiga");
    cleanup();
  });

  it("ignora eventos tardios da sessão anterior depois de criar outra conversa", async () => {
    installBackend();
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");
    submit(root, "pergunta antiga");
    const firstSession = JSON.parse(socket.sent[0]!).session_id;
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_start", session_id: firstSession,
      provider: "openai", model: "gpt-test", selection_reason: "message_override",
    }) });
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_end", session_id: firstSession, finish_reason: "stop",
    }) });
    root.querySelector<HTMLButtonElement>("[data-new-chat]")!.click();

    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "delta", session_id: firstSession, text: "evento atrasado",
    }) });

    expect(root.querySelector("[data-chat-messages]")!.textContent).not.toContain("evento atrasado");
    cleanup();
  });

  it("materializa a resposta concluída antes de aceitar o próximo envio", async () => {
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.set(++frameId, callback);
      return frameId;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
    installBackend();
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");
    submit(root, "primeira pergunta");
    const firstSession = JSON.parse(socket.sent[0]!).session_id;
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_start", session_id: firstSession,
      provider: "openai", model: "gpt-test", selection_reason: "message_override",
    }) });
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "delta", session_id: firstSession, text: "primeira resposta",
    }) });
    socket.emit("message", { data: JSON.stringify({
      protocol: 1, type: "turn_end", session_id: firstSession, finish_reason: "stop",
    }) });

    submit(root, "segunda pergunta");

    expect(socket.sent).toHaveLength(2);
    expect(root.querySelector("[data-chat-messages]")!.textContent).toContain("primeira resposta");
    cleanup();
  });

  it("impede envio duplicado e troca de sessão durante um turno aceito", async () => {
    location.hash = "#/chat?session=existing-session";
    const second = { ...oldSession, id: "second-session", title: "Segunda" };
    const backend = installBackend({
      sessions: [oldSession, second],
      details: {
        "existing-session": { ...oldSession, selection: { provider: "openai", model: "gpt-test",
          parameters: {}, reason: "global_default" } },
        "second-session": { ...second, selection: { provider: "openrouter", model: "vendor/model:free",
          parameters: {}, reason: "conversation_override" } },
      },
      messages: { "existing-session": [], "second-session": [
        { id: "second-message", role: "user", content: "não carregar", created_at: 2 },
      ] },
    });
    const root = document.createElement("main");
    const cleanup = await chatView(root, {}, { signal: new AbortController().signal });
    const socket = FakeSocket.instances[0]!;
    socket.emit("open");

    submit(root, "primeira");
    submit(root, "duplicada");
    root.querySelector<HTMLButtonElement>('[data-session-id="second-session"]')!.click();
    root.querySelector<HTMLButtonElement>("[data-new-chat]")!.click();

    expect(socket.sent).toHaveLength(1);
    expect(location.hash).toBe("#/chat?session=existing-session");
    expect(root.textContent).not.toContain("não carregar");
    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(true);
    expect(backend.requests.some((request) => request.path.includes("second-session/messages")))
      .toBe(false);

    socket.emit("message", { data: JSON.stringify({ protocol: 1, type: "turn_end", finish_reason: "stop" }) });
    expect(root.querySelector<HTMLTextAreaElement>("textarea")!.disabled).toBe(false);
    cleanup();
  });
});
