import { afterEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { api as spaApi } from "../../../kairos_web/ui/js/api.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { ChatClient } from "../../../kairos_web/ui/js/chat-client.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { providerCardMarkup } from "../../../kairos_web/ui/js/views/provedores.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { filterModels, modelSelectionMarkup } from "../../../kairos_web/ui/js/views/modelos.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { chatShellMarkup, initialTurnState, reduceTurn, turnMarkup } from "../../../kairos_web/ui/js/views/chat.js";
import {
  PROFILE_QUERY_PARAM,
  REAUTH_ERROR_CODES,
  RECONNECT_MAX_MS,
  reconnectDelay,
  shouldReauthenticate,
  withProfile,
} from "../lib/api.js";
import {
  FIELD_TYPES,
  displayValue,
  resolveFieldType,
  validateField,
  type FieldSchema,
} from "../components/autoField.js";
import {
  KNOWN_SLOT_NAMES,
  SlotRegistry,
  UnknownSlotError,
} from "../plugins/slots.js";

describe("escopo de perfil", () => {
  it("vai por QUERY PARAM, não por header", () => {
    // 🔧 Correção da revisão da spec.
    expect(withProfile("/api/sessions", "trabalho"))
      .toBe(`/api/sessions?${PROFILE_QUERY_PARAM}=trabalho`);
  });

  it("acrescenta com & quando já há query", () => {
    expect(withProfile("/api/x?a=1", "p")).toBe("/api/x?a=1&profile=p");
  });

  it("sem perfil, o caminho fica intacto", () => {
    expect(withProfile("/api/x", null)).toBe("/api/x");
    expect(withProfile("/api/x")).toBe("/api/x");
  });

  it("escapa o nome do perfil", () => {
    expect(withProfile("/api/x", "a b&c")).toContain("a%20b%26c");
  });
});

describe("tratamento de 401", () => {
  it("é SELETIVO por código de erro", () => {
    // Tratar todo 401 como sessão expirada derruba o usuário por causa de um
    // endpoint que exige escopo maior — e ensina a reautenticar por reflexo.
    expect(shouldReauthenticate({ status: 401, code: "session_expired" })).toBe(true);
    expect(shouldReauthenticate({ status: 401, code: "insufficient_scope" })).toBe(false);
    expect(shouldReauthenticate({ status: 401 })).toBe(false);
    expect(shouldReauthenticate({ status: 403, code: "session_expired" })).toBe(false);
  });

  it("os códigos de reautenticação são poucos e nomeados", () => {
    expect(REAUTH_ERROR_CODES.size).toBeGreaterThan(0);
    expect(REAUTH_ERROR_CODES.has("session_expired")).toBe(true);
  });
});

describe("backoff de reconexão", () => {
  it("cresce exponencialmente e satura", () => {
    const semJitter = () => 1;
    expect(reconnectDelay(0, semJitter)).toBe(500);
    expect(reconnectDelay(1, semJitter)).toBe(1000);
    expect(reconnectDelay(20, semJitter)).toBe(RECONNECT_MAX_MS);
  });

  it("aplica jitter TOTAL", () => {
    // Sem jitter, todas as abas reconectam no mesmo milissegundo e a rajada
    // derruba o servidor no momento em que ele está subindo.
    expect(reconnectDelay(5, () => 0)).toBe(0);
    expect(reconnectDelay(5, () => 0.5)).toBeLessThan(reconnectDelay(5, () => 1));
  });

  it("tentativa negativa é recusada", () => {
    expect(() => reconnectDelay(-1)).toThrow(RangeError);
  });
});

describe("AutoField", () => {
  const s = (o: Partial<FieldSchema>): FieldSchema =>
    ({ key: "k", type: "text", ...o }) as FieldSchema;

  it("os cinco tipos REAIS", () => {
    // 🔧 A spec listava string/int/secret; os reais são text/number/list.
    expect([...FIELD_TYPES].sort())
      .toEqual(["boolean", "list", "number", "select", "text"]);
  });

  it("tipo desconhecido cai em text em vez de quebrar o formulário", () => {
    expect(resolveFieldType(s({ type: "inventado" as never }))).toBe("text");
  });

  it("campo obrigatório vazio reprova", () => {
    expect(validateField(s({ required: true }), "")).not.toBeNull();
    expect(validateField(s({ required: true, type: "list" }), [])).not.toBeNull();
    expect(validateField(s({ required: true }), "x")).toBeNull();
  });

  it("número respeita min e max", () => {
    const schema = s({ type: "number", min: 1, max: 10 });
    expect(validateField(schema, 5)).toBeNull();
    expect(validateField(schema, 0)?.message).toContain("mínimo");
    expect(validateField(schema, 11)?.message).toContain("máximo");
    expect(validateField(schema, "abc")?.message).toContain("número");
  });

  it("select recusa valor fora das opções", () => {
    const schema = s({ type: "select", options: ["a", "b"] });
    expect(validateField(schema, "a")).toBeNull();
    expect(validateField(schema, "z")).not.toBeNull();
  });

  it("booleano e lista checam o tipo", () => {
    expect(validateField(s({ type: "boolean" }), "sim" as never)).not.toBeNull();
    expect(validateField(s({ type: "boolean" }), true)).toBeNull();
    expect(validateField(s({ type: "list" }), "a" as never)).not.toBeNull();
    expect(validateField(s({ type: "list" }), ["a"])).toBeNull();
  });

  it("campo secreto é MASCARADO na exibição", () => {
    // A spec registra que o mascaramento não foi localizado no legado. Um
    // campo de chave de API em texto claro é vazamento por tela compartilhada.
    const out = displayValue(s({ secret: true }), "sk-super-secreto");
    expect(out).not.toContain("sk-");
    expect(out).toMatch(/^•+$/);
  });

  it("campo comum não é mascarado", () => {
    expect(displayValue(s({}), "visível")).toBe("visível");
    expect(displayValue(s({ type: "list" }), ["a", "b"])).toBe("a, b");
  });
});

describe("slots de plugin", () => {
  it("são 30 slots nomeados", () => {
    expect(KNOWN_SLOT_NAMES.length).toBe(30);
    expect(new Set(KNOWN_SLOT_NAMES).size).toBe(30);
  });

  it("slot desconhecido é REJEITADO, não ignorado", () => {
    // Um widget que nunca aparece e não avisa é a pior falha para quem está
    // escrevendo o plugin.
    const r = new SlotRegistry();
    expect(() => r.register("nav.inventado", "p", {})).toThrow(UnknownSlotError);
  });

  it("a ordem é determinística", () => {
    const r = new SlotRegistry<string>();
    r.register("nav.primary", "zeta", "z", 1);
    r.register("nav.primary", "alfa", "a", 1);
    r.register("nav.primary", "meio", "m", 0);
    expect(r.entries("nav.primary").map((e) => e.plugin))
      .toEqual(["meio", "alfa", "zeta"]);
  });

  it("remover um plugin limpa todos os seus widgets", () => {
    const r = new SlotRegistry<string>();
    r.register("nav.primary", "p", "a");
    r.register("chat.sidebar", "p", "b");
    r.register("nav.primary", "outro", "c");
    expect(r.removePlugin("p")).toBe(2);
    expect(r.entries("nav.primary").map((e) => e.plugin)).toEqual(["outro"]);
    expect(r.entries("chat.sidebar")).toEqual([]);
  });

  it("slot vazio devolve lista vazia", () => {
    expect(new SlotRegistry().entries("global.modal")).toEqual([]);
  });
});

describe("cliente canônico da SPA", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("serializa filtros de provider e gratuidade do OpenRouter", async () => {
    let requestedUrl = "";
    vi.stubGlobal("window", {});
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      requestedUrl = url;
      return new Response(JSON.stringify({ models: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));

    await spaApi.modelos({ provider: "openrouter", freeOnly: true });

    expect(requestedUrl).toBe("/api/models?provider=openrouter&free_only=true");
  });

  it("envia seleção canônica com seu escopo", async () => {
    let sentBody: unknown;
    vi.stubGlobal("window", {});
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      sentBody = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ status: "updated" }), { status: 200 });
    }));

    await spaApi.selecionarModelo({
      provider: "openai",
      model: "gpt-5.6-terra",
      scope: "global",
    });

    expect(sentBody).toEqual({
      provider: "openai",
      model: "gpt-5.6-terra",
      scope: "global",
    });
  });

  it("ignora eventos WebSocket aditivos e entrega os conhecidos", () => {
    const onEvent = vi.fn();
    const client = new ChatClient({ onEvent });

    expect(() => client.accept({ protocol: 1, type: "future_event" })).not.toThrow();
    client.accept({ protocol: 1, type: "delta", text: "olá" });

    expect(onEvent).toHaveBeenCalledOnce();
    expect(onEvent).toHaveBeenCalledWith({ protocol: 1, type: "delta", text: "olá" });
  });
});

