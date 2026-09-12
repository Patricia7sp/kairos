// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error Production views use direct JavaScript modules.
import { ferramentasView } from "../../../kairos_web/ui/js/views/ferramentas.js";
// @ts-expect-error Production API uses a direct JavaScript module.
import { api } from "../../../kairos_web/ui/js/api.js";

const inventory = {
  total: 2, available: 1,
  toolsets: [
    { name: "core", enabled: true, tools: ["read_file"], aliases: [] },
    { name: "remote", enabled: false, tools: ["remote_read"], aliases: [] },
  ],
  tools: [
    {
      name: "read_file", toolset: "core", available: true, description: "Read local text",
      schema: { type: "function", function: { name: "read_file", parameters: {
        type: "object", required: ["path"], properties: {
          path: { type: "string", description: "File path" },
          offset: { type: "integer", description: "Starting line", default: 0 },
        },
      } } },
    },
    {
      name: "remote_read", toolset: "remote", available: false,
      description: "Remote <script>bad()</script> resource",
      schema: { type: "function", function: { name: "remote_read", parameters: {
        type: "object", properties: {},
      } } },
    },
  ],
};
let root: HTMLElement;
let controller: AbortController;
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const mount = async () => {
  await ferramentasView(root, {}, { signal: controller.signal });
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
  vi.spyOn(api, "toolsets").mockResolvedValue(inventory);
});
afterEach(() => { controller.abort(); vi.restoreAllMocks(); });

describe("registered tools", () => {
  it("shows actual availability, required parameters and the execution scope", async () => {
    await mount();
    expect(root.querySelectorAll("[data-tool]")).toHaveLength(2);
    expect(root.querySelector('[data-tool="remote_read"]')?.textContent).toContain("Indisponível");
    const local = root.querySelector('[data-tool="read_file"]')!;
    expect(local.textContent).toContain("path");
    expect(local.textContent).toContain("obrigatório");
    expect(local.textContent).toContain("Starting line");
    expect(root.textContent).toContain("CLI");
    expect(root.textContent).toContain("Chat");
    expect(root.textContent).toContain("automaticamente");
    expect(root.querySelector("script")).toBeNull();
    expect(root.textContent).toContain("<script>bad()</script>");
  });

  it("combines search and toolset filters and recovers from an empty search", async () => {
    await mount();
    change("[data-tools-search]", "remote", "input");
    expect(root.querySelectorAll("[data-tool]")).toHaveLength(1);
    expect(root.querySelector("[data-tool]")?.getAttribute("data-tool")).toBe("remote_read");
    change("[data-tools-group]", "core", "change");
    expect(root.querySelectorAll("[data-tool]")).toHaveLength(0);
    expect(root.textContent).toContain("Nenhuma ferramenta encontrada");
    change("[data-tools-search]", "", "input");
    expect(root.querySelector("[data-tool]")?.getAttribute("data-tool")).toBe("read_file");
  });

  it("offers retry after an inventory error and refreshes from the current registry", async () => {
    api.toolsets.mockRejectedValueOnce(new Error("Unavailable"));
    await mount();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("Unavailable");
    root.querySelector<HTMLButtonElement>("[data-tools-refresh]")!.click();
    await flush();
    expect(root.querySelectorAll("[data-tool]")).toHaveLength(2);
    expect(root.querySelector('[role="alert"]')).toBeNull();
  });

  it("does not restore the inventory after navigation disposes the view", async () => {
    let resolve!: (data: typeof inventory) => void;
    api.toolsets.mockReturnValue(new Promise((done) => { resolve = done; }));
    await mount();
    controller.abort();
    const content = root.querySelector("[data-tools-content]")!;
    content.textContent = "Next view";
    resolve(inventory);
    await flush();
    expect(content.textContent).toBe("Next view");
  });
});
