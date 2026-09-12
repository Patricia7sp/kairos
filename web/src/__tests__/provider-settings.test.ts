// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
// @ts-expect-error The shipped SPA uses plain JavaScript.
import { provedoresView } from "../../../kairos_web/ui/js/views/provedores.js";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const flush = async () => { await new Promise((resolve) => setTimeout(resolve, 0)); };
const provider = { id: "custom", name: "Personalizado", provider: "custom", configured: true,
  requires_credential: true, credential_state: "configured", auth_methods: ["api_key"] };
const settings = { base_url: "http://127.0.0.1:8000/v1", models_url: "http://127.0.0.1:8000/v1/models",
  headers: {}, trusted_remote: false };
let saved: any[];
let finishTest: (value: Response) => void;

beforeEach(() => {
  saved = [];
  vi.stubGlobal("CSS", { escape: (value: string) => value });
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit) => {
    if (input === "/api/providers") return json({ providers: [provider] });
    if (input === "/api/providers/custom/test") return new Promise<Response>((resolve) => { finishTest = resolve; });
    if (input === "/api/providers/custom/settings") {
      if (init.method === "PUT") {
        const body = JSON.parse(String(init.body));
        saved.push(body);
        return json({ provider: "custom", settings: body.settings });
      }
      return json({ provider: "custom", settings });
    }
    throw new Error(`Unexpected request: ${input}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());

async function openSettings() {
  const root = document.createElement("main");
  const dispose = await provedoresView(root, {});
  const edit = root.querySelector<HTMLButtonElement>("[data-edit-provider-settings]");
  expect(edit).not.toBeNull();
  edit!.click();
  await flush();
  return { root, dispose, form: root.querySelector<HTMLFormElement>("[data-provider-settings-form]")! };
}

it("requires explicit credential destination consent and saves only attribution and endpoint fields", async () => {
  const { root, form, dispose } = await openSettings();
  const base = form.elements.namedItem("base_url") as HTMLInputElement;
  expect(base.value).toBe("http://127.0.0.1:8000/v1");
  base.value = "https://private.example/v1";
  base.dispatchEvent(new Event("input", { bubbles: true }));
  (form.elements.namedItem("trusted_remote") as HTMLInputElement).checked = true;
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await flush();
  expect(saved).toHaveLength(0);
  expect(root.textContent).toContain("Confirme");
  (form.elements.namedItem("confirm_transfer") as HTMLInputElement).checked = true;
  (form.elements.namedItem("title") as HTMLInputElement).value = "Meu projeto";
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await flush();
  expect(saved).toEqual([{ settings: {
    base_url: "https://private.example/v1", models_url: "https://private.example/v1/models",
    trusted_remote: true, headers: { "X-Title": "Meu projeto" },
  }, confirm_credential_transfer: true }]);
  expect(root.textContent).toContain("Configurações salvas");
  expect((form.elements.namedItem("confirm_transfer") as HTMLInputElement).checked).toBe(false);
  dispose();
});

it("a probe from before the settings save cannot mark the changed endpoint as verified", async () => {
  const { root, form, dispose } = await openSettings();
  root.querySelector<HTMLButtonElement>("[data-test-provider]")!.click();
  (form.elements.namedItem("title") as HTMLInputElement).value = "Outro título";
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await flush();
  finishTest(json({ connected: true, message: "Old endpoint connected" }));
  await flush();
  expect(root.querySelector("[data-provider-connection]")!.textContent).toContain("Conexão não testada");
  expect(root.textContent).not.toContain("Old endpoint connected");
  dispose();
});

it("an aborted view ignores a late settings load", async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(async (input: string) => input === "/api/providers"
    ? json({ providers: [provider] }) : new Promise<Response>((resolve) => { finish = resolve; })));
  const { root, dispose, form } = await openSettings();
  const before = form.innerHTML;
  dispose();
  finish(json({ provider: "custom", settings }));
  await flush();
  expect(form.innerHTML).toBe(before);
  expect(root.textContent).not.toContain("Falha ao carregar");
});
