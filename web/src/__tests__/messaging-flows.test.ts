// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error Production views use direct JavaScript modules.
import { mensageriaView } from "../../../kairos_web/ui/js/views/mensageria.js";
// @ts-expect-error Production API uses a direct JavaScript module.
import { api } from "../../../kairos_web/ui/js/api.js";

const baseStatus = () => {
  const shared = {
    enabled: true, configured: false, deliverable: false,
    secreto_campo: "Segredo", secreto_dica: "",
  };
  return {
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
        ...shared, platform: "whatsapp", label: "WhatsApp",
        campos: [
          { key: "phone_number_id", label: "ID do número" },
          { key: "number_default", label: "Número padrão" },
        ],
        valores: { enabled: true, phone_number_id: "", number_default: "" }, vault: "unlocked",
      },
      {
        ...shared, platform: "slack", label: "Slack",
        campos: [{ key: "channel_default", label: "Canal padrão" }],
        valores: { enabled: true, channel_default: "" }, vault: "unlocked",
      },
      {
        platform: "webhook", label: "Webhook",
        enabled: false, configured: false, deliverable: false,
        secreto_campo: "", secreto_dica: "",
        campos: [],
        valores: { enabled: false, endpoints: [] }, vault: "unlocked",
      },
    ],
  };
};

let root: HTMLElement;
let controller: AbortController;
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const mount = async () => {
  await mensageriaView(root, {}, { signal: controller.signal });
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
  vi.spyOn(api, "salvarMensageria").mockResolvedValue({});
  vi.spyOn(api, "salvarCredencialMensageria").mockResolvedValue({});
  vi.spyOn(api, "removerCredencialMensageria").mockResolvedValue({});
  vi.spyOn(api, "testarMensageria").mockResolvedValue({ ok: true, message: "conectado" });
  vi.spyOn(api, "enviarMensagem").mockResolvedValue({ delivered: true, pending: false, obligation_id: "abc123" });
});
afterEach(() => { controller.abort(); vi.restoreAllMocks(); });

