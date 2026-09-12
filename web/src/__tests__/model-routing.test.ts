// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error Native SPA module.
import { modelosView } from "../../../kairos_web/ui/js/views/modelos.js";

type Obj = Record<string, any>;
const model = { provider: "openrouter", id: "vendor/model", name: "Vendor", stability: "stable" };
const defaults = { temperature: 0.3, routing: { data_collection: "deny", require_parameters: true, allow_fallbacks: true } };
const profile = { name: "private", provider: model.provider, model: model.id,
  parameters: { max_tokens: 123, routing: { data_collection: "deny", require_parameters: false, allow_fallbacks: false } } };
const response = (body: Obj, status = 200) => new Response(JSON.stringify(body), { status,
  headers: { "Content-Type": "application/json" } });
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { resolve, promise };
};
function backend({ detail, save, parameters = defaults, profiles = [profile] }: {
  detail?: Response | Promise<Response>; save?: Promise<Response>; parameters?: Obj; profiles?: Obj[] } = {}) {
  const writes: Obj[] = [];
  vi.stubGlobal("fetch", async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path === "/api/models") return response({ default_provider: model.provider, default_model: model.id,
      default_parameters: parameters, profiles, models: [model, { ...model, id: "other", name: "Other" }] });
    if (path === "/api/providers") return response({ providers: [] });
    if (path === "/api/sessions/saved") return detail || response({ selection: {
      provider: model.provider, model: model.id, parameters: { temperature: 0.8, max_tokens: 777,
        routing: { data_collection: "allow", require_parameters: false, allow_fallbacks: false } },
    } });
    if (path === "/api/models/selection") {
      writes.push(JSON.parse(String(init?.body)));
      return save || response({ status: "updated", selection: writes.at(-1) });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  return writes;
}
let cleanup: (() => void) | undefined;
async function mount() {
  const root = document.createElement("main");
  document.body.append(root);
  cleanup = await modelosView(root, {});
  root.querySelector<HTMLButtonElement>(`[data-choose-model="openrouter/vendor/model"]`)!.click();
  return root;
}
const click = (root: HTMLElement, selector: string) => root.querySelector<HTMLButtonElement>(selector)!.click();
const field = (root: HTMLElement, name: string) => root.querySelector<HTMLInputElement>(`[name="${name}"]`)!;
beforeEach(() => {
  document.body.innerHTML = "";
  sessionStorage.clear();
  location.hash = "#/modelos";
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value() { this.open = false; } });
});
afterEach(() => { cleanup?.(); cleanup = undefined; vi.unstubAllGlobals(); });

