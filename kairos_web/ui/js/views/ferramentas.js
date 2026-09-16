/* The process registry is the source of this read-only catalog. */
import { api } from "../api.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);

function parametros(schema) {
  const definition = schema?.function || schema || {};
  const parameters = definition.parameters || {};
  const required = new Set(parameters.required || []);
  const rows = Object.entries(parameters.properties || {});
  if (!rows.length) return "<p>Esta ferramenta não declara parâmetros.</p>";
  return `<div class="k-tabela-envolve"><table class="k-tabela">
    <caption class="k-sr">Parâmetros da ferramenta</caption>
    <thead><tr><th scope="col">Parâmetro</th><th scope="col">Tipo</th><th scope="col">Descrição</th></tr></thead>
    <tbody>${rows.map(([name, value]) => `<tr>
      <td><code>${esc(name)}</code><div class="k-tabela__sub">${required.has(name) ? "obrigatório" : "opcional"}</div></td>
      <td>${esc(value.type || "—")}</td>
      <td>${esc(value.description || "—")}
        ${value.default !== undefined ? `<div class="k-tabela__sub">Padrão: <code>${esc(JSON.stringify(value.default))}</code></div>` : ""}
        ${value.enum ? `<div class="k-tabela__sub">Valores: ${esc(value.enum.join(", "))}</div>` : ""}
      </td></tr>`).join("")}</tbody></table></div>`;
}

function cartao(tool) {
  return `<article class="k-card k-skill" data-tool="${esc(tool.name)}">
    <header class="k-skill__head"><div>
      <h2>${esc(tool.name)}</h2>
      <div class="k-skill__meta"><span class="k-badge">${esc(tool.toolset)}</span>
        <span class="k-badge ${tool.available ? "k-badge--ok" : ""}">${tool.available ? "Disponível" : "Indisponível"}</span>
        ${tool.plugin ? `<span class="k-badge">Plugin: ${esc(tool.plugin)}</span>` : ""}
        ${tool.chat ? '<span class="k-badge k-badge--accent">No Chat</span>' : '<span class="k-badge">Fora do Chat</span>'}
        ${tool.mutating ? '<span class="k-badge k-badge--warn">Exige aprovação</span>' : ""}
      </div>
    </div></header>
    <p class="k-skill__desc">${esc(tool.description || "Sem descrição registrada.")}</p>
    ${!tool.available ? "<p>Os requisitos deste conjunto de ferramentas não estão satisfeitos.</p>" : ""}
    <details><summary>Parâmetros e definição</summary>
      <div class="k-det__bloco">${parametros(tool.schema)}</div>
      <details class="k-det__bloco"><summary>Definição JSON</summary>
        <pre class="k-msg__corpo">${esc(JSON.stringify(tool.schema || {}, null, 2))}</pre>
      </details>
    </details>
  </article>`;
}

export async function ferramentasView(raiz, _rota, { signal } = {}) {
  if (signal?.aborted) return;
  const listeners = new AbortController();
  let disposed = false;
  let generation = 0;
  let inventory = null;
  const dispose = () => {
    disposed = true;
    listeners.abort();
    signal?.removeEventListener("abort", dispose);
  };
  signal?.addEventListener("abort", dispose, { once: true });
  raiz.innerHTML = `
    <div class="k-page-head"><h1>Ferramentas</h1>
      <p>Capacidades registradas para execução pelo agente e pela CLI, tiradas do registro real
        do processo. No Chat entra apenas o subconjunto com o selo “No Chat”; as marcadas como
        “Exige aprovação” pedem confirmação a cada turno.</p>
    </div>
    <div class="k-skill-bar" role="search">
      <label class="k-field k-skill-busca"><span class="k-sr">Buscar ferramentas</span>
        <input class="k-input" type="search" data-tools-search placeholder="Buscar por nome, descrição ou conjunto…"></label>
      <label class="k-field"><span class="k-sr">Conjunto de ferramentas</span>
        <select class="k-select" data-tools-group><option value="">Todos os conjuntos</option></select></label>
      <button class="k-btn k-btn--ghost" type="button" data-tools-refresh>Atualizar</button>
    </div>
    <p class="k-sk__resumo">Disponibilidade indica os requisitos locais do registro; serviços externos são verificados durante cada execução.</p>
    <p class="k-sk__resumo" data-tools-summary aria-live="polite"></p>
    <div data-tools-content></div>`;
  const search = raiz.querySelector("[data-tools-search]");
  const group = raiz.querySelector("[data-tools-group]");
  const refresh = raiz.querySelector("[data-tools-refresh]");
  const content = raiz.querySelector("[data-tools-content]");
  const summary = raiz.querySelector("[data-tools-summary]");
  const render = () => {
    if (disposed || !inventory) return;
    const tools = inventory.tools || [];
    const term = search.value.trim().toLocaleLowerCase("pt-BR");
    const visible = tools.filter((tool) => (!group.value || tool.toolset === group.value)
      && `${tool.name} ${tool.description} ${tool.toolset}`.toLocaleLowerCase("pt-BR").includes(term));
    summary.textContent = `${visible.length} de ${tools.length} ferramentas · ${tools.filter((tool) => tool.available).length} disponíveis no registro`;
    content.innerHTML = visible.length
      ? `<div class="k-grid k-grid--cards" style="align-items:start">${visible.map(cartao).join("")}</div>`
      : `<div class="k-card k-empty"><h2>Nenhuma ferramenta encontrada</h2>
          <p>${tools.length ? "Ajuste a busca ou o conjunto selecionado." : "O registro não contém ferramentas."}</p></div>`;
  };
  const load = async () => {
    if (disposed) return;
    const current = ++generation;
    inventory = null;
    refresh.disabled = true;
    summary.textContent = "Carregando ferramentas…";
    content.innerHTML = '<div class="k-skeleton" style="height:280px"></div>';
    try {
      const data = await api.toolsets();
      if (disposed || generation !== current) return;
      inventory = data;
      const selected = group.value;
      group.innerHTML = '<option value="">Todos os conjuntos</option>' + (data.toolsets || [])
        .map((item) => `<option value="${esc(item.name)}">${esc(item.name)} (${(item.tools || []).length})</option>`).join("");
      group.value = (data.toolsets || []).some((item) => item.name === selected) ? selected : "";
      render();
    } catch (error) {
      if (disposed || generation !== current) return;
      summary.textContent = "";
      content.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar as ferramentas</h2>
        <p>${esc(error.message)}</p><p>Use Atualizar para tentar novamente.</p></div>`;
    } finally {
      if (!disposed && generation === current) refresh.disabled = false;
    }
  };
  search.addEventListener("input", render, { signal: listeners.signal });
  group.addEventListener("change", render, { signal: listeners.signal });
  refresh.addEventListener("click", () => void load(), { signal: listeners.signal });
  void load();
  return dispose;
}
