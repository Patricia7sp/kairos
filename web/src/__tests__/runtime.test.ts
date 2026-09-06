// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { RuntimeClient } from "../../../kairos_web/ui/js/runtime-client.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { initialRuntimeState, reduceRuntime, renderRuntimeSession, renderRuntimeSetup, runtimeView, safeHttpUrl } from "../../../kairos_web/ui/js/views/runtime.js";
// @ts-expect-error A SPA principal é JavaScript sem etapa de build.
import { api } from "../../../kairos_web/ui/js/api.js";

const event = (overrides: Record<string, unknown> = {}) => ({
  execution_kind: "agent_runtime",
  protocol_version: 1,
  event_id: "event-1",
  session_id: "runtime-1",
  turn_id: "turn-1",
  sequence: 1,
  cursor: "v1:runtime-1:1",
  kind: "text",
  payload: { itemId: "answer", delta: "Olá" },
  ...overrides,
});

describe("reducer do runtime servido", () => {
  it("aplica cursor uma vez e ignora o mesmo evento", () => {
    const before = initialRuntimeState("runtime-1", ["text"]);
    const incoming = event();
    const after = reduceRuntime(before, incoming);

    expect(reduceRuntime(after, incoming)).toEqual(after);
    expect(after.cursor).toBe(incoming.cursor);
    expect(after.items[0].text).toBe("Olá");
  });

  it("preserva o cursor e sinaliza lacuna antes de aplicar evento fora de ordem", () => {
    const first = reduceRuntime(initialRuntimeState("runtime-1"), event());
    const after = reduceRuntime(first, event({
      event_id: "event-3", sequence: 3, cursor: "v1:runtime-1:3",
      payload: { itemId: "answer", delta: " perdido" },
    }));

    expect(after.cursor).toBe("v1:runtime-1:1");
    expect(after.lastSequence).toBe(1);
    expect(after.gap).toBe(true);
    expect(after.items[0].text).toBe("Olá");
  });

  it("snapshot reconciliado substitui o item parcial e mantém a indicação da lacuna", () => {
    const partial = {
      ...reduceRuntime(initialRuntimeState("runtime-1"), event()),
      gap: true,
    };
    const after = reduceRuntime(partial, event({
      event_id: "event-2", sequence: 2, cursor: "v1:runtime-1:2",
      kind: "reconciled",
      payload: {
        reason: "sequence_gap",
        snapshot: { items: [{ id: "answer", type: "agentMessage", text: "Resposta final" }] },
      },
    }));

    expect(after.items).toHaveLength(1);
    expect(after.items[0].text).toBe("Resposta final");
    expect(after.gap).toBe(true);
    expect(after.reconciled).toBe(true);
  });

  it("mantém uso desconhecido visível até chegar uso conhecido", () => {
    const state = reduceRuntime(initialRuntimeState("runtime-1"), event({
      kind: "turn_end", payload: { state: "completed", usage: { status: "unknown" } },
    }));
    const root = document.createElement("div");
    renderRuntimeSession(root, state, {
      session_id: "runtime-1", runtime_kind: "codex", canonical_cwd: "/work",
      sandbox: "read_only", state: "ready", capabilities: { features: ["text"] },
    }, { connected: true });

    expect(root.textContent).toContain("Uso desconhecido");
    expect(root.textContent).not.toContain("0 tokens");
  });
});

