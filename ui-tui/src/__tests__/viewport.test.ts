import { describe, expect, it } from "vitest";
import {
  COLD_START,
  ESTIMATE,
  MAX_MOUNTED,
  OVERSCAN,
  computeWindow,
  ensureVirtualItemHeight,
  itemHeight,
  pruneVirtualHeightCache,
  shouldSetVirtualClamp,
  type HeightCache,
  type Item,
} from "../domain/viewport.js";

const itens = (n: number): Item[] =>
  Array.from({ length: n }, (_, i) => ({ key: `k${i}` }));

describe("cache de altura", () => {
  it("usa a estimativa para item não medido", () => {
    expect(itemHeight(new Map(), "x")).toBe(ESTIMATE);
  });

  it("usa a altura medida quando existe", () => {
    const c: HeightCache = new Map();
    ensureVirtualItemHeight(c, "x", 7);
    expect(itemHeight(c, "x")).toBe(7);
  });

  it("ignora medida inválida em vez de corromper o cache", () => {
    const c: HeightCache = new Map();
    ensureVirtualItemHeight(c, "x", NaN);
    ensureVirtualItemHeight(c, "x", -1);
    expect(itemHeight(c, "x")).toBe(ESTIMATE);
  });

  it("poda as chaves que saíram da lista", () => {
    // Sem a poda o cache cresce monotonicamente numa sessão longa: cada
    // mensagem rebobinada ou compactada deixa uma entrada morta.
    const c: HeightCache = new Map([["k0", 3], ["morta", 9]]);
    expect(pruneVirtualHeightCache(c, itens(1))).toBe(1);
    expect(c.has("morta")).toBe(false);
    expect(c.has("k0")).toBe(true);
  });
});

describe("janela virtual", () => {
  it("lista vazia não monta nada", () => {
    expect(computeWindow([], new Map(), { scrollTop: 0, viewportRows: 40 }))
      .toEqual({ start: 0, end: 0, mounted: 0 });
  });

  it("nunca monta mais que MAX_MOUNTED", () => {
    // A garantia que impede a árvore de fibers de crescer até o renderer
    // perder o frame (~23 mil nós Yoga no profiling do legado).
    const w = computeWindow(itens(5000), new Map(), { scrollTop: 0, viewportRows: 40 });
    expect(w.mounted).toBeLessThanOrEqual(MAX_MOUNTED);
  });

  it("o span montado COBRE o viewport mesmo com itens minúsculos", () => {
    // A outra garantia: se não cobrisse, a tela piscaria em branco ao rolar.
    const lista = itens(1000);
    const c: HeightCache = new Map(lista.map((i) => [i.key, 1]));
    const w = computeWindow(lista, c, { scrollTop: 0, viewportRows: 40 });
    let linhas = 0;
    for (let i = w.start; i < w.end; i += 1) linhas += itemHeight(c, lista[i]!.key);
    expect(linhas).toBeGreaterThanOrEqual(40);
  });

  it("aplica overscan antes do primeiro visível", () => {
    const lista = itens(500);
    const c: HeightCache = new Map(lista.map((i) => [i.key, 1]));
    const w = computeWindow(lista, c, { scrollTop: 100, viewportRows: 20 });
    expect(w.start).toBeLessThanOrEqual(100 - OVERSCAN);
    expect(w.start).toBeGreaterThanOrEqual(0);
  });

  it("cold start monta menos", () => {
    const grande = computeWindow(itens(1000), new Map(), { scrollTop: 0, viewportRows: 40 });
    const frio = computeWindow(itens(1000), new Map(), {
      scrollTop: 0, viewportRows: 40, coldStart: true,
    });
    expect(frio.mounted).toBeLessThanOrEqual(COLD_START);
    expect(frio.mounted).toBeLessThanOrEqual(grande.mounted);
  });

  it("as constantes de profiling não regridem", () => {
    // OVERSCAN 40→20 e MAX_MOUNTED 260→120 vieram de medição; um "ajuste"
    // que as devolva ao valor antigo reintroduz o p99 de 106 ms.
    expect(OVERSCAN).toBe(20);
    expect(MAX_MOUNTED).toBe(120);
    expect(ESTIMATE).toBe(4);
    // A folga declarada: 120 é mais de 4× a cobertura necessária (~25 itens).
    expect(MAX_MOUNTED / 25).toBeGreaterThan(4);
  });
});

describe("clamp de rolagem", () => {
  it("prende no começo e no fim, e não mexe no meio", () => {
    expect(shouldSetVirtualClamp(-10, 100)).toBe(0);
    expect(shouldSetVirtualClamp(200, 100)).toBe(100);
    expect(shouldSetVirtualClamp(50, 100)).toBeNull();
  });

  it("maxScroll negativo prende em zero", () => {
    expect(shouldSetVirtualClamp(5, -1)).toBe(0);
  });
});
