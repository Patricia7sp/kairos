// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { modelosView } from "../../../kairos_web/ui/js/views/modelos.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { provedoresView } from "../../../kairos_web/ui/js/views/provedores.js";

type JsonRecord = Record<string, any>;

const jsonResponse = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { "Content-Type": "application/json" },
});

const flush = async () => {
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
};

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};

const model = (
  id: string,
  contextLength: number,
  reasoning: boolean | null,
  stability = "stable",
): JsonRecord => ({
  id,
  name: id,
  provider: "openrouter",
  kind: "model",
  stability,
  is_free: true,
  pricing: { prompt: "0", completion: "0", request: "0" },
  capabilities: {
    chat: true,
    tools: false,
    vision: false,
    reasoning,
    streaming: true,
    context_length: contextLength,
    max_output_tokens: 4096,
  },
  origins: ["dynamic"],
  supported_parameters: reasoning ? ["reasoning"] : [],
  input_modalities: ["text"],
  output_modalities: ["text"],
  expiration_date: null,
});

const configuredProvider = {
  id: "openai",
  provider: "openai",
  name: "OpenAI",
  auth_methods: ["api_key"],
  requires_credential: true,
  configured: true,
  credential_state: "configured",
};

describe("filtros reais da página Modelos", () => {
  beforeEach(() => {
    location.hash = "#/modelos";
    HTMLDialogElement.prototype.showModal = vi.fn();
    HTMLDialogElement.prototype.close = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/providers") {
        return jsonResponse({ providers: [
          configuredProvider,
          { ...configuredProvider, id: "anthropic", provider: "anthropic", name: "Anthropic" },
          { ...configuredProvider, id: "groq", provider: "groq", name: "Groq", configured: false,
            credential_state: "missing" },
        ] });
      }
      if (path === "/api/models") {
        return jsonResponse({
          default_provider: "openrouter",
          default_model: "reasoning-large",
          models: [
            model("reasoning-small", 32_000, true),
            model("reasoning-large", 128_000, true),
            model("unknown-large", 256_000, null),
          ],
        });
      }
      if (path === "/api/models?include_preview=true") {
        return jsonResponse({
          default_provider: "openrouter",
          default_model: "reasoning-large",
          models: [
            model("reasoning-large", 128_000, true),
            model("reasoning-preview", 196_000, true, "preview"),
          ],
        });
      }
      if (path === "/api/models/selection") {
        return jsonResponse({ status: "updated", selection: JSON.parse(String(init?.body)) });
      }
      throw new Error(`Request inesperado: ${path}`);
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("filtra por raciocínio explícito e contexto mínimo após interação no formulário", async () => {
    const root = document.createElement("main");
    const cleanup = await modelosView(root, {}, { signal: new AbortController().signal });

    const reasoning = root.querySelector<HTMLInputElement>('input[name="reasoning"]')!;
    reasoning.checked = true;
    reasoning.dispatchEvent(new Event("input", { bubbles: true }));
    const minContext = root.querySelector<HTMLInputElement>('input[name="minContext"]')!;
    minContext.value = "64000";
    minContext.dispatchEvent(new Event("input", { bubbles: true }));

    const rows = root.querySelector("[data-model-rows]")!.textContent!;
    expect(rows).toContain("reasoning-large");
    expect(rows).not.toContain("reasoning-small");
    expect(rows).not.toContain("unknown-large");
    expect(root.textContent).toContain("2 configurados");
    cleanup();
  });

  it("busca o catálogo com previews antes de exibir a opção marcada", async () => {
    const root = document.createElement("main");
    const cleanup = await modelosView(root, {}, { signal: new AbortController().signal });

    const includePreview = root.querySelector<HTMLInputElement>('input[name="includePreview"]')!;
    includePreview.checked = true;
    includePreview.dispatchEvent(new Event("input", { bubbles: true }));
    await flush();

    expect(fetch).toHaveBeenCalledWith(
      "/api/models?include_preview=true",
      expect.objectContaining({ method: "GET" }),
    );
    expect(root.querySelector("[data-model-rows]")!.textContent).toContain("reasoning-preview");
    cleanup();
  });

  it("atualiza o padrão exibido depois de aplicar uma seleção global", async () => {
    const root = document.createElement("main");
    const cleanup = await modelosView(root, {}, { signal: new AbortController().signal });
    root.querySelector<HTMLButtonElement>(
      '[data-choose-model="openrouter/unknown-large"]',
    )!.click();

    root.querySelector<HTMLButtonElement>("[data-apply-model]")!.click();
    await flush();

    expect(fetch).toHaveBeenCalledWith(
      "/api/models/selection",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          provider: "openrouter",
          model: "unknown-large",
          scope: "global",
          parameters: { routing: { data_collection: "deny", require_parameters: true, allow_fallbacks: true } },
        }),
      }),
    );
    expect(root.querySelector("[data-default-selection]")!.textContent)
      .toContain("openrouter/unknown-large");
    expect(root.querySelector('[data-choose-model="openrouter/unknown-large"]')!
      .closest("tr")!.classList.contains("k-tabela__destaque")).toBe(true);
    cleanup();
  });
});