describe("model routing controls", () => {
  it("inherits nested routing in profiles and drafts without replacing sibling settings", async () => {
    location.hash = "#/modelos?new=1&session=draft&provider=openrouter&model=other&profile=partial";
    sessionStorage.setItem("kairos.chat.draft.draft", JSON.stringify({ provider: "openrouter", model: "other",
      profile: "partial", parameters: { routing: { allow_fallbacks: true } } }));
    backend({ parameters: { temperature: 0.7, routing: { data_collection: "allow", require_parameters: false, allow_fallbacks: false } },
      profiles: [{ ...profile, model: "other", name: "partial", parameters: { routing: { require_parameters: true } } }],
    });
    const root = await mount();
    click(root, '[data-choose-model="openrouter/other"]');
    expect(field(root, "data_collection").value).toBe("allow");
    expect(field(root, "require_parameters").checked).toBe(true);
    click(root, "[data-apply-model]");
    expect(JSON.parse(sessionStorage.getItem("kairos.chat.draft.draft")!).parameters).toEqual({
      temperature: 0.7, routing: { data_collection: "allow", require_parameters: true, allow_fallbacks: true },
    });
  });

  it("allows choosing a model for a legacy conversation whose selection is null", async () => {
    location.hash = "#/modelos?session=saved";
    const writes = backend({ detail: response({ selection: null }) });
    const root = await mount();
    await vi.waitFor(() => expect(root.querySelector<HTMLButtonElement>("[data-apply-model]")!.disabled).toBe(false));
    click(root, "[data-apply-model]");
    await vi.waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({ scope: "conversation", profile: "", session_id: "saved", model: "vendor/model" });
  });

  it("preserves generation parameters while applying an explicit data collection change", async () => {
    const writes = backend();
    const root = await mount();
    expect(root.querySelector("details")?.textContent).toContain("Configurações avançadas");
    expect(field(root, "data_collection")?.value).toBe("deny");
    field(root, "data_collection").value = "allow";
    field(root, "allow_fallbacks").checked = false;
    click(root, "[data-apply-model]");
    await vi.waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({ scope: "global", provider: "openrouter", model: "vendor/model",
      parameters: { temperature: 0.3, routing: { data_collection: "allow", require_parameters: true, allow_fallbacks: false } } });
  });

  it("loads conversation parameters before allowing writes, preserving them when changing models", async () => {
    location.hash = "#/modelos?session=saved";
    const pending = deferred<Response>();
    const writes = backend({ detail: pending.promise });
    const root = await mount();
    expect(root.querySelector<HTMLButtonElement>("[data-apply-model]")!.disabled).toBe(true);
    pending.resolve(response({ selection: { provider: "openrouter", model: "previous", parameters: {
      temperature: 0.8, max_tokens: 777, routing: { data_collection: "allow", require_parameters: false, allow_fallbacks: false },
    } } }));
    await vi.waitFor(() => expect(field(root, "data_collection")?.value).toBe("allow"));
    click(root, "[data-apply-model]");
    await vi.waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({ scope: "conversation", session_id: "saved", model: "vendor/model",
      parameters: { temperature: 0.8, max_tokens: 777,
        routing: { data_collection: "allow", require_parameters: false, allow_fallbacks: false } } });
  });

  it("keeps actions blocked after failed conversation loading and permits retry", async () => {
    location.hash = "#/modelos?session=saved";
    const writes = backend({ detail: response({ error: "unavailable" }, 503) });
    const root = await mount();
    await vi.waitFor(() => expect(root.querySelector("[data-selection-status]")!.textContent).toMatch(/503|unavailable/));
    click(root, "[data-apply-model]");
    expect(writes).toHaveLength(0);
    expect(root.querySelector<HTMLButtonElement>("[data-new-conversation]")!.disabled).toBe(true);
    expect(root.querySelector("[data-retry-selection]")).not.toBeNull();
  });

  it("saves a named profile without changing the global selection and starts a draft with it", async () => {
    const writes = backend();
    const root = await mount();
    expect(field(root, "profile")?.tagName).toBe("SELECT");
    field(root, "profile").value = "private";
    field(root, "profile").dispatchEvent(new Event("change"));
    expect(field(root, "allow_fallbacks").checked).toBe(false);
    field(root, "profile_name").value = "team";
    field(root, "profile_name").dispatchEvent(new Event("input"));
    click(root, "[data-save-profile]");
    await vi.waitFor(() => expect(root.querySelector("[data-selection-status]")!.textContent).toContain("Perfil salvo"));
    expect(writes).toEqual([{ provider: "openrouter", model: "vendor/model", scope: "profile", profile: "team",
      parameters: { temperature: 0.3, max_tokens: 123,
        routing: { data_collection: "deny", require_parameters: false, allow_fallbacks: false } } }]);
    expect(root.querySelector("[data-default-selection]")!.textContent).toBe("openrouter/vendor/model");
    click(root, "[data-new-conversation]");
    const params = new URLSearchParams(location.hash.split("?")[1]);
    expect(params.get("profile")).toBe("team");
    const draft = JSON.parse(sessionStorage.getItem(`kairos.chat.draft.${params.get("session")}`)!);
    expect(draft).toMatchObject({ profile: "team", parameters: { max_tokens: 123, temperature: 0.3 } });
  });

  it("restores and updates routing in an existing draft without writing server selection", async () => {
    location.hash = "#/modelos?new=1&session=draft&provider=openrouter&model=vendor%2Fmodel&profile=private";
    sessionStorage.setItem("kairos.chat.draft.draft", JSON.stringify({ provider: "openrouter", model: "vendor/model",
      profile: "private", parameters: { temperature: 0.9, routing: { data_collection: "allow", require_parameters: false, allow_fallbacks: false } } }));
    const writes = backend();
    const root = await mount();
    expect(field(root, "data_collection")?.value).toBe("allow");
    field(root, "require_parameters").checked = true;
    click(root, "[data-apply-model]");
    expect(location.hash).toContain("session=draft");
    expect(writes).toHaveLength(0);
    expect(JSON.parse(sessionStorage.getItem("kairos.chat.draft.draft")!)).toMatchObject({ profile: "private",
      parameters: { temperature: 0.9, routing: { data_collection: "allow", require_parameters: true, allow_fallbacks: false } } });
  });

  it("prevents duplicate saves while the first save is pending", async () => {
    const pending = deferred<Response>();
    const writes = backend({ save: pending.promise });
    const root = await mount();
    click(root, "[data-apply-model]");
    click(root, "[data-apply-model]");
    expect(writes).toHaveLength(1);
    expect(root.querySelector<HTMLButtonElement>("[data-new-conversation]")!.disabled).toBe(true);
    pending.resolve(response({ status: "updated" }));
    await vi.waitFor(() => expect(root.querySelector<HTMLButtonElement>("[data-apply-model]")!.disabled).toBe(false));
  });

  it("retains draft generation settings when choosing another model without carrying its profile", async () => {
    location.hash = "#/modelos?new=1&session=draft&provider=openrouter&model=vendor%2Fmodel&profile=private";
    sessionStorage.setItem("kairos.chat.draft.draft", JSON.stringify({ provider: "openrouter", model: "vendor/model",
      profile: "private", parameters: { temperature: 0.9, max_tokens: 450, routing: { data_collection: "allow" } } }));
    backend();
    const root = await mount();
    click(root, '[data-choose-model="openrouter/other"]');
    click(root, "[data-apply-model]");
    const draft = JSON.parse(sessionStorage.getItem("kairos.chat.draft.draft")!);
    expect(draft).toMatchObject({ model: "other", parameters: { temperature: 0.9, max_tokens: 450 } });
    expect(draft).not.toHaveProperty("profile");
  });

  it("ignores a pending session response when a newer dialog replaced it", async () => {
    location.hash = "#/modelos?session=saved";
    const pending = deferred<Response>();
    backend({ detail: pending.promise });
    const root = await mount();
    click(root, '[data-choose-model="openrouter/other"]');
    cleanup?.();
    root.innerHTML = "Outra rota";
    pending.resolve(response({ selection: { parameters: defaults } }));
    await Promise.resolve();
    await Promise.resolve();
    expect(root.textContent).toBe("Outra rota");
  });
});