describe("DOM e controles do runtime", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("renderiza conteúdo e tools maliciosos somente como texto", () => {
    let state = reduceRuntime(initialRuntimeState("runtime-1", ["text", "tools"]), event({
      payload: { itemId: "answer", delta: '<img src=x onerror="globalThis.pwned=1">' },
    }));
    state = reduceRuntime(state, event({
      event_id: "event-2", sequence: 2, cursor: "v1:runtime-1:2", kind: "tool",
      payload: { item: { id: "tool-1", type: "commandExecution", command: "<svg/onload=alert(1)>" } },
    }));
    const root = document.createElement("div");
    renderRuntimeSession(root, state, {
      session_id: "runtime-1", runtime_kind: "codex", canonical_cwd: "/work/<script>",
      sandbox: "workspace_write", state: "ready", capabilities: { features: ["text", "tools"] },
    }, { connected: true });

    expect(root.querySelector("img, svg, script")).toBeNull();
    expect(root.textContent).toContain("<img src=x");
    expect(root.textContent).toContain("<svg/onload");
    expect(root.textContent).toContain("/work/<script>");
  });

  it("desabilita aprovação pendente enquanto desconectado", () => {
    const state = reduceRuntime(initialRuntimeState("runtime-1", ["approvals"]), event({
      kind: "approval_request",
      payload: { approval_id: "approval-1", details: { command: "git status" } },
    }));
    const root = document.createElement("div");
    renderRuntimeSession(root, state, {
      session_id: "runtime-1", runtime_kind: "codex", canonical_cwd: "/work",
      sandbox: "workspace_write", state: "ready", capabilities: { features: ["approvals"] },
    }, { connected: false });

    expect(root.querySelectorAll("[data-runtime-approval] button:disabled")).toHaveLength(2);
    expect(root.textContent).toContain("Reconecte para responder");
  });

  it("preserva a saída parcial quando o cancelamento ainda não foi confirmado", () => {
    const state = reduceRuntime(initialRuntimeState("runtime-1", ["text", "cancel"]), event({
      payload: { itemId: "answer", delta: "Trecho já recebido" },
    }));
    const root = document.createElement("div");
    renderRuntimeSession(root, state, {
      session_id: "runtime-1", runtime_kind: "codex", canonical_cwd: "/work",
      sandbox: "workspace_write", state: "ready",
      capabilities: { features: ["text", "cancel"] },
    }, { connected: true, error: { error: "cancel_partial" } });

    expect(root.textContent).toContain("Trecho já recebido");
    expect(root.textContent).toContain("Cancelamento ainda não confirmado");
  });

  it("bloqueia criação quando runtime está desabilitado e exige consentimento broad_access", () => {
    const root = document.createElement("div");
    renderRuntimeSetup(root, {
      enabled: false, state: "disabled", authorized_projects: [], sandbox_profiles: [],
    }, null);
    expect(root.querySelector<HTMLButtonElement>("[data-runtime-create]")?.disabled).toBe(true);

    renderRuntimeSetup(root, {
      enabled: true, state: "ready", authorized_projects: ["/work"],
      sandbox_profiles: ["read_only", "workspace_write", "broad_access"],
    }, null);
    const sandbox = root.querySelector<HTMLSelectElement>("[name=sandbox]")!;
    sandbox.value = "broad_access";
    sandbox.dispatchEvent(new Event("change"));
    expect(root.querySelector("[data-runtime-broad-consent]")).not.toBeNull();
    expect(root.querySelector<HTMLButtonElement>("[data-runtime-create]")?.disabled).toBe(true);
    root.querySelector<HTMLInputElement>("[name=consent]")!.click();
    expect(root.querySelector<HTMLButtonElement>("[data-runtime-create]")?.disabled).toBe(false);
  });

  it("aceita somente URLs HTTP(S) para login transitório", () => {
    expect(safeHttpUrl("https://auth.openai.com/start")?.protocol).toBe("https:");
    expect(safeHttpUrl("http://localhost/device")?.protocol).toBe("http:");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,pwned")).toBeNull();
  });

  it("acompanha login pendente até o estado terminal sem persistir a resposta", async () => {
    vi.useFakeTimers();
    location.hash = "#/runtime";
    vi.spyOn(api, "runtimeStatus").mockResolvedValue({
      enabled: true, state: "ready", authorized_projects: ["/work"],
      sandbox_profiles: ["read_only"],
    });
    vi.spyOn(api, "runtimeAccount")
      .mockResolvedValueOnce({ requires_openai_auth: true, authenticated: false, auth_mode: null,
        email: null, plan_type: null, login_state: "idle" })
      .mockResolvedValueOnce({ requires_openai_auth: true, authenticated: false, auth_mode: null,
        email: null, plan_type: null, login_state: "pending" })
      .mockResolvedValueOnce({ requires_openai_auth: true, authenticated: false, auth_mode: null,
        email: null, plan_type: null, login_state: "pending" })
      .mockResolvedValueOnce({ requires_openai_auth: true, authenticated: true, auth_mode: "chatgpt",
        email: "dev@example.com", plan_type: "plus", login_state: "succeeded" });
    vi.spyOn(api, "runtimeLogin").mockResolvedValue({
      mode: "chatgpt", state: "pending", login_id: "transient-id",
      auth_url: "https://auth.openai.com/start",
    });
    const root = document.createElement("div");
    const cleanup = await runtimeView(root);

    root.querySelector<HTMLFormElement>("[data-runtime-login-form]")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(3_000);

    expect(root.textContent).toContain("dev@example.com");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    cleanup();
  });

  it("não renderiza nem consulta conta quando login conclui depois de sair da view", async () => {
    location.hash = "#/runtime";
    vi.spyOn(api, "runtimeStatus").mockResolvedValue({
      enabled: true, state: "ready", authorized_projects: ["/work"],
      sandbox_profiles: ["read_only"],
    });
    const account = vi.spyOn(api, "runtimeAccount").mockResolvedValue({
      requires_openai_auth: true, authenticated: false, auth_mode: null,
      email: null, plan_type: null, login_state: "idle",
    });
    let finishLogin!: (value: unknown) => void;
    vi.spyOn(api, "runtimeLogin").mockImplementation(() => new Promise((resolve) => {
      finishLogin = resolve;
    }));
    const root = document.createElement("div");
    const cleanup = await runtimeView(root);
    const form = root.querySelector<HTMLFormElement>("[data-runtime-login-form]")!;
    const method = form.elements.namedItem("method") as HTMLSelectElement;
    const key = form.elements.namedItem("api_key") as HTMLInputElement;
    method.value = "apiKey";
    method.dispatchEvent(new Event("change"));
    key.value = "sk-transient-sentinel";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(key.value).toBe("");
    cleanup();

    finishLogin({ mode: "chatgptDeviceCode", state: "pending", login_id: "late",
      user_code: "LATE-CODE", verification_url: "https://auth.openai.com/late" });
    await Promise.resolve();
    await Promise.resolve();

    expect(account).toHaveBeenCalledOnce();
    expect(root.textContent).not.toContain("LATE-CODE");
    expect(root.innerHTML).not.toContain("auth.openai.com/late");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("remove URL e código transitórios quando o login é cancelado", async () => {
    location.hash = "#/runtime";
    vi.spyOn(api, "runtimeStatus").mockResolvedValue({
      enabled: true, state: "ready", authorized_projects: ["/work"],
      sandbox_profiles: ["read_only"],
    });
    vi.spyOn(api, "runtimeAccount")
      .mockResolvedValueOnce({ requires_openai_auth: true, authenticated: false, auth_mode: null,
        email: null, plan_type: null, login_state: "idle" })
      .mockResolvedValueOnce({ requires_openai_auth: true, authenticated: false, auth_mode: null,
        email: null, plan_type: null, login_state: "pending" })
      .mockResolvedValueOnce({ requires_openai_auth: true, authenticated: false, auth_mode: null,
        email: null, plan_type: null, login_state: "cancelled" });
    vi.spyOn(api, "runtimeLogin").mockResolvedValue({ mode: "chatgptDeviceCode", state: "pending",
      login_id: "transient-id", user_code: "DEVICE-CODE",
      verification_url: "https://auth.openai.com/device" });
    vi.spyOn(api, "runtimeLoginCancel").mockResolvedValue({ status: "cancelled" });
    const root = document.createElement("div");
    const cleanup = await runtimeView(root);
    const method = root.querySelector<HTMLSelectElement>("[name=method]")!;
    method.value = "chatgptDeviceCode";
    root.querySelector<HTMLFormElement>("[data-runtime-login-form]")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
    expect(root.textContent).toContain("DEVICE-CODE");
    expect(root.querySelector<HTMLAnchorElement>('a[href="https://auth.openai.com/device"]')).not.toBeNull();

    root.querySelector<HTMLButtonElement>("[data-runtime-login-cancel]")!.click();
    await Promise.resolve();
    await Promise.resolve();
    expect(root.textContent).not.toContain("DEVICE-CODE");
    expect(root.innerHTML).not.toContain("auth.openai.com/device");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    cleanup();
  });
});

describe("cliente WebSocket do runtime", () => {
  it("reconecta com o último cursor aplicado sem reenviar turno", async () => {
    const sockets: FakeSocket[] = [];
    class FakeSocket {
      static OPEN = 1;
      readyState = 0;
      listeners = new Map<string, ((event: any) => void)[]>();
      sent: string[] = [];
      constructor(public url: string) { sockets.push(this); }
      addEventListener(name: string, fn: (event: any) => void) {
        this.listeners.set(name, [...(this.listeners.get(name) || []), fn]);
      }
      emit(name: string, event: any = {}) {
        if (name === "open") this.readyState = 1;
        for (const fn of this.listeners.get(name) || []) fn(event);
      }
      send(value: string) { this.sent.push(value); }
      close() { this.readyState = 3; this.emit("close", { code: 1012 }); }
    }
    let cursor: string | null = null;
    const ticket = vi.fn()
      .mockResolvedValueOnce({ ticket: "one" })
      .mockResolvedValueOnce({ ticket: "two" });
    const client = new RuntimeClient({
      ticket, getCursor: () => cursor, reconnectDelay: () => 0,
      WebSocketImpl: FakeSocket as any,
    });

    await client.connect("runtime-1");
    sockets[0]!.emit("open");
    expect(JSON.parse(sockets[0]!.sent[0]!)).toEqual({
      type: "subscribe", session_id: "runtime-1",
    });
    cursor = "v1:runtime-1:7";
    sockets[0]!.close();
    await new Promise((resolve) => setTimeout(resolve, 5));
    sockets[1]!.emit("open");
    expect(JSON.parse(sockets[1]!.sent[0]!)).toEqual({
      type: "subscribe", session_id: "runtime-1", cursor: "v1:runtime-1:7",
    });
    expect(ticket).toHaveBeenCalledTimes(2);
    client.dispose();
  });

  it("ao sair da tela encerra somente a assinatura e não reconecta", async () => {
    const sockets: Array<{ close: ReturnType<typeof vi.fn> }> = [];
    class DisposableSocket {
      static OPEN = 1;
      readyState = 0;
      listeners = new Map<string, ((event: any) => void)[]>();
      sent: string[] = [];
      close = vi.fn(() => {
        this.readyState = 3;
        for (const fn of this.listeners.get("close") || []) fn({ code: 1000 });
      });
      constructor(_url: string) { sockets.push(this); }
      addEventListener(name: string, fn: (event: any) => void) {
        this.listeners.set(name, [...(this.listeners.get(name) || []), fn]);
      }
      send(value: string) { this.sent.push(value); }
    }
    const ticket = vi.fn().mockResolvedValue({ ticket: "one-use" });
    const client = new RuntimeClient({
      ticket, reconnectDelay: () => 0, WebSocketImpl: DisposableSocket as any,
    });

    await client.connect("runtime-1");
    client.dispose();
    await new Promise((resolve) => setTimeout(resolve, 5));

    expect(sockets).toHaveLength(1);
    expect(sockets[0]!.close).toHaveBeenCalledOnce();
    expect(ticket).toHaveBeenCalledOnce();
  });

  it("a view reconecta a cada salto usando somente o último cursor aplicado", async () => {
    vi.useFakeTimers();
    location.hash = "#/runtime?session=runtime-1";
    vi.spyOn(api, "runtimeStatus").mockResolvedValue({
      enabled: true, state: "ready", authorized_projects: ["/work"],
      sandbox_profiles: ["workspace_write"],
    });
    vi.spyOn(api, "runtimeAccount").mockResolvedValue({ requires_openai_auth: false,
      authenticated: true, auth_mode: "chatgpt", email: null, plan_type: null,
      login_state: "succeeded" });
    vi.spyOn(api, "sessao").mockResolvedValue({ id: "runtime-1", execution_kind: "agent_runtime",
      runtime_kind: "codex", canonical_cwd: "/work", cwd: "/work",
      sandbox: "workspace_write", runtime_state: "ready",
      capabilities: { features: ["text", "snapshot_reconcile"] } });
    const ticket = vi.spyOn(api, "wsTicket")
      .mockResolvedValueOnce({ ticket: "one" })
      .mockResolvedValueOnce({ ticket: "two" })
      .mockResolvedValueOnce({ ticket: "three" });
    class ViewSocket {
      static instances: ViewSocket[] = [];
      readyState = 0;
      sent: string[] = [];
      listeners = new Map<string, ((event: any) => void)[]>();
      close = vi.fn((code = 1000) => {
        this.readyState = 3;
        this.emit("close", { code });
      });
      constructor(_url: string) { ViewSocket.instances.push(this); }
      addEventListener(name: string, fn: (event: any) => void) {
        this.listeners.set(name, [...(this.listeners.get(name) || []), fn]);
      }
      emit(name: string, value: any = {}) {
        if (name === "open") this.readyState = 1;
        for (const fn of this.listeners.get(name) || []) fn(value);
      }
      send(value: string) { this.sent.push(value); }
    }
    vi.stubGlobal("WebSocket", ViewSocket);
    const root = document.createElement("div");
    const cleanup = await runtimeView(root);
    await Promise.resolve();
    ViewSocket.instances[0]!.emit("open");
    ViewSocket.instances[0]!.emit("message", { data: JSON.stringify(event()) });
    ViewSocket.instances[0]!.emit("message", { data: JSON.stringify(event({
      event_id: "event-3", sequence: 3, cursor: "v1:runtime-1:3",
    })) });
    expect(ViewSocket.instances[0]!.close).toHaveBeenCalledWith(1012, "reconcile");
    await vi.advanceTimersByTimeAsync(500);
    ViewSocket.instances[1]!.emit("open");
    expect(JSON.parse(ViewSocket.instances[1]!.sent[0]!)).toEqual({
      type: "subscribe", session_id: "runtime-1", cursor: "v1:runtime-1:1",
    });
    ViewSocket.instances[1]!.emit("message", { data: JSON.stringify(event({
      event_id: "event-2", sequence: 2, cursor: "v1:runtime-1:2",
    })) });
    ViewSocket.instances[1]!.emit("message", { data: JSON.stringify(event({
      event_id: "event-4", sequence: 4, cursor: "v1:runtime-1:4",
    })) });

    expect(ViewSocket.instances[1]!.close).toHaveBeenCalledWith(1012, "reconcile");
    expect(ticket).toHaveBeenCalledTimes(2);
    cleanup();
  });

  it("sair da view enquanto o ticket está pendente não cria socket depois", async () => {
    location.hash = "#/runtime?session=runtime-1";
    vi.spyOn(api, "runtimeStatus").mockResolvedValue({ enabled: true, state: "ready",
      authorized_projects: ["/work"], sandbox_profiles: ["read_only"] });
    vi.spyOn(api, "runtimeAccount").mockResolvedValue({ requires_openai_auth: false,
      authenticated: true, auth_mode: "chatgpt", email: null, plan_type: null,
      login_state: "succeeded" });
    vi.spyOn(api, "sessao").mockResolvedValue({ id: "runtime-1", execution_kind: "agent_runtime",
      runtime_kind: "codex", canonical_cwd: "/work", sandbox: "read_only",
      runtime_state: "ready", capabilities: { features: ["text"] } });
    let finishTicket!: (value: { ticket: string }) => void;
    vi.spyOn(api, "wsTicket").mockImplementation(() => new Promise((resolve) => {
      finishTicket = resolve;
    }));
    const sockets: unknown[] = [];
    vi.stubGlobal("WebSocket", class {
      constructor(_url: string) { sockets.push(this); }
      addEventListener() {}
      close() {}
    });
    const root = document.createElement("div");
    const cleanup = await runtimeView(root);
    cleanup();
    finishTicket({ ticket: "late-ticket" });
    await Promise.resolve();
    await Promise.resolve();

    expect(sockets).toHaveLength(0);
  });
});