describe("mensageria screen", () => {
  it("renders each platform card with the real delivery state", async () => {
    await mount();
    const cards = root.querySelectorAll("[data-cm]");
    expect(cards).toHaveLength(4);
    expect(root.querySelector('[data-cm="telegram"]')?.textContent).toContain("Entregando");
    expect(root.querySelector('[data-cm="whatsapp"]')?.textContent).toContain("Sem credencial");
    expect(root.querySelector('[data-cm="webhook"]')?.textContent).toContain("Não configurado");
  });

  it("shows the vault banner when the credential vault is locked", async () => {
    const payload = baseStatus();
    payload.platforms[0]!.vault = "locked";
    api.mensageriaStatus.mockResolvedValue(payload);
    await mount();
    expect(root.querySelector("[data-cm-vault]")?.textContent).toContain("bloqueado");
  });

  it("reports the jobs awaiting redelivery on the send card", async () => {
    const payload = baseStatus();
    payload.obligations.pending = 2;
    api.mensageriaStatus.mockResolvedValue(payload);
    await mount();
    expect(root.querySelector("[data-cm-send]")?.textContent).toContain("2 pendente(s)");
  });

  it("saves editable config and refreshes the badge from the current registry", async () => {
    await mount();
    change('[data-cm="telegram"] [name="chat_id_default"]', "555", "input");
    change('[data-cm="telegram"] [name="enabled"]', "", "change");
    const payload = baseStatus();
    payload.platforms[0]!.valores.chat_id_default = "555";
    api.mensageriaStatus.mockResolvedValue(payload);
    root.querySelector<HTMLButtonElement>('[data-cm="telegram"] [data-cm-save]')!.click();
    await flush();
    expect(api.salvarMensageria).toHaveBeenCalledWith("telegram", expect.objectContaining({ chat_id_default: "555" }));
    expect(root.querySelector('[data-cm="telegram"] [data-cm-status]')?.textContent).toContain("Configuração salva");
  });

  it("saves a credential to the vault, clears the input and never echoes it", async () => {
    await mount();
    change('[data-cm="telegram"] [name="secret"]', "super-secret", "input");
    api.mensageriaStatus.mockResolvedValue(baseStatus());
    root.querySelector<HTMLElement>('[data-cm="telegram"] [data-cm-credential-form]')!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    expect(api.salvarCredencialMensageria).toHaveBeenCalledWith("telegram", "super-secret");
    expect(root.querySelector<HTMLInputElement>('[data-cm="telegram"] [name="secret"]')!.value).toBe("");
    expect(root.textContent).not.toContain("super-secret");
    expect(root.querySelector('[data-cm="telegram"] [data-cm-status]')?.textContent).toContain("Credencial salva");
  });

  it("rejects an empty credential with an explicit status, without calling the API", async () => {
    await mount();
    root.querySelector<HTMLElement>('[data-cm="telegram"] [data-cm-credential-form]')!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    expect(api.salvarCredencialMensageria).not.toHaveBeenCalled();
    expect(root.querySelector('[data-cm="telegram"] [data-cm-status]')?.textContent).toContain("vazio");
  });

  it("removes the credential at user confirmation and hides the remove action", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await mount();
    root.querySelector<HTMLButtonElement>('[data-cm="telegram"] [data-cm-credential-remove]')!.click();
    await flush();
    expect(api.removerCredencialMensageria).toHaveBeenCalledWith("telegram");
    expect(root.querySelector('[data-cm="telegram"] [data-cm-credential-remove]')).toBeNull();
  });

  it("runs the real test and says whether the platform answered", async () => {
    await mount();
    root.querySelector<HTMLButtonElement>('[data-cm="telegram"] [data-cm-test]')!.click();
    await flush();
    expect(api.testarMensageria).toHaveBeenCalledWith("telegram", "");
    expect(root.querySelector('[data-cm="telegram"] [data-cm-status]')?.textContent).toContain("Conexão verificada");
  });

  it("parses webhook endpoints written one per line", async () => {
    await mount();
    change('[data-cm="webhook"] textarea', "alerta https://hooks.exemplo.com/x\naviso http://hooks.local/y", "input");
    root.querySelector<HTMLInputElement>('[data-cm="webhook"] [name="enabled"]')!.click();
    await flush();
    root.querySelector<HTMLButtonElement>('[data-cm="webhook"] [data-cm-save]')!.click();
    await flush();
    expect(api.salvarMensageria).toHaveBeenCalledWith("webhook", {
      enabled: true,
      endpoints: [
        { name: "alerta", url: "https://hooks.exemplo.com/x" },
        { name: "aviso", url: "http://hooks.local/y" },
      ],
    });
  });

  it("records the send result from delivery and from the queue", async () => {
    await mount();
    change("[data-cm-send-form] [name='target']", "telegram:123", "input");
    change("[data-cm-send-form] [name='text']", "Olá", "input");
    root.querySelector<HTMLFormElement>("[data-cm-send-form]")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    expect(api.enviarMensagem).toHaveBeenCalledWith("telegram:123", "Olá");
    expect(root.querySelector("[data-cm-send-status]")?.textContent).toContain("Entregue");

    api.enviarMensagem.mockResolvedValue({ delivered: false, pending: true, obligation_id: "abc" });
    change("[data-cm-send-form] [name='text']", "depois", "input");
    root.querySelector<HTMLFormElement>("[data-cm-send-form]")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    expect(root.querySelector("[data-cm-send-status]")?.textContent).toContain("Mensagem agendada");
  });

  it("surfaces load errors and offers no fabricated state", async () => {
    api.mensageriaStatus.mockRejectedValueOnce(new Error("Cofre indisponível"));
    await mount();
    expect(root.querySelector("[data-cm-lista] [role='alert']")?.textContent).toContain("Cofre indisponível");
  });

  it("does not write status text after navigation disposes the view", async () => {
    let resolve!: (data: ReturnType<typeof baseStatus>) => void;
    api.mensageriaStatus.mockReturnValue(new Promise((done) => { resolve = done; }));
    const pending = mensageriaView(root, {}, { signal: controller.signal });
    await Promise.resolve();
    controller.abort();
    const vaultEl = root.querySelector("[data-cm-vault]");
    const before = vaultEl?.textContent as string;
    resolve(baseStatus());
    await pending;
    await flush();
    expect((vaultEl as HTMLElement).textContent).toBe(before);
  });
});