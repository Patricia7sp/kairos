// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error Production views use direct JavaScript modules.
import { integracoesView } from "../../../kairos_web/ui/js/views/integracoes.js";
// @ts-expect-error Production API uses a direct JavaScript module.
import { api } from "../../../kairos_web/ui/js/api.js";

const baseStatus = () => ({
  adapters: [],
  obligations: { pending: 0, claimed: 0, delivered: 0, abandoned: 0 },
  platforms: [
    {
      platform: "telegram", label: "Telegram",
      enabled: true, configured: true, deliverable: true,
      secreto_campo: "Token do bot", secreto_dica: "123456:ABC",
      campos: [{ key: "chat_id_default", label: "Chat padrão" }],
      valores: { enabled: true, chat_id_default: "123456789" }, vault: "unlocked",
    },
    {
      platform: "webhook", label: "Webhook",
      enabled: true, configured: false, deliverable: true,
      secreto_campo: "", secreto_dica: "",
      campos: [],
      valores: { enabled: true, endpoints: [] }, vault: "unlocked",
    },
  ],
});

const baseWebhooks = () => ({
  enabled: false,
  endpoints: [
    { name: "alerta", url: "https://hooks.exemplo.com/x" },
    { name: "aviso", url: "http://hooks.local/y" },
  ],
});

let root: HTMLElement;
let controller: AbortController;
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const mount = async () => {
  await integracoesView(root, {}, { signal: controller.signal });
  await flush();
};
const change = (selector: string, value: string, event: string) => {
  const field = root.querySelector<HTMLInputElement>(selector)!;
  field.value = value;
  field.dispatchEvent(new Event(event));
};

beforeEach(() => {
  root = document.createElement("main");
  document.body.replaceChildren(root);
  controller = new AbortController();
  vi.spyOn(api, "mensageriaStatus").mockResolvedValue(baseStatus());
  vi.spyOn(api, "endpointsWebhook").mockResolvedValue(baseWebhooks());
  vi.spyOn(api, "criarEndpointWebhook").mockResolvedValue({});
  vi.spyOn(api, "removerEndpointWebhook").mockResolvedValue({});
  vi.spyOn(api, "testarMensageria").mockResolvedValue({ ok: true, message: "conectado" });
  vi.spyOn(api, "salvarMensageria").mockResolvedValue({});
});
afterEach(() => { controller.abort(); vi.restoreAllMocks(); });

describe("integrações screen", () => {
  it("starts on Mensageria with tabs announced and webhook own panel", async () => {
    await mount();
    const tabs = root.querySelectorAll<HTMLElement>("[role='tab']");
    expect(tabs).toHaveLength(2);
    expect(tabs[0]!.getAttribute("aria-selected")).toBe("true");
    expect(tabs[1]!.getAttribute("aria-selected")).toBe("false");
    expect(root.querySelector('[data-painel="canais"]')).not.toBeNull();
    expect(root.querySelector('[data-painel="webhooks"]')?.hasAttribute("hidden")).toBe(true);
    // a aba de mensageria monta os canais sem o webhook
    expect(root.querySelector('[data-cm="telegram"]')?.textContent).toContain("Entregando");
    expect(root.querySelector('[data-cm="webhook"]')).toBeNull();
  });

  it("switches to Webhooks and lists endpoints with their names and URLs", async () => {
    await mount();
    root.querySelector<HTMLElement>('[data-tab="webhooks"]')!.click();
    await flush();
    expect(root.querySelector('[data-painel="webhooks"]')?.hasAttribute("hidden")).toBe(false);
    expect(root.querySelector('[data-painel="canais"]')?.hasAttribute("hidden")).toBe(true);
    expect(root.querySelector('[data-tab="webhooks"]')?.getAttribute("aria-selected")).toBe("true");
    const rows = root.querySelectorAll("[data-wh-tabela] tbody tr");
    expect(rows).toHaveLength(2);
    expect(root.textContent).toContain("webhook:alerta");
    expect(root.textContent).toContain("https://hooks.exemplo.com/x");
  });

  it("adds a named endpoint and refreshes the list", async () => {
    await mount();
    root.querySelector<HTMLElement>('[data-tab="webhooks"]')!.click();
    await flush();
    api.endpointsWebhook.mockResolvedValue({
      enabled: false,
      endpoints: [...baseWebhooks().endpoints, { name: "novo", url: "https://x.novo" }],
    });
    change("[data-wh-form] [name='nome']", "novo", "input");
    change("[data-wh-form] [name='url']", "https://x.novo", "input");
    root.querySelector<HTMLFormElement>("[data-wh-form]")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    expect(api.criarEndpointWebhook).toHaveBeenCalledWith("novo", "https://x.novo");
    expect(root.querySelector("[data-wh-status]")?.textContent).toContain("Endpoint novo adicionado");
    expect(root.querySelectorAll("[data-wh-tabela] tbody tr")).toHaveLength(3);
  });

  it("rejects a non-http URL without calling the backend", async () => {
    await mount();
    root.querySelector<HTMLElement>('[data-tab="webhooks"]')!.click();
    await flush();
    change("[data-wh-form] [name='nome']", "ruim", "input");
    change("[data-wh-form] [name='url']", "ftp://x", "input");
    root.querySelector<HTMLFormElement>("[data-wh-form]")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    expect(api.criarEndpointWebhook).not.toHaveBeenCalled();
    expect(root.querySelector("[data-wh-status]")?.textContent).toContain("URL inválida");
  });

  it("tests a single endpoint by its named target", async () => {
    api.testarMensageria.mockResolvedValue({ ok: true, sent: true, message: "enviado" });
    await mount();
    root.querySelector<HTMLElement>('[data-tab="webhooks"]')!.click();
    await flush();
    root.querySelector<HTMLButtonElement>('[data-wh-test="alerta"]')!.click();
    await flush();
    expect(api.testarMensageria).toHaveBeenCalledWith("webhook", "webhook:alerta");
    expect(root.querySelector("[data-wh-status]")?.textContent).toContain("Mensagem de teste enviada");
  });

  it("removes an endpoint after confirmation and hides it from the list", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await mount();
    root.querySelector<HTMLElement>('[data-tab="webhooks"]')!.click();
    await flush();
    api.endpointsWebhook.mockResolvedValue({
      enabled: false,
      endpoints: [{ name: "aviso", url: "http://hooks.local/y" }],
    });
    root.querySelector<HTMLButtonElement>('[data-wh-remove="alerta"]')!.click();
    await flush();
    expect(api.removerEndpointWebhook).toHaveBeenCalledWith("alerta");
    expect(root.querySelector('[data-wh-test="alerta"]')).toBeNull();
    expect(root.querySelector('[data-wh-test="aviso"]')).not.toBeNull();
  });

  it("persists the enable toggle with the current endpoints, not an empty list", async () => {
    await mount();
    root.querySelector<HTMLElement>('[data-tab="webhooks"]')!.click();
    await flush();
    api.endpointsWebhook.mockResolvedValue({ enabled: true, endpoints: baseWebhooks().endpoints });
    root.querySelector<HTMLInputElement>("[data-wh-enabled]")!.click();
    await flush();
    expect(api.salvarMensageria).toHaveBeenCalledWith("webhook", {
      enabled: true,
      endpoints: baseWebhooks().endpoints,
    });
    expect(root.querySelector("[data-wh-status]")?.textContent).toContain("habilitados");
  });
});