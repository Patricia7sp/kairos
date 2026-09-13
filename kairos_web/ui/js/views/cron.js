/* Agendamentos persistidos, executados pelo serviço do Kairos. */
import { api } from "../api.js";
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);

const states = { claimed: "Reivindicada", running: "Executando", completed: "Concluída", failed: "Falhou", unknown: "Resultado desconhecido" };
const date = (value) => value ? new Date(value).toLocaleString("pt-BR") : "—";
const scheduleText = (schedule) => schedule.kind === "once" ? `Uma vez: ${date(schedule.run_at)}` : schedule.kind === "interval" ? `A cada ${schedule.minutes} minutos` : `Cron (UTC): ${schedule.expr}`;
const occurrenceLimit = (job) => job.schedule.kind === "once" ? 1 : job.repeat?.times;
const exhausted = (job) => occurrenceLimit(job) != null
  && (job.repeat?.completed ?? 0) >= occurrenceLimit(job);
const occurrenceText = (job) => `Ocorrências reservadas: ${(job.repeat?.completed ?? 0).toLocaleString("pt-BR")}`
  + (occurrenceLimit(job) == null ? " · Sem limite" : ` de ${occurrenceLimit(job).toLocaleString("pt-BR")}`);

export async function cronView(root, _route, { signal } = {}) {
  const listeners = new AbortController();
  let disposed = Boolean(signal?.aborted), busy = false, historyVersion = 0;
  let jobs = [];
  let notepads = new Map();
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
        <label class="k-field" data-timing="recurring" hidden>Limite de ocorrências (opcional)
          <input class="k-input" name="times" type="number" min="1" max="1000000" step="1"
            placeholder="Sem limite" aria-describedby="cron-limit-help" disabled>
          <small id="cron-limit-help">Deixe vazio para continuar sem limite. Falhas e resultados desconhecidos também consomem uma ocorrência.</small>
        </label>
        <label class="k-field" data-timing="recurring" hidden>Monitor de fonte (opcional)
          <input class="k-input" name="monitor" placeholder="/caminho/para/fonte ou comando, sem shell" aria-describedby="cron-monitor-help" disabled>
          <small id="cron-monitor-help">O agente só roda quando a fonte muda. Falha da fonte é erro, nunca disparo. Sem shell: use o caminho completo e argumentos separados.</small>
        </label>
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
      const recurring = label.dataset.timing === "recurring";
      label.hidden = recurring ? kind === "once" : label.dataset.timing !== kind;
      const field = label.querySelector("input");
      field.disabled = label.hidden; field.required = !label.hidden && !recurring;
    });
  };
  const monitorInfo = (job) => {
    if (!job.monitor) return "";
    const state = job.monitor_state || {};
    return `<div class="k-monitor">
      <p><strong>Monitor</strong> · <code>${esc(job.monitor.script)}</code></p>
      <p>Última verificação: ${esc(date(state.last_checked_at))} · Última mudança: ${esc(date(state.last_changed_at))}</p>
      <div class="k-actions">
        <button class="k-btn k-btn--ghost" data-monitor-test="${esc(job.id)}">Testar fonte</button>
        <button class="k-btn k-btn--ghost" data-monitor-remove="${esc(job.id)}">Remover monitor</button>
      </div>
    </div>`;
  };
  const monitorEditor = (job) => job.monitor || job.schedule.kind === "once" || exhausted(job)
    ? "" : `<details class="k-monitor" data-monitor-edit="${esc(job.id)}">
      <summary>Definir monitor de fonte</summary>
      <label class="k-field">Comando, sem shell<input class="k-input" placeholder="/comando/para/fonte arg1 arg2" data-monitor-input></label>
      <button class="k-btn k-btn--ghost" data-monitor-save="${esc(job.id)}">Salvar fonte</button>
    </details>`;
  const notepadInfo = (job) => {
    const notes = notepads.get(job.id) || [];
    const lines = notes.map(n => `<li><code>${esc(n.key)}</code>: <span style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(n.value)}</span> <button class="k-btn k-btn--ghost" type="button" data-note-remove="${esc(job.id)}" data-note-key="${esc(n.key)}">Remover</button></li>`).join("");
    return `<details class="k-monitor" data-notepad="${esc(job.id)}">
      <summary>Bloco de notas persistente (${notes.length})</summary>
      ${notes.length ? `<ul style="list-style:none;padding-left:0">${lines}</ul>` : "<p>Sem anotações.</p>"}
      <label class="k-field">Chave<input class="k-input" placeholder="cursor" data-note-key-input></label>
      <label class="k-field">Valor<input class="k-input" placeholder="última posição lida" data-note-value-input></label>
      <button class="k-btn k-btn--ghost" type="button" data-note-save="${esc(job.id)}">Salvar anotação</button>
    </details>`;
  };
  const render = () => {
    content.innerHTML = jobs.length ? jobs.map(job => `
      <article class="k-card" data-job="${esc(job.id)}">
        <h3>${esc(job.name)}</h3><p>${esc(scheduleText(job.schedule))}</p>
        <p>${esc(occurrenceText(job))}</p>
        <p>${exhausted(job) ? "Limite de ocorrências atingido." : job.paused || !job.enabled ? "Pausado" : job.next_run_at ? `Próxima execução: ${esc(date(job.next_run_at))}` : "Agenda encerrada"}</p>
        <details><summary>Instrução</summary><p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(job.prompt)}</p></details>
        ${monitorInfo(job)}${monitorEditor(job)}${notepadInfo(job)}
        <div class="k-actions">
          ${job.next_run_at && !exhausted(job) ? `<button class="k-btn k-btn--ghost" data-pause="${esc(job.id)}">${job.paused ? "Retomar" : "Pausar"}</button>` : ""}
          <button class="k-btn k-btn--ghost" data-history="${esc(job.id)}">Histórico</button>
          <button class="k-btn k-btn--ghost" data-remove="${esc(job.id)}">Excluir</button>
        </div>
      </article>`).join("") : '<p>Nenhum agendamento criado.</p>';
  };
  const load = async () => {
    const [data, status] = await Promise.all([api.agendamentos(), api.statusAgendamentos()]);
    if (disposed) return;
    jobs = data.jobs;
    const lists = await Promise.all(jobs.map(j => api.blocoNotas(j.id)
      .then(r => [j.id, r.notes]).catch(() => [j.id, []])));
    if (disposed) return;
    notepads = new Map(lists);
    render();
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
    const limit = kind !== "once" && data.get("times") ? { times: Number(data.get("times")) } : {};
    const schedule = kind === "once" ? { kind, run_at: new Date(String(data.get("run_at"))).toISOString() } : kind === "interval" ? { kind, minutes: Number(data.get("minutes")) } : { kind, expr: data.get("expr") };
    const monitor = kind !== "once" && String(data.get("monitor") || "").trim()
      ? { type: "script", script: String(data.get("monitor")).trim() } : undefined;
    operation(() => api.criarAgendamento({ name: data.get("name"), prompt: data.get("prompt"), schedule, ...(limit), ...(monitor ? { monitor } : {}) }), "Agendamento criado.");
  }, { signal: listeners.signal });
  root.querySelector("[data-refresh]").addEventListener("click", () => operation(async () => {}, "Atualizado."), { signal: listeners.signal });
  content.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button || busy || disposed) return;
    if (button.dataset.pause) {
      const job = jobs.find(j => j.id === button.dataset.pause);
      operation(() => api.pausarAgendamento(job.id, !job.paused), job.paused ? "Agendamento retomado." : "Agendamento pausado. Uma execução já iniciada pode terminar.");
    } else if (button.dataset.monitorSave) {
      const id = button.dataset.monitorSave;
      const script = button.closest("[data-monitor-edit]")?.querySelector("[data-monitor-input]").value?.trim();
      if (!script) { message.textContent = "Informe o comando da fonte."; return; }
      operation(() => api.definirMonitor(id, script), "Monitor definido. O agente roda quando a fonte muda.");
    } else if (button.dataset.monitorTest) {
      const id = button.dataset.monitorTest;
      operation(async () => {
        const result = await api.rodarMonitor(id);
        message.textContent = result.ok ? `Fonte OK (${result.output_chars} caracteres) · decisão do próximo tick: ${result.decision}.` : `Fonte falhou: ${result.detail}.`;
      }, "");
    } else if (button.dataset.monitorRemove) {
      const id = button.dataset.monitorRemove;
      operation(() => api.removerMonitor(id), "Monitor removido. O agente volta a rodar a cada ocorrência.");
    } else if (button.dataset.noteSave) {
      const id = button.dataset.noteSave;
      const details = button.closest("[data-notepad]");
      const key = details?.querySelector("[data-note-key-input]").value?.trim();
      const value = details?.querySelector("[data-note-value-input]").value ?? "";
      if (!key) { message.textContent = "Informe a chave da anotação."; return; }
      operation(() => api.definirNota(id, key, value), "Anotação salva.");
    } else if (button.dataset.noteRemove) {
      const id = button.dataset.noteRemove;
      const key = button.dataset.noteKey;
      operation(() => api.removerNota(id, key), "Anotação removida.");
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
