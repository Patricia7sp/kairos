// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error The production SPA uses JavaScript modules directly.
import { sessoesView } from "../../../kairos_web/ui/js/views/sessoes.js";
// @ts-expect-error The production SPA uses JavaScript modules directly.
import { api } from "../../../kairos_web/ui/js/api.js";

const session = (id: string, extra = {}) => ({
  id, title: `Session ${id}`, source: "web", execution_kind: "model",
  tags: [], started_at: 1, status: "aberta", ...extra,
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };
let root: HTMLElement;
let controller: AbortController;
const click = (selector: string) => root.querySelector<HTMLElement>(selector)!.click();
const mount = async () => {
  await sessoesView(root, {}, { signal: controller.signal });
  await flush();
};

beforeEach(() => {
  root = document.createElement("main");
  document.body.replaceChildren(root);
  controller = new AbortController();
  vi.spyOn(api, "sessoes").mockImplementation(async ({ offset = 0 }) => ({
    sessions: [session("a"), session("b")], offset, limit: 25,
    total: 60, has_more: offset < 50, tag_counts: [],
  }));
  vi.spyOn(api, "sessao").mockImplementation(async (id: string) => session(id));
  vi.spyOn(api, "mensagens").mockResolvedValue({ messages: [] });
  vi.spyOn(api, "atualizarSessao").mockResolvedValue({});
});

afterEach(() => {
  controller.abort();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("session library", () => {
  it("keeps the selected detail and metadata actions when an older selection finishes late", async () => {
    const old = deferred<ReturnType<typeof session>>();
    api.sessao.mockImplementation((id: string) => id === "a" ? old.promise : Promise.resolve(session(id)));
    await mount();
    click('[data-sessao="a"]');
    click('[data-sessao="b"]');
    await flush();
    old.resolve(session("a"));
    await flush();
    expect(root.querySelector("[data-detalhe] h3")?.textContent).toBe("Session b");
    click("[data-arquivar-sessao]");
    await flush();
    expect(api.atualizarSessao).toHaveBeenCalledWith("b", { archived: true });
  });

  it("ignores an older selection error after another session opens", async () => {
    const old = deferred<ReturnType<typeof session>>();
    api.sessao.mockImplementation((id: string) => id === "a" ? old.promise : Promise.resolve(session(id)));
    await mount();
    click('[data-sessao="a"]');
    click('[data-sessao="b"]');
    await flush();
    old.reject(new Error("Old failure"));
    await flush();
    expect(root.querySelector("[data-detalhe] h3")?.textContent).toBe("Session b");
    expect(root.textContent).not.toContain("Old failure");
  });

  it("preserves complete escaped message content in the transcript", async () => {
    const content = "<example>&".repeat(900) + "THE END";
    api.mensagens.mockResolvedValue({ messages: [{ role: "assistant", content }] });
    await mount();
    click('[data-sessao="a"]');
    await flush();
    expect(root.querySelector(".k-msg__corpo")?.textContent).toBe(content);
    expect(root.querySelector(".k-msg__corpo example")).toBeNull();
  });

  it.each([{ archived: true }, { hidden: true }])("links explicitly selected model history to Chat: %o", async (flags) => {
    api.sessao.mockResolvedValue(session("a", flags));
    await mount();
    click('[data-sessao="a"]');
    await flush();
    expect(root.querySelector('a[href="#/chat?session=a"]')?.textContent).toContain("chat");
  });

  it("retains runtime navigation for runtime sessions", async () => {
    api.sessao.mockResolvedValue(session("a", { execution_kind: "agent_runtime" }));
    await mount();
    click('[data-sessao="a"]');
    await flush();
    expect(root.querySelector('a[href="#/runtime?session=a"]')).not.toBeNull();
    expect(root.querySelector('a[href="#/chat?session=a"]')).toBeNull();
  });

  it("exports complete tool content with its identity in JSON and Markdown", async () => {
    const content = "output <&>\n".repeat(500) + "Final output";
    api.mensagens.mockResolvedValue({ messages: [{ role: "tool", tool_name: "shell", content }] });
    const exports: Array<{ filename: string; blob: Blob }> = [];
    let nextBlob: Blob;
    vi.stubGlobal("URL", {
      createObjectURL: (blob: Blob) => { nextBlob = blob; return "blob:session-export"; },
      revokeObjectURL: () => {},
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      exports.push({ filename: this.download, blob: nextBlob });
    });
    await mount();
    click('[data-sessao="a"]');
    await flush();
    click("[data-exportar-sessao]");
    click("[data-exportar-markdown]");
    const readBlob = (blob: Blob) => new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
    const json = JSON.parse(await readBlob(exports[0]!.blob));
    const markdown = await readBlob(exports[1]!.blob);
    expect(exports.map((item) => item.filename)).toEqual(["kairos-session-a.json", "kairos-session-a.md"]);
    expect(json.messages).toEqual([{ role: "tool", tool_name: "shell", content }]);
    expect(markdown).toContain(content);
    expect(markdown).toContain("shell");
  });

  it("removes pagination listeners when the same root mounts again", async () => {
    await mount();
    controller.abort();
    controller = new AbortController();
    await mount();
    api.sessoes.mockClear();
    click("[data-pagina-proxima]");
    await flush();
    expect(api.sessoes).toHaveBeenCalledTimes(1);
    expect(root.querySelector(".k-ses__paginacao")?.textContent).toContain("26–27 de 60");
  });

  it("cancels pending search debounce when leaving the view", async () => {
    vi.useFakeTimers();
    await mount();
    const search = root.querySelector<HTMLInputElement>("[data-busca-sessoes]")!;
    search.value = "old search";
    search.dispatchEvent(new Event("input"));
    controller.abort();
    api.sessoes.mockClear();
    await vi.advanceTimersByTimeAsync(300);
    expect(api.sessoes).not.toHaveBeenCalled();
  });

  it("ignores detail responses after disposal", async () => {
    const pending = deferred<ReturnType<typeof session>>();
    api.sessao.mockReturnValue(pending.promise);
    await mount();
    click('[data-sessao="a"]');
    controller.abort();
    const detail = root.querySelector("[data-detalhe]")!;
    detail.textContent = "view disposed";
    pending.resolve(session("a"));
    await flush();
    expect(detail.textContent).toBe("view disposed");
  });

  it("returns to the last valid page after hiding its only remaining session", async () => {
    let hidden = false;
    api.sessoes.mockImplementation(async ({ offset = 0 }) => ({
      sessions: offset === 0 ? [session("a")] : hidden ? [] : [session("b")],
      offset, limit: 25, total: hidden ? 25 : 26, has_more: offset === 0 && !hidden, tag_counts: [],
    }));
    api.atualizarSessao.mockImplementation(async () => { hidden = true; return {}; });
    await mount();
    click("[data-pagina-proxima]");
    await flush();
    click('[data-sessao="b"]');
    await flush();
    click("[data-ocultar-sessao]");
    await flush();
    expect(root.querySelector('[data-sessao="a"]')).not.toBeNull();
    expect(root.querySelector(".k-ses__paginacao")?.textContent).not.toContain("26–25");
  });
});
