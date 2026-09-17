/* Experiências: o aprendizado operacional (`kairos_memory`) na web.
 *
 * Fecha a divergência #2 do diagnóstico — antes só a CLI gerenciava. Confirmar,
 * invalidar, remover e registrar resultado chamam o store real pelo backend;
 * nenhuma ação aqui é otimista: a lista é recarregada do servidor. */

import { api } from "../api.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

const ROTULO_STATUS = { ativa: "Ativa", candidata: "Candidata", invalida: "Inválida" };

const badge = (status) => {
  const kind = status === "ativa" ? "ok" : status === "candidata" ? "warn" : "";
  return `<span class="k-badge ${kind ? `k-badge--${kind}` : ""}">${esc(ROTULO_STATUS[status] || status)}</span>`;
};

const pct = (n) => `${Math.round((Number(n) || 0) * 100)}%`;

function linha(exp) {
  const sucesso = exp.hits ? `${exp.successes}/${exp.hits} ok` : "sem uso registrado";
  return `<tr data-exp="${esc(exp.id)}">
    <td>
      <strong>${esc(exp.trigger)}</strong>
      <div class="k-tabela__sub">${esc(exp.correction)}</div>
      ${exp.observation && exp.observation !== exp.correction
        ? `<div class="k-tabela__sub">Observação: ${esc(exp.observation)}</div>` : ""}
    </td>
    <td>${badge(exp.status)}<div class="k-tabela__sub"><code>${esc(exp.scope)}</code></div></td>
    <td>${pct(exp.confidence)}</td>
    <td>${esc(sucesso)}</td>
    <td><div class="k-provider__actions">
      ${exp.status === "candidata"
        ? `<button class="k-btn k-btn--ghost" type="button" data-exp-confirm="${esc(exp.id)}">Confirmar</button>` : ""}
      ${exp.status !== "invalida"
        ? `<button class="k-btn k-btn--ghost" type="button" data-exp-invalidate="${esc(exp.id)}">Invalidar</button>` : ""}
      <button class="k-btn k-btn--ghost" type="button" data-exp-ok="${esc(exp.id)}">Sucesso</button>
      <button class="k-btn k-btn--ghost" type="button" data-exp-fail="${esc(exp.id)}">Falha</button>
      <button class="k-btn k-btn--ghost" type="button" data-exp-remove="${esc(exp.id)}">Remover</button>
    </div></td>
  </tr>`;
}

