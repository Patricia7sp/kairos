/**
 * Virtualização de histórico por altura MEDIDA.
 *
 * As constantes são resultado de profiling, não escolha arbitrária — e o
 * comentário do legado registra os números:
 *
 *  - OVERSCAN caiu de 40 para 20: 40 era o viewport inteiro, e alturas bem
 *    estimadas não precisam disso. Economiza ~20 itens montados por borda.
 *  - MAX_MOUNTED caiu de 260 para 120 depois que o profiling mostrou ~23 mil
 *    nós Yoga vivos em PageUp sustentado (renderer p99 = 106 ms). A cobertura
 *    necessária é viewport + 2×overscan = 80 linhas ≈ 25 itens a 3 linhas por
 *    item; 120 deixa mais de 4× de folga e nunca esvazia o viewport, mesmo
 *    com itens minúsculos.
 */
export const ESTIMATE = 4;
export const OVERSCAN = 20;
export const MAX_MOUNTED = 120;
export const COLD_START = 30;

export interface Item {
  key: string;
}

export type HeightCache = Map<string, number>;

/** Altura conhecida, ou a estimativa para item ainda não medido. */
export function itemHeight(cache: HeightCache, key: string): number {
  return cache.get(key) ?? ESTIMATE;
}

export function ensureVirtualItemHeight(
  cache: HeightCache,
  key: string,
  measured: number,
): void {
  if (!Number.isFinite(measured) || measured < 0) return;
  cache.set(key, measured);
}

/**
 * Remove do cache as chaves que não estão mais na lista.
 *
 * Sem a poda, o cache cresce monotonicamente numa sessão longa: cada mensagem
 * já rebobinada ou compactada deixa uma entrada que nunca mais é consultada.
 */
export function pruneVirtualHeightCache(cache: HeightCache, items: readonly Item[]): number {
  const vivas = new Set(items.map((i) => i.key));
  let removidas = 0;
  for (const key of [...cache.keys()]) {
    if (!vivas.has(key)) {
      cache.delete(key);
      removidas += 1;
    }
  }
  return removidas;
}

export interface Window {
  start: number;
  end: number;
  mounted: number;
}

/**
 * A janela de itens a montar.
 *
 * Duas garantias que precisam valer ao mesmo tempo, e é a tensão entre elas
 * que define o algoritmo:
 *
 *  1. O span montado tem de COBRIR o viewport, qualquer que seja a altura
 *     real dos itens — senão a tela pisca em branco ao rolar.
 *  2. O total montado nunca passa de MAX_MOUNTED — senão a árvore de fibers
 *     cresce até o renderer perder o frame.
 */
export function computeWindow(
  items: readonly Item[],
  cache: HeightCache,
  opts: { scrollTop: number; viewportRows: number; coldStart?: boolean },
): Window {
  const total = items.length;
  if (total === 0) return { start: 0, end: 0, mounted: 0 };

  const teto = opts.coldStart ? Math.min(COLD_START, MAX_MOUNTED) : MAX_MOUNTED;

  // Encontra o primeiro item visível somando alturas.
  let acumulado = 0;
  let primeiroVisivel = 0;
  for (let i = 0; i < total; i += 1) {
    const h = itemHeight(cache, items[i]!.key);
    if (acumulado + h > opts.scrollTop) {
      primeiroVisivel = i;
      break;
    }
    acumulado += h;
    primeiroVisivel = i + 1;
  }

  const start = Math.max(0, primeiroVisivel - OVERSCAN);

  // Estende até cobrir viewport + overscan de baixo, respeitando o teto.
  let cobertura = 0;
  let end = start;
  const alvo = opts.viewportRows + OVERSCAN * ESTIMATE;
  while (end < total && (cobertura < alvo || end - start < 1)) {
    if (end - start >= teto) break;
    cobertura += itemHeight(cache, items[end]!.key);
    end += 1;
  }

  return { start, end, mounted: end - start };
}

/** O clamp de rolagem: nunca além do fim, nunca antes do começo. */
export function shouldSetVirtualClamp(scrollTop: number, maxScroll: number): number | null {
  if (scrollTop < 0) return 0;
  if (scrollTop > maxScroll) return Math.max(0, maxScroll);
  return null;
}
