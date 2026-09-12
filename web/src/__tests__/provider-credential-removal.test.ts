// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
// @ts-expect-error Native SPA JavaScript.
import { provedoresView } from "../../../kairos_web/ui/js/views/provedores.js";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const flush = async () => { await new Promise((resolve) => setTimeout(resolve, 0)); };
const provider = { id: "openai", name: "OpenAI", requires_credential: true,
  configured: true, credential_state: "configured", credential_source: "vault", can_remove_credential: true };

beforeEach(() => vi.stubGlobal("CSS", { escape: (value: string) => value }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("confirms removal, serializes mutations, ignores stale probes and reloads actual remaining credentials", async () => {
  let finishDelete!: () => void;
  let finishProbe!: () => void;
  let finishRefresh!: () => void;
  let deleted = false;
  let deletes = 0;
  let reads = 0;
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/providers") {
      reads++;
      return json({ providers: [{ ...provider, can_remove_credential: !deleted }] });
    }
    if (path.endsWith("/test")) {
      await new Promise<void>((resolve) => { finishProbe = resolve; });
      return json({ connected: true, message: "STALE PROBE" });
    }
    if (path === "/api/models/refresh") {
      await new Promise<void>((resolve) => { finishRefresh = resolve; });
      return json({ models: [1], source: "STALE REFRESH" });
    }
    if (path.endsWith("/credentials")) {
      expect(init?.method).toBe("DELETE");
      deletes++;
      await new Promise<void>((resolve) => { finishDelete = resolve; });
      deleted = true;
      return json({ removed: true });
    }
    throw new Error(path);
  }));
  const root = document.createElement("main");
  const cleanup = await provedoresView(root, {}, {});
  const remove = root.querySelector<HTMLButtonElement>("[data-remove-credential]")!;
  expect(remove).not.toBeNull();
  remove.click();
  expect(deletes).toBe(0);
  const test = root.querySelector<HTMLButtonElement>("[data-test-provider]")!;
  const refresh = root.querySelector<HTMLButtonElement>("[data-refresh-provider]")!;
  test.click(); refresh.click();
  confirm.mockReturnValue(true);
  remove.click();
  await flush();
  expect(remove.disabled).toBe(true);
  expect(test.disabled).toBe(true);
  expect(refresh.disabled).toBe(true);
  root.querySelector("form")!.dispatchEvent(new Event("submit", { cancelable: true }));
  remove.click();
  expect(deletes).toBe(1);
  finishProbe(); finishRefresh(); await flush();
  expect(root.textContent).not.toContain("STALE");
  finishDelete(); await flush(); await flush();
  expect(reads).toBe(2);
  expect(root.textContent).toContain("Credencial configurada");
  expect(root.textContent).toContain("Credencial removida do cofre");
  expect(remove.hidden).toBe(true);
  expect(test.disabled).toBe(false);
  cleanup();
});

it("does not offer deletion of a read-only external credential", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ providers: [{ ...provider,
    credential_source: "external", can_remove_credential: false }] })));
  const root = document.createElement("main");
  const cleanup = await provedoresView(root, {}, {});
  expect(root.textContent).toContain("externa");
  expect(root.querySelector<HTMLButtonElement>("[data-remove-credential]")!.hidden).toBe(true);
  cleanup();
});