describe("estado verdadeiro da página Provedores", () => {
  beforeEach(() => {
    vi.stubGlobal("CSS", { escape: (value: string) => value });
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("começa sem afirmar conexão e atualiza o selo após sucesso ou falha", async () => {
    const results = [
      { connected: true, state: "available", message: "3 modelos encontrados", models_count: 3 },
      { connected: false, state: "unavailable", message: "Falha de autenticação", models_count: 0 },
    ];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/providers") return jsonResponse({ providers: [configuredProvider] });
      if (path === "/api/providers/openai/test") return jsonResponse(results.shift());
      throw new Error(`Request inesperado: ${path}`);
    }));
    const root = document.createElement("main");
    const cleanup = await provedoresView(root, {}, { signal: new AbortController().signal });
    const badge = root.querySelector("[data-provider-connection]")!;
    const testButton = root.querySelector<HTMLButtonElement>("[data-test-provider]")!;

    expect(badge.textContent).toContain("Conexão não testada");
    testButton.click();
    await flush();
    expect(badge.textContent).toContain("Conexão verificada");
    testButton.click();
    await flush();
    expect(badge.textContent).toContain("Falha na conexão");
    expect(root.textContent).not.toContain("Geração garantida");
    cleanup();
  });

  it("uma credencial substituída invalida teste anterior e ignora sua resposta atrasada", async () => {
    const probe = deferred<JsonRecord>();
    const save = deferred<JsonRecord>();
    let probeCount = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/providers") return jsonResponse({ providers: [configuredProvider] });
      if (path === "/api/providers/openai/test") {
        probeCount += 1;
        if (probeCount === 1) {
          return jsonResponse({ connected: true, state: "available", message: "ok", models_count: 3 });
        }
        return jsonResponse(await probe.promise);
      }
      if (path === "/api/providers/openai/credentials") {
        expect(JSON.parse(String(init?.body))).toEqual({ secret: "nova-chave", auth_method: "api_key" });
        return jsonResponse(await save.promise);
      }
      throw new Error(`Request inesperado: ${path}`);
    }));
    const root = document.createElement("main");
    const cleanup = await provedoresView(root, {}, { signal: new AbortController().signal });
    const testButton = root.querySelector<HTMLButtonElement>("[data-test-provider]")!;
    testButton.click();
    await flush();
    expect(root.querySelector("[data-provider-connection]")!.textContent)
      .toContain("Conexão verificada");
    testButton.click();
    const form = root.querySelector<HTMLFormElement>("[data-credential-form]")!;
    const input = form.elements.namedItem("secret") as HTMLInputElement;
    input.value = "nova-chave";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();

    expect(root.querySelector("[data-provider-connection]")!.textContent)
      .toContain("Conexão não testada");
    expect(testButton.disabled).toBe(true);
    probe.resolve({ connected: true, state: "available", message: "ok", models_count: 3 });
    await flush();
    expect(root.querySelector("[data-provider-connection]")!.textContent)
      .toContain("Conexão não testada");
    expect(testButton.disabled).toBe(true);
    save.resolve({ provider: "openai", state: "configured" });
    await flush();
    expect(testButton.disabled).toBe(false);
    expect(root.innerHTML).not.toContain("nova-chave");
    cleanup();
  });

  it("serializa substituições de credencial enquanto a primeira gravação está pendente", async () => {
    const save = deferred<JsonRecord>();
    const savedBodies: JsonRecord[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/providers") return jsonResponse({ providers: [configuredProvider] });
      if (path === "/api/providers/openai/credentials") {
        savedBodies.push(JSON.parse(String(init?.body)));
        return jsonResponse(await save.promise);
      }
      throw new Error(`Request inesperado: ${path}`);
    }));
    const root = document.createElement("main");
    const cleanup = await provedoresView(root, {}, { signal: new AbortController().signal });
    const form = root.querySelector<HTMLFormElement>("[data-credential-form]")!;
    const input = form.elements.namedItem("secret") as HTMLInputElement;
    const saveButton = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
    const testButton = root.querySelector<HTMLButtonElement>("[data-test-provider]")!;

    input.value = "chave-a";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    input.value = "chave-b";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();

    expect(savedBodies).toEqual([{ secret: "chave-a", auth_method: "api_key" }]);
    expect(input.disabled).toBe(true);
    expect(saveButton.disabled).toBe(true);
    expect(testButton.disabled).toBe(true);
    save.resolve({ provider: "openai", state: "configured" });
    await flush();
    expect(input.disabled).toBe(false);
    expect(saveButton.disabled).toBe(false);
    expect(testButton.disabled).toBe(false);
    cleanup();
  });

  it("aborto de rota impede teste atrasado de alterar o DOM", async () => {
    const probe = deferred<JsonRecord>();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/providers") return jsonResponse({ providers: [configuredProvider] });
      if (path === "/api/providers/openai/test") return jsonResponse(await probe.promise);
      throw new Error(`Request inesperado: ${path}`);
    }));
    const controller = new AbortController();
    const root = document.createElement("main");
    await provedoresView(root, {}, { signal: controller.signal });
    root.querySelector<HTMLButtonElement>("[data-test-provider]")!.click();
    controller.abort();
    const before = root.innerHTML;

    probe.resolve({ connected: true, state: "available", message: "ok", models_count: 3 });
    await flush();

    expect(root.innerHTML).toBe(before);
  });
});