export async function experienciasView(raiz, _rota, { signal } = {}) {
  if (signal?.aborted) return;
  const listeners = new AbortController();
  let disposed = false;
  let generation = 0;
  let dados = { items: [], counts: {} };
  const dispose = () => {
    disposed = true;
    listeners.abort();
    signal?.removeEventListener("abort", dispose);
  };
  signal?.addEventListener("abort", dispose, { once: true });

  raiz.innerHTML = `<div class="k-page-head"><h1>Experiências</h1>
    <p>Correções validadas por humano que o agente reaproveita no <strong>turno atual</strong>
      quando a injeção está ligada. São sugestões: nunca alteram regras nem o system prompt,
      e nada aqui é auto-aprovado.</p></div>
    <div class="k-skill-bar" role="search">
      <label class="k-field"><span class="k-sr">Filtrar por estado</span>
        <select class="k-select" data-exp-status>
          <option value="">Ativas e candidatas</option>
          <option value="all">Todas (inclui inválidas)</option>
          <option value="ativa">Só ativas</option>
          <option value="candidata">Só candidatas</option>
          <option value="invalida">Só inválidas</option>
        </select></label>
      <button class="k-btn k-btn--ghost" type="button" data-exp-refresh>Atualizar</button>
    </div>
    <p class="k-sk__resumo" data-exp-summary aria-live="polite"></p>
    <details class="k-card" data-exp-form-open>
      <summary>Nova experiência</summary>
      <form data-exp-form style="margin-top: var(--k-space-3)">
        <div class="k-grid k-grid--2">
          <label class="k-field"><span class="k-label">Gatilho</span>
            <input class="k-input" name="trigger" placeholder="erro ao publicar imagem" required></label>
          <label class="k-field"><span class="k-label">Escopo</span>
            <input class="k-input" name="scope" placeholder="global" value="global"></label>
        </div>
        <label class="k-field"><span class="k-label">Correção</span>
          <input class="k-input" name="correction" placeholder="o que fazer quando o gatilho ocorre" required></label>
        <label class="k-field"><span class="k-label">Observação (opcional)</span>
          <input class="k-input" name="observation" placeholder="detalhe ou causa"></label>
        <label class="k-switch" style="margin-top: var(--k-space-2)">
          <input type="checkbox" name="confirm"><span class="k-switch__track"></span><span class="k-switch__thumb"></span>
          <span class="k-label">Já validada (entra como ativa)</span></label>
        <div class="k-provider__actions" style="margin-top: var(--k-space-2)">
          <button class="k-btn k-btn--primary" type="submit">Adicionar</button>
        </div>
      </form>
    </details>
    <div data-exp-content></div>`;

  const status = raiz.querySelector("[data-exp-status]");
  const refresh = raiz.querySelector("[data-exp-refresh]");
  const summary = raiz.querySelector("[data-exp-summary]");
  const content = raiz.querySelector("[data-exp-content]");
  const form = raiz.querySelector("[data-exp-form]");

  const carregar = async (mensagem = "") => {
    if (disposed) return;
    const current = ++generation;
    refresh.disabled = true;
    summary.textContent = "Carregando experiências…";
    content.innerHTML = '<div class="k-skeleton" style="height:220px"></div>';
    try {
      dados = await api.experiencias({ status: status.value === "all" ? "" : status.value, includeAll: status.value === "all" });
      if (disposed || generation !== current) return;
      const c = dados.counts || {};
      const total = c.total || 0;
      summary.textContent = total
        ? `Total ${total} · ${c.ativa || 0} ativas · ${c.candidata || 0} candidatas · ${c.invalida || 0} inválidas`
        : "Nenhuma experiência registrada ainda.";
      content.innerHTML = dados.items.length
        ? `<div class="k-tabela-envolve"><table class="k-tabela" aria-label="Experiências">
            <thead><tr><th scope="col">Experiência</th><th scope="col">Estado</th>
              <th scope="col">Confiança</th><th scope="col">Uso</th><th scope="col">Ações</th></tr></thead>
            <tbody>${dados.items.map(linha).join("")}</tbody></table></div>`
        : '<div class="k-card k-empty"><h2>Nada aqui</h2><p>Nenhuma experiência corresponde ao filtro.</p></div>';
      if (mensagem) summary.textContent = `${mensagem} — ${summary.textContent}`;
    } catch (error) {
      if (disposed || generation !== current) return;
      summary.textContent = "";
      content.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar as experiências</h2>
        <p>${esc(error.body?.detail || error.message)}</p></div>`;
    } finally {
      if (!disposed && generation === current) refresh.disabled = false;
    }
  };

  const acao = async (botao, fn, mensagem) => {
    botao.disabled = true;
    try {
      await fn();
      await carregar(mensagem);
    } catch (error) {
      if (disposed) return;
      summary.textContent = error.body?.detail || error.message || "A ação falhou.";
      botao.disabled = false;
    }
  };

  status.addEventListener("change", () => void carregar(), { signal: listeners.signal });
  refresh.addEventListener("click", () => void carregar(), { signal: listeners.signal });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const botao = form.querySelector('button[type="submit"]');
    botao.disabled = true;
    try {
      await api.criarExperiencia({
        trigger: form.elements.trigger.value.trim(),
        correction: form.elements.correction.value.trim(),
        observation: form.elements.observation.value.trim(),
        scope: form.elements.scope.value.trim() || "global",
        confirm: form.elements.confirm.checked,
      });
      form.reset();
      form.elements.scope.value = "global";
      await carregar("Experiência registrada.");
    } catch (error) {
      if (disposed) return;
      summary.textContent = error.body?.detail || error.message || "Falha ao registrar experiência.";
    } finally {
      if (!disposed) botao.disabled = false;
    }
  }, { signal: listeners.signal });

  content.addEventListener("click", (event) => {
    const alvo = event.target.closest("[data-exp-confirm], [data-exp-invalidate], [data-exp-ok], [data-exp-fail], [data-exp-remove]");
    if (!alvo || disposed) return;
    const id = alvo.dataset.expConfirm || alvo.dataset.expInvalidate || alvo.dataset.expOk
      || alvo.dataset.expFail || alvo.dataset.expRemove;
    if (alvo.dataset.expConfirm) return void acao(alvo, () => api.confirmarExperiencia(id), "Experiência confirmada.");
    if (alvo.dataset.expInvalidate) return void acao(alvo, () => api.invalidarExperiencia(id), "Experiência invalidada.");
    if (alvo.dataset.expOk) return void acao(alvo, () => api.registrarResultadoExperiencia(id, true), "Sucesso registrado.");
    if (alvo.dataset.expFail) return void acao(alvo, () => api.registrarResultadoExperiencia(id, false), "Falha registrada.");
    if (alvo.dataset.expRemove) {
      if (!window.confirm("Remover esta experiência? O registro é apagado; para manter histórico, use Invalidar.")) return;
      return void acao(alvo, () => api.removerExperiencia(id), "Experiência removida.");
    }
  }, { signal: listeners.signal });

  void carregar();
  return dispose;
}
