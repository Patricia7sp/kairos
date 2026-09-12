// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
// @ts-expect-error Native SPA uses plain JavaScript modules.
import { cronView } from "../../../kairos_web/ui/js/views/cron.js";

type Job = Record<string, any>;
let root: HTMLElement;
let controller: AbortController;
let jobs: Job[];
let writes: Job[];
const response = (body: unknown) => new Response(JSON.stringify(body));
const changeKind = (kind: string) => {
  root.querySelector<HTMLSelectElement>('[name="kind"]')!.value = kind;
  root.querySelector('[name="kind"]')!.dispatchEvent(new Event("change"));
};
const submit = () => root.querySelector("form")!.dispatchEvent(new Event("submit", { cancelable: true }));
const mount = async () => {
  await cronView(root, {}, { signal: controller.signal });
  root.querySelector<HTMLInputElement>('[name="name"]')!.value = "Rotina";
  root.querySelector<HTMLTextAreaElement>('[name="prompt"]')!.value = "Faça o resumo";
};
beforeEach(() => {
  root = document.createElement("main");
  document.body.replaceChildren(root);
  controller = new AbortController(); jobs = []; writes = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    if (url.endsWith("/status")) return response({ running: true, last_tick: null });
    if (init.method === "POST") {
      const body = JSON.parse(String(init.body)); writes.push(body);
      return response({ ...body, id: "new-job", enabled: true, paused: false,
        repeat: { times: body.times ?? null, completed: 0 }, next_run_at: "2026-09-12T18:00:00Z" });
    }
    if (url.endsWith("/history")) return response({ executions: [
      { id: "failed", status: "failed", conversation_id: "failed-turn", claimed_at: "2026-09-12T16:00:00Z" },
      { id: "unknown", status: "unknown", conversation_id: "unknown-turn", claimed_at: "2026-09-12T15:00:00Z" },
    ] });
    return response({ jobs });
  });
});
afterEach(() => { controller.abort(); vi.unstubAllGlobals(); });

it.each(["interval", "cron"])("sends an optional finite limit for %s schedules", async kind => {
  await mount();
  changeKind(kind);
  const times = root.querySelector<HTMLInputElement>('[name="times"]');
  expect(times).not.toBeNull();
  expect(times!.disabled).toBe(false);
  expect(times!.required).toBe(false);
  times!.value = "2";
  if (kind === "cron") root.querySelector<HTMLInputElement>('[name="expr"]')!.value = "0 9 * * *";
  submit();
  await vi.waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toEqual({ name: "Rotina", prompt: "Faça o resumo", times: 2,
    schedule: kind === "interval" ? { kind: "interval", minutes: 60 } : { kind: "cron", expr: "0 9 * * *" } });
});

it("keeps a blank recurring limit unlimited and ignores a hidden limit for a once schedule", async () => {
  await mount();
  const times = root.querySelector<HTMLInputElement>('[name="times"]');
  expect(times).not.toBeNull();
  expect(times!.disabled).toBe(true);
  expect(times!.closest("[data-timing]")!.hasAttribute("hidden")).toBe(true);
  changeKind("interval");
  expect(times!.value).toBe("");
  submit();
  await vi.waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).not.toHaveProperty("times");
  await vi.waitFor(() => expect(root.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(false));
  times!.value = "1000001";
  changeKind("once");
  expect(times!.disabled).toBe(true);
  expect(times!.required).toBe(false);
  root.querySelector<HTMLInputElement>('[name="run_at"]')!.value = "2026-09-12T18:00";
  submit();
  await vi.waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1]).not.toHaveProperty("times");
  expect(writes[1]!.schedule.kind).toBe("once");
});

it.each(["0", "-1", "1.5", "1000001"])("rejects invalid occurrence limit %s before sending", async value => {
  await mount(); changeKind("interval");
  const times = root.querySelector<HTMLInputElement>('[name="times"]');
  expect(times).not.toBeNull();
  times!.value = value;
  submit();
  expect(times!.validity.valid).toBe(false);
  expect(writes).toHaveLength(0);
});

it.each(["1", "1000000"])("accepts occurrence limit boundary %s", async value => {
  await mount(); changeKind("interval");
  const times = root.querySelector<HTMLInputElement>('[name="times"]');
  expect(times).not.toBeNull();
  times!.value = value;
  submit();
  await vi.waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.times).toBe(Number(value));
});

it("shows reserved occurrences including failed and unknown runs, and exhausted schedules cannot resume", async () => {
  jobs = [
    { id: "exhausted", name: "Finita", prompt: "Resumo", schedule: { kind: "interval", minutes: 15 },
      repeat: { times: 2, completed: 2 }, enabled: true, paused: true, next_run_at: null },
    { id: "unlimited", name: "Ilimitada", prompt: "Resumo", schedule: { kind: "cron", expr: "0 9 * * *" },
      repeat: { times: null, completed: 3 }, enabled: true, paused: false, next_run_at: "2026-09-13T09:00:00Z" },
  ];
  await mount();
  const finite = root.querySelector('[data-job="exhausted"]')!;
  expect(finite.textContent).toContain("Ocorrências reservadas: 2 de 2");
  expect(finite.textContent).toContain("Limite de ocorrências atingido");
  expect(finite.querySelector("[data-pause]")).toBeNull();
  expect(root.querySelector('[data-job="unlimited"]')!.textContent).toContain("Ocorrências reservadas: 3 · Sem limite");
  changeKind("interval");
  expect(root.textContent).toContain("Falhas e resultados desconhecidos também consomem uma ocorrência");
  finite.querySelector<HTMLButtonElement>('[data-history]')!.click();
  await vi.waitFor(() => expect(root.querySelector('[data-job-history]')!.textContent).toContain("Resultado desconhecido"));
  expect(root.querySelector('[data-job-history]')!.textContent).toContain("Falhou");
  expect(finite.textContent).toContain("Ocorrências reservadas: 2 de 2");
});
