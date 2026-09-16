// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error The shipped API uses native JavaScript modules.
import { api } from "../../../kairos_web/ui/js/api.js";
// @ts-expect-error The shipped view uses native JavaScript modules.
import { logsView } from "../../../kairos_web/ui/js/views/logs.js";
// @ts-expect-error The shipped shell uses native JavaScript modules.
import { iniciar } from "../../../kairos_web/ui/js/app.js";

type Payload = Record<string, any>;
const event = { timestamp: "2026-09-12T16:00:00Z", code: "chat.completed", service: "chat",
  level: "info", message: "Chamada ao modelo concluída.", counters: { api_calls: 2, input_tokens: 30, output_tokens: 12 } };
const ready = { state: "ready", source: "service-events", events: [event] };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const deferred = () => {
  let resolve!: (value: Response) => void;
  return { promise: new Promise<Response>(done => { resolve = done; }), resolve: (value: Response) => resolve(value) };
};
let root: HTMLElement;
let controller: AbortController;
let requests: Array<{ url: URL; signal?: AbortSignal | null }>;
let respond: (url: URL) => Response | Promise<Response>;
const mount = async () => { await logsView(root, {}, { signal: controller.signal }); };
const change = (name: string, value: string) => {
  const input = root.querySelector<HTMLInputElement>(`[name="${name}"]`)!;
  input.value = value;
  input.dispatchEvent(new Event("change", { bubbles: true }));
};
beforeEach(() => {
  root = document.createElement("main");
  document.body.replaceChildren(root);
  controller = new AbortController();
  requests = [];
  respond = () => response(ready);
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value() { this.open = false; this.dispatchEvent(new Event("close")); } });
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    const url = new URL(path, "http://localhost");
    requests.push({ url, signal: init?.signal });
    return respond(url);
  });
});
afterEach(() => { controller.abort(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("service event records", () => {
  it("sends explicit API filters and forwards cancellation to fetch", async () => {
    expect(api.logs).toBeTypeOf("function");
    await api.logs({ limit: 25, service: "search", level: "error", signal: controller.signal });
    expect(requests[0]!.url.pathname).toBe("/api/logs");
    expect(Object.fromEntries(requests[0]!.url.searchParams)).toEqual({ limit: "25", service: "search", level: "error" });
    expect(requests[0]!.signal).toBe(controller.signal);
    await api.logs();
    expect(Object.fromEntries(requests[1]!.url.searchParams)).toEqual({ limit: "50" });
  });

  it("renders recent events with dates, localized counters and escaped content", async () => {
    respond = () => response({ ...ready, events: [event, { ...event, timestamp: "2026-09-12T15:59:00Z",
      message: '<img src=x onerror="alert(1)">', service: '<script>x</script>',
      counters: { results: '<svg onload="alert(2)">' } }] });
    await mount();
    await vi.waitFor(() => expect(root.querySelectorAll("[data-log-event]")).toHaveLength(2));
    const rows = root.querySelectorAll("[data-log-event]");
    expect(rows[0]!.textContent).toContain("Chamada ao modelo concluída.");
    expect(rows[0]!.querySelector("time")!.dateTime).toBe("2026-09-12T16:00:00Z");
    expect(rows[0]!.textContent).toContain("Chamadas ao modelo: 2");
    expect(rows[0]!.textContent).toContain("Tokens de entrada: 30");
    expect(rows[1]!.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(rows[1]!.textContent).toContain('<svg onload="alert(2)">');
    expect(root.querySelector("img, script, svg")).toBeNull();
    expect(root.textContent).toContain("Eventos dos serviços");
  });

  it("opens a detail dialog with code, counters and meta fields", async () => {
    respond = () => response({ ...ready, events: [{ ...event,
      meta: { origin: "web", call_type: "chat", status: "completed", duration_ms: 150,
        conversation_id: "conv-001", tool: "web_search", error: "auth" },
      counters: { api_calls: 2, results: 5 } }] });
    await mount();
    await vi.waitFor(() => expect(root.querySelectorAll("[data-log-event]")).toHaveLength(1));
    root.querySelector<HTMLElement>("[data-log-event]")!.click();
    await vi.waitFor(() => expect(document.querySelector("dialog")).not.toBeNull());
    const dialog = document.querySelector("dialog")!;
    expect(dialog.textContent).toContain("Chamada ao modelo concluída.");
    expect(dialog.textContent).toContain("chat.completed");
    expect(dialog.textContent).toContain("Chamadas ao modelo");
    expect(dialog.textContent).toContain("Resultados");
    expect(dialog.textContent).toContain("Origem");
    expect(dialog.textContent).toContain("web");
    expect(dialog.textContent).toContain("Duração");
    expect(dialog.textContent).toContain("150 ms");
    expect(dialog.textContent).toContain("Conversa");
    expect(dialog.textContent).toContain("conv-001");
    dialog.close();
    await new Promise(done => setTimeout(done, 0));
    expect(document.querySelector("dialog")).toBeNull();
  });

  it.each([
    [{ state: "ready", source: "service-events", events: [] }, "Nenhum registro encontrado", false],
    [{ state: "unavailable", source: "service-events", events: [] }, "Registros indisponíveis", false],
    [{ state: "error", source: "service-events", events: [], error: "Falha segura." }, "Não foi possível carregar os registros", true],
  ])("distinguishes an API state: %j", async (payload, text, alert) => {
    respond = () => response(payload);
    await mount();
    await vi.waitFor(() => expect(root.textContent).toContain(text));
    expect(Boolean(root.querySelector('[role="alert"]'))).toBe(alert);
  });

  it.each(["invalid_journal", "read_failed"])("shows readable recovery guidance for %s", async (code) => {
    respond = () => response({ state: "error", source: "service-events", events: [], error: code });
    await mount();
    await vi.waitFor(() => expect(root.querySelector('[role="alert"]')).not.toBeNull());
    const message = root.querySelector('[role="alert"]')!.textContent!;
    expect(message).not.toContain(code);
    expect(message).toContain("Tente novamente em instantes.");
    expect(message).toContain("Atualizar");
  });

  it("shows loading and retries a transport error using manual refresh", async () => {
    const loading = deferred();
    respond = () => loading.promise;
    await mount();
    expect(root.textContent).toContain("Carregando registros");
    loading.resolve(response({ error: "Falha segura." }, 503));
    await vi.waitFor(() => expect(root.querySelector('[role="alert"]')?.textContent).toContain("Falha segura."));
    respond = () => response(ready);
    root.querySelector<HTMLButtonElement>("[data-logs-refresh]")!.click();
    await vi.waitFor(() => expect(root.querySelectorAll("[data-log-event]")).toHaveLength(1));
    expect(requests).toHaveLength(2);
  });

  it("applies filters, validates the limit and ignores superseded requests", async () => {
    await mount();
    await vi.waitFor(() => expect(root.querySelectorAll("[data-log-event]")).toHaveLength(1));
    const late = deferred();
    respond = () => late.promise;
    change("service", "search");
    const obsolete = requests.at(-1)!;
    respond = () => response({ ...ready, events: [{ ...event, service: "search", level: "error", message: "Resultado atual" }] });
    change("level", "error");
    await vi.waitFor(() => expect(root.textContent).toContain("Resultado atual"));
    expect(obsolete.signal?.aborted).toBe(true);
    late.resolve(response({ ...ready, events: [{ ...event, message: "Resultado atrasado" }] }));
    await new Promise(done => setTimeout(done, 0));
    expect(root.textContent).not.toContain("Resultado atrasado");
    change("limit", "200");
    await vi.waitFor(() => expect(requests.at(-1)!.url.searchParams.get("limit")).toBe("200"));
    expect(Object.fromEntries(requests.at(-1)!.url.searchParams)).toEqual({ limit: "200", service: "search", level: "error" });
    const count = requests.length;
    change("limit", "201");
    expect(requests).toHaveLength(count);
    expect(root.querySelector<HTMLInputElement>('[name="limit"]')!.validity.valid).toBe(false);
  });

  it("aborts a pending fetch and leaves the next route untouched after cleanup", async () => {
    const loading = deferred();
    respond = () => loading.promise;
    await mount();
    controller.abort();
    root.innerHTML = "<h1>Outra tela</h1>";
    loading.resolve(response(ready));
    await new Promise(done => setTimeout(done, 0));
    expect(requests[0]!.signal?.aborted).toBe(true);
    expect(root.textContent).toBe("Outra tela");
  });

  it("opens the native Registros route through the real application shell", async () => {
    history.replaceState(null, "", "#/registros");
    respond = url => response(url.pathname === "/api/auth/me" ? { authenticated: true }
      : url.pathname === "/api/health" ? { status: "ok", version: "test" } : ready);
    await iniciar();
    await vi.waitFor(() => expect(document.querySelector('#conteudo h1')?.textContent).toBe("Registros"));
    expect(document.querySelector('[data-rota="registros"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector('[data-rota="registros"]')?.getAttribute("href")).toBe("#/registros");
  });
});
