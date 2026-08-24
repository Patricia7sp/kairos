/**
 * Buffer circular tipado.
 *
 * Limita quanto histórico EXISTE. É camada distinta da virtualização, que
 * limita quanto está MONTADO na árvore de fibers — as duas são necessárias e
 * confundi-las leva a resolver metade do problema.
 */
export class CircularBuffer<T> {
  readonly #capacity: number;
  #items: T[] = [];
  #head = 0;
  #len = 0;

  constructor(capacity: number) {
    if (!Number.isInteger(capacity) || capacity <= 0) {
      // RangeError e não silêncio: uma capacidade inválida vira um buffer que
      // descarta tudo ou cresce sem limite, e os dois falham longe da causa.
      throw new RangeError(`capacidade deve ser inteiro positivo, recebido: ${capacity}`);
    }
    this.#capacity = capacity;
    this.#items = new Array<T>(capacity);
  }

  get capacity(): number {
    return this.#capacity;
  }

  get length(): number {
    return this.#len;
  }

  push(item: T): void {
    const index = (this.#head + this.#len) % this.#capacity;
    this.#items[index] = item;
    if (this.#len < this.#capacity) {
      this.#len += 1;
    } else {
      this.#head = (this.#head + 1) % this.#capacity;
    }
  }

  at(i: number): T | undefined {
    if (i < 0 || i >= this.#len) return undefined;
    return this.#items[(this.#head + i) % this.#capacity];
  }

  toArray(): T[] {
    const out: T[] = [];
    for (let i = 0; i < this.#len; i += 1) {
      out.push(this.#items[(this.#head + i) % this.#capacity] as T);
    }
    return out;
  }

  clear(): void {
    this.#head = 0;
    this.#len = 0;
  }
}