describe("página de provedores", () => {
  it("nunca inclui segredo no cartão após salvar credencial", () => {
    const secret = "sk-nao-renderizar";
    const markup = providerCardMarkup({
      id: "openai",
      provider: "openai",
      name: "OpenAI",
      auth_methods: ["api_key"],
      requires_credential: true,
      configured: true,
      credential_state: "configured",
      savedSecret: secret,
    });

    expect(markup).not.toContain(secret);
    expect(markup).not.toContain("masked_identifier");
  });

  it("mostra Ollama sem formulário de credencial", () => {
    const markup = providerCardMarkup({
      id: "ollama",
      provider: "ollama",
      name: "Ollama",
      auth_methods: [],
      requires_credential: false,
      configured: true,
      credential_state: "not_required",
    });

    expect(markup).toContain("Sem credencial necessária");
    expect(markup).not.toContain('type="password"');
  });
});

describe("catálogo de modelos", () => {
  const free = {
    id: "openrouter/free",
    provider: "openrouter",
    name: "OpenRouter Free",
    is_free: true,
    stability: "stable",
    pricing: { prompt: "0", completion: "0", request: "0" },
    capabilities: { chat: true, tools: true, vision: false, context_length: 128000 },
    origins: ["curated"],
  };
  const paid = {
    ...free,
    id: "anthropic/paid",
    name: "Pago",
    is_free: false,
    pricing: { prompt: "0.000003", completion: "0.000015", request: null },
  };

  it("somente gratuitos remove modelos pagos do OpenRouter", () => {
    expect(filterModels([free, paid], { provider: "openrouter", freeOnly: true }))
      .toEqual([free]);
  });

  it("troca de modelo oferece próximo turno e nova conversa", () => {
    const markup = modelSelectionMarkup(free);
    expect(markup).toContain("Aplicar ao próximo turno");
    expect(markup).toContain("Iniciar nova conversa");
  });

  it("preview fica oculto até o filtro explícito", () => {
    const preview = { ...free, id: "preview", stability: "preview" };
    expect(filterModels([free, preview], {})).toEqual([free]);
    expect(filterModels([free, preview], { includePreview: true })).toEqual([free, preview]);
  });
});

