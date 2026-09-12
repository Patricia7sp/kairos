/* Agendamentos persistidos, executados pelo serviço do Kairos. */
import { api } from "../api.js";
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);

const states = { claimed: "Reivindicada", running: "Executando", completed: "Concluída", failed: "Falhou", unknown: "Resultado desconhecido" };
const date = (value) => value ? new Date(value).toLocaleString("pt-BR") : "—";
const scheduleText = (schedule) => schedule.kind === "once" ? `Uma vez: ${date(schedule.run_at)}` : schedule.kind === "interval" ? `A cada ${schedule.minutes} minutos` : `Cron (UTC): ${schedule.expr}`;

export async function cronView(root, _route, { signal } = {}) {
  const listeners = new AbortController();
  let disposed = Boolean(signal?.aborted), busy = false, historyVersion = 0;
  let jobs = [];
  const dispose = () => { disposed = true; listeners.abort(); signal?.removeEventListener("abort", dispose); };
  if (disposed) return dispose;
  signal?.addEventListener("abort", dispose, { once: true });
  root.innerHTML = `
    <div class="k-page-head"><h1>Agendamentos</h1><p>Instruções executadas nos horários escolhidos, com cada resultado salvo em uma conversa própria.</p></div>
    <p data-cron-status role="status">Verificando serviço…</p>
    <section class="k-card"><h2>Novo agendamento</h2>
      <p>As execuções usam o modelo padrão e podem consumir créditos do provedor. O serviço do Kairos precisa estar em execução.</p>
      <form class="k-form">
        <label class="k-field">Nome<input class="k-input" name="name" required maxlength="120"></label>
        <label class="k-field">Instrução<textarea class="k-input" name="prompt" required maxlength="32000" rows="3"></textarea></label>
        <label class="k-field">Frequência<select class="k-select" name="kind"><option value="once">Uma vez</option><option value="interval">Intervalo</option><option value="cron">Expressão cron (UTC)</option></select></label>
        <label class="k-field" data-timing="once">Data e hora local<input class="k-input" name="run_at" type="datetime-local" required></label>
        <label class="k-field" data-timing="interval" hidden>Intervalo em minutos<input class="k-input" name="minutes" type="number" min="1" max="525600" step="1" value="60" disabled></label>
        <label class="k-field" data-timing="cron" hidden>Expressão de cinco campos<input class="k-input" name="expr" placeholder="0 9 * * *" disabled></label>
        <button class="k-btn k-btn--primary" type="submit">Criar agendamento</button>
      </form>
    </section>
    <div class="k-page-head"><h2>Seus agendamentos</h2><button class="k-btn k-btn--ghost" data-refresh>Atualizar</button></div>
    <p data-job-message role="status"></p><div data-jobs></div>
    <section class="k-card" data-job-history hidden></section>`;
  const form = root.querySelector("form");
  const content = root.querySelector("[data-jobs]");
  const message = root.querySelector("[data-job-message]");
  const history = root.querySelector("[data-job-history]");
  const setBusy = (value) => {
    busy = value;
    root.querySelectorAll("button").forEach(b => { b.disabled = value; });
  };
  const timing = () => {
    const kind = form.elements.namedItem("kind").value;
    root.querySelectorAll("[data-timing]").forEach(label => {
      label.hidden = label.dataset.timing !== kind;
      const field = label.querySelector("input");
      field.disabled = label.hidden; field.required = !label.hidden;
    });
  };
  const render = () => {
    content.innerHTML = jobs.length ? jobs.map(job => `
      <article class="k-card" data-job="${esc(job.id)}">
        <h3>${esc(job.name)}</h3><p>${esc(scheduleText(job.schedule))}</p>
        <p>${job.paused || !job.enabled ? "Pausado" : job.next_run_at ? `Próxima execução: ${esc(date(job.next_run_at))}` : "Agenda encerrada"}</p>
        <details><summary>Instrução</summary><p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(job.prompt)}</p></details>
        <div class="k-actions">
          ${job.next_run_at ? `<button class="k-btn k-btn--ghost" data-pause="${esc(job.id)}">${job.paused ? "Retomar" : "Pausar"}</button>` : ""}
          <button class="k-btn k-btn--ghost" data-history="${esc(job.id)}">Histórico</button>
          <button class="k-btn k-btn--ghost" data-remove="${esc(job.id)}">Excluir</button>
        </div>
      </article>`).join("") : '<p>Nenhum agendamento criado.</p>';
  };
  const load = async () => {
    const [data, status] = await Promise.all([api.agendamentos(), api.statusAgendamentos()]);
    if (disposed) return;
    jobs = data.jobs; render();
    root.querySelector("[data-cron-status]").textContent = status.error || (status.running ? `Serviço ativo. Verificação a cada minuto. Última verificação: ${date(status.last_tick)}.` : "Agendador não está ativo nesta instância.");
  };
  const operation = async (action, success) => {
    if (disposed || busy) return;
    setBusy(true);
    try {
      await action();
      if (disposed) return;
      await load();
      if (!disposed) message.textContent = success;
    } catch {
      if (!disposed) message.textContent = "Não foi possível concluir. Confira os dados e atualize para verificar o estado salvo.";
    } finally {
      if (!disposed) setBusy(false);
    }
  };
  form.elements.namedItem("kind").addEventListener("change", timing, { signal: listeners.signal });
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const kind = data.get("kind");
    const schedule = kind === "once" ? { kind, run_at: new Date(String(data.get("run_at"))).toISOString() } : kind === "interval" ? { kind, minutes: Number(data.get("minutes")) } : { kind, expr: data.get("expr") };
    operation(() => api.criarAgendamento({ name: data.get("name"), prompt: data.get("prompt"), schedule }), "Agendamento criado.");
  }, { signal: listeners.signal });
  root.querySelector("[data-refresh]").addEventListener("click", () => operation(async () => {}, "Atualizado."), { signal: listeners.signal });
  content.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button || busy || disposed) return;
    if (button.dataset.pause) {
      const job = jobs.find(j => j.id === button.dataset.pause);
      operation(() => api.pausarAgendamento(job.id, !job.paused), job.paused ? "Agendamento retomado." : "Agendamento pausado. Uma execução já iniciada pode terminar.");
    } else if (button.dataset.remove) {
      const id = button.dataset.remove;
      operation(() => api.excluirAgendamento(id), "Agendamento excluído. Conversas e histórico foram preservados.");
    } else if (button.dataset.history) {
      const id = button.dataset.history;
      const version = ++historyVersion;
      history.hidden = false; history.textContent = "Carregando histórico…";
      api.historicoAgendamento(id).then(data => {
        if (disposed || version !== historyVersion) return;
        history.innerHTML = `<h2>Histórico de execuções</h2>${data.executions.length ? data.executions.map(e => `<article><p><strong>${esc(states[e.status] || e.status)}</strong> · ${esc(date(e.claimed_at))}</p>${e.error ? `<p>${esc(e.error)}</p>` : ""}<a href="#/chat?session=${encodeURIComponent(e.conversation_id)}">Abrir conversa</a></article>`).join("") : "<p>Nenhuma execução registrada.</p>"}`;
      }).catch(() => {
        if (!disposed && version === historyVersion) history.textContent = "Não foi possível carregar o histórico.";
      });
    }
  }, { signal: listeners.signal });
  await operation(async () => {}, "");
  return dispose;
}
