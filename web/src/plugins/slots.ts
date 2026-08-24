/**
 * Slots de plugin — 30 pontos de injeção visual.
 *
 * Permitem que um plugin acrescente abas, botões e widgets **sem modificar a
 * casca da SPA**. Um slot desconhecido é rejeitado no registro, e não
 * ignorado em silêncio: um widget que nunca aparece e não avisa é a pior
 * forma de falha para quem está escrevendo o plugin.
 */

export const KNOWN_SLOT_NAMES = [
  "nav.primary", "nav.secondary", "nav.footer",
  "dashboard.header", "dashboard.cards", "dashboard.footer",
  "sessions.toolbar", "sessions.row.actions", "sessions.detail.tabs",
  "chat.composer.actions", "chat.message.actions", "chat.sidebar",
  "settings.tabs", "settings.section", "settings.footer",
  "skills.toolbar", "skills.card.badge", "skills.detail.panel",
  "cron.toolbar", "cron.row.actions",
  "tools.toolbar", "tools.detail.panel",
  "mcp.toolbar", "mcp.server.actions",
  "profiles.toolbar", "profiles.row.actions",
  "console.toolbar", "console.footer",
  "status.indicators", "global.modal",
] as const;

export type SlotName = (typeof KNOWN_SLOT_NAMES)[number];

const known = new Set<string>(KNOWN_SLOT_NAMES);

export class UnknownSlotError extends Error {}

export interface SlotEntry<T = unknown> {
  plugin: string;
  widget: T;
  order: number;
}

export class SlotRegistry<T = unknown> {
  #slots = new Map<string, SlotEntry<T>[]>();

  register(slot: string, plugin: string, widget: T, order = 0): void {
    if (!known.has(slot)) {
      throw new UnknownSlotError(
        `slot desconhecido: ${slot}. Um widget registrado num slot inexistente ` +
          `nunca apareceria, e o autor do plugin não teria como saber.`,
      );
    }
    const lista = this.#slots.get(slot) ?? [];
    lista.push({ plugin, widget, order });
    lista.sort((a, b) => a.order - b.order || a.plugin.localeCompare(b.plugin));
    this.#slots.set(slot, lista);
  }

  /** Ordem determinística: por `order`, e desempate por nome do plugin. */
  entries(slot: string): readonly SlotEntry<T>[] {
    return this.#slots.get(slot) ?? [];
  }

  removePlugin(plugin: string): number {
    let removidos = 0;
    for (const [slot, lista] of this.#slots) {
      const filtrado = lista.filter((e) => e.plugin !== plugin);
      removidos += lista.length - filtrado.length;
      this.#slots.set(slot, filtrado);
    }
    return removidos;
  }
}