describe("Chat principal", () => {
  it("bloqueia o composer quando nenhum provider é utilizável", () => {
    const markup = chatShellMarkup({ providers: [], sessions: [], models: [] });
    expect(markup).toContain("Configurar um provedor");
    expect(markup).toContain("textarea disabled");
  });

  it("mostra provider, modelo e gratuidade no painel de contexto", () => {
    const markup = chatShellMarkup({
      providers: [{ id: "openrouter", configured: true }],
      sessions: [],
      models: [{
        id: "openrouter/free", provider: "openrouter", name: "OpenRouter Free",
        is_free: true, capabilities: { tools: true },
      }],
      selection: { provider: "openrouter", model: "openrouter/free" },
    });
    expect(markup).toContain("openrouter");
    expect(markup).toContain("OpenRouter Free");
    expect(markup).toContain("Gratuito");
  });

  it("preserva texto parcial quando o turno termina com erro", () => {
    let state = initialTurnState();
    state = reduceTurn(state, { protocol: 1, type: "turn_start" });
    state = reduceTurn(state, { protocol: 1, type: "delta", text: "parcial" });
    state = reduceTurn(state, {
      protocol: 1, type: "turn_error", error_kind: "rate_limit", error: "limite",
    });
    expect(state.text).toBe("parcial");
    expect(state.error).toBe("limite");
    expect(state.status).toBe("error");
  });

  it("resume tools sem guardar argumentos brutos", () => {
    const state = reduceTurn(initialTurnState(), {
      protocol: 1,
      type: "tool_call",
      tool_call: { id: "tool-1", name: "search", arguments: '{"secret":"x"}' },
    });
    expect(state.tools).toEqual([{ id: "tool-1", name: "search", status: "running" }]);
    expect(JSON.stringify(state)).not.toContain("secret");
  });

  it("mantém o composer bloqueado enquanto o WebSocket ainda conecta", () => {
    const markup = chatShellMarkup({
      providers: [{ id: "ollama", configured: true }], sessions: [], models: [],
    });
    expect(markup).toContain("textarea disabled");
  });

  it("não deixa um turno terminal marcado para substituição", () => {
    expect(turnMarkup({ ...initialTurnState(), status: "done", text: "primeira" }))
      .not.toContain("data-active-turn");
    expect(turnMarkup({ ...initialTurnState(), status: "streaming", text: "segunda" }))
      .toContain("data-active-turn");
  });
});
