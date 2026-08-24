import { describe, expect, it } from "vitest";
import { CircularBuffer } from "../lib/circularBuffer.js";

describe("CircularBuffer", () => {
  it("recusa capacidade inválida com RangeError", () => {
    // Silêncio aqui viraria um buffer que descarta tudo ou cresce sem limite,
    // e os dois falham longe da causa.
    for (const ruim of [0, -1, 1.5, NaN]) {
      expect(() => new CircularBuffer<number>(ruim)).toThrow(RangeError);
    }
  });

  it("guarda até a capacidade", () => {
    const b = new CircularBuffer<number>(3);
    b.push(1); b.push(2);
    expect(b.length).toBe(2);
    expect(b.toArray()).toEqual([1, 2]);
  });

  it("descarta o mais antigo ao passar da capacidade", () => {
    const b = new CircularBuffer<number>(3);
    for (const n of [1, 2, 3, 4, 5]) b.push(n);
    expect(b.length).toBe(3);
    expect(b.toArray()).toEqual([3, 4, 5]);
  });

  it("indexa a partir do mais antigo vivo", () => {
    const b = new CircularBuffer<string>(2);
    b.push("a"); b.push("b"); b.push("c");
    expect(b.at(0)).toBe("b");
    expect(b.at(1)).toBe("c");
    expect(b.at(2)).toBeUndefined();
    expect(b.at(-1)).toBeUndefined();
  });

  it("limpa sem perder a capacidade", () => {
    const b = new CircularBuffer<number>(2);
    b.push(1); b.clear();
    expect(b.length).toBe(0);
    expect(b.capacity).toBe(2);
  });
});
