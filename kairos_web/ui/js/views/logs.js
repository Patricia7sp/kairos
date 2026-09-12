/* Eventos operacionais dos serviços. */
import { api } from "../api.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
const services = { web: "Web", chat: "Chat", search: "Busca", cron: "Agendamentos" };
const levels = { info: "Informação", warning: "Atenção", error: "Erro" };
const counters = { api_calls: "Chamadas ao modelo", input_tokens: "Tokens de entrada",
  output_tokens: "Tokens de saída", results: "Resultados" };
const timestamp = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Horário indisponível"
    : `${date.toLocaleString("pt-BR", { timeZone: "UTC" })} UTC`;
};
const eventMarkup = (event) => `<article class="k-card" data-log-event>
  <div class="k-skill__meta">
    <time datetime="${esc(event.timestamp)}">${esc(timestamp(event.timestamp))}</time>
    <span class="k-badge">${esc(services[event.service] || event.service)}</span>
    <span class="k-badge ${event.level === "error" ? "k-badge--danger" : ""}">${esc(levels[event.level] || event.level)}</span>
  </div>
  <p style="overflow-wrap:anywhere">${esc(event.message)}</p>
  ${Object.entries(event.counters || {}).length ? `<p class="k-tabela__sub" style="overflow-wrap:anywhere">${
    Object.entries(event.counters).map(([key, value]) =>
      `${esc(counters[key] || key)}: ${esc(typeof value === "number" ? value.toLocaleString("pt-BR") : value)}`,
    ).join(" · ")}</p>` : ""}
</article>`;

export async function logsView(root, _route, { signal } = {}) {
  const listeners = new AbortController();
  let disposed = Boolean(signal?.aborted);
  let generation = 0;
  let pending = null;
  const dispose = () => {
    disposed = true;
    generation += 1;
    pending?.abort();
    listeners.abort();
    signal?.removeEventListener("abort", dispose);
  };
  if (disposed) return dispose;
  signal?.addEventListener("abort", dispose, { once: true });
  root.innerHTML = `
    <div class="k-page-head"><h1>Registros</h1><p>Eventos dos serviços Web, Chat, Busca e Agendamentos.</p></div>
    <form class="k-skill-bar" aria-label="Filtrar registros">
      <label class="k-field">Serviço<select class="k-select" name="service">
        <option value="">Todos os serviços</option>${Object.entries(services).map(([value, label]) =>
          `<option value="${value}">${label}</option>`).join("")}</select></label>
      <label class="k-field">Nível<select class="k-select" name="level">
        <option value="">Todos os níveis</option>${Object.entries(levels).map(([value, label]) =>
          `<option value="${value}">${label}</option>`).join("")}</select></label>
      <label class="k-field">Limite<input class="k-input" name="limit" type="number" min="1" max="200" step="1" value="50" required></label>
      <button class="k-btn k-btn--ghost" type="submit" data-logs-refresh>Atualizar</button>
    </form>
    <p class="k-sk__resumo">Mais recentes primeiro. Horários em UTC.</p>
    <p class="k-sk__resumo" data-logs-status role="status"></p>
    <section data-logs-content aria-label="Eventos dos serviços"></section>`;
  const form = root.querySelector("form");
  const content = root.querySelector("[data-logs-content]");
  const status = root.querySelector("[data-logs-status]");
  const refresh = root.querySelector("[data-logs-refresh]");
  const showError = (message) => {
    status.textContent = "";
    content.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar os registros</h2>
      <p>${esc(message || "Tente novamente em instantes.")}</p><p>Use Atualizar para tentar novamente.</p></div>`;
  };
  const load = async () => {
    if (disposed || !form.reportValidity()) return;
    const current = ++generation;
    pending?.abort();
    pending = new AbortController();
    refresh.disabled = true;
    status.textContent = "Carregando registros…";
    content.setAttribute("aria-busy", "true");
    content.innerHTML = '<div class="k-skeleton" style="height:240px"></div>';
    try {
      const data = await api.logs({
        limit: Number(form.elements.limit.value), service: form.elements.service.value,
        level: form.elements.level.value, signal: pending.signal,
      });
      if (disposed || current !== generation) return;
      if (data?.state === "unavailable") {
        status.textContent = "Registros indisponíveis.";
        content.innerHTML = '<div class="k-card k-empty"><p>Os eventos dos serviços ainda não estão disponíveis nesta instância.</p></div>';
      } else if (data?.state !== "ready" || !Array.isArray(data.events)) {
        showError();
      } else {
        status.textContent = `${data.events.length} registro${data.events.length === 1 ? "" : "s"} exibido${data.events.length === 1 ? "" : "s"}.`;
        content.innerHTML = data.events.length
          ? `<div class="k-grid">${data.events.map(eventMarkup).join("")}</div>`
          : '<div class="k-card k-empty"><h2>Nenhum registro encontrado</h2><p>Nenhum evento disponível para os filtros selecionados.</p></div>';
      }
    } catch (error) {
      if (!disposed && current === generation) showError(error.message);
    } finally {
      if (!disposed && current === generation) {
        refresh.disabled = false;
        content.setAttribute("aria-busy", "false");
      }
    }
  };
  form.addEventListener("submit", (event) => { event.preventDefault(); void load(); }, { signal: listeners.signal });
  form.addEventListener("change", () => void load(), { signal: listeners.signal });
  void load();
  return dispose;
}
