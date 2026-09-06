/* Sessões: as conversas guardadas no state.db.
 *
 * Lista à esquerda, transcrição à direita. Uma sessão é uma sequência, e ler
 * uma sequência num diálogo modal obriga a fechar e reabrir para comparar —
 * o painel lado a lado deixa a lista visível enquanto se lê.
 */

import { api, ApiError } from "../api.js";
import { icons } from "../icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function quando(v) {
  if (!v) return "—";
  // O banco guarda epoch em segundos; `Date` espera milissegundos.
  const d = new Date(v < 1e12 ? v * 1000 : v);
  return Number.isNaN(d.getTime())
    ? String(v)
    : d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

const milhares = (n) => (typeof n === "number" ? n.toLocaleString("pt-BR") : "0");

function erroHtml(e, titulo) {
  const dica = e instanceof ApiError && e.naoImplementado
    ? "Esta rota ainda não existe no servidor."
    : "Os logs do container têm o detalhe.";
  return `<div class="k-error" role="alert"><h3>${esc(titulo)}</h3>
    <p>${esc(e.message)}</p><p>${dica}</p>
    <code>${esc(e.path || "")} ${e.status ? "→ HTTP " + e.status : ""}</code></div>`;
}

export async function sessoesView(raiz) {
  raiz.innerHTML = `
    <div class="k-page-head">
      <h1>Sessões</h1>
      <p>Conversas guardadas em <code>state.db</code> — pela CLI, pelo painel ou por uma plataforma conectada.</p>
    </div>
    <div class="k-ses__filtros" role="search">
      <label class="k-sr" for="k-ses-busca">Buscar sessões</label>
      <input id="k-ses-busca" class="k-input k-ses__busca" data-busca-sessoes
        type="search" placeholder="Buscar por título, origem ou mensagem…" autocomplete="off">
      <label class="k-sr" for="k-ses-filtro">Filtrar sessões</label>
      <select id="k-ses-filtro" class="k-select" data-filtro-sessoes>
        <option value="todas">Todas</option>
        <option value="abertas">Abertas</option>
        <option value="encerradas">Encerradas</option>
        <option value="arquivadas">Arquivadas</option>
        <option value="ocultas">Ocultas</option>
      </select>
      <label class="k-sr" for="k-ses-tag">Filtrar por tag</label>
      <select id="k-ses-tag" class="k-select" data-filtro-tag>
        <option value="">Todas as tags</option>
      </select>
      <button type="button" class="k-btn k-btn--ghost" data-atualizar-sessoes>Atualizar</button>
    </div>
    <div data-conteudo><div class="k-skeleton" style="height:340px"></div></div>`;

  const alvo = raiz.querySelector("[data-conteudo]");
  const busca = raiz.querySelector("[data-busca-sessoes]");
  const filtro = raiz.querySelector("[data-filtro-sessoes]");
  const filtroTag = raiz.querySelector("[data-filtro-tag]");
  const atualizar = raiz.querySelector("[data-atualizar-sessoes]");
  let timer;
  let selecionadaId = "";
  let carregamento = 0;
  let deslocamento = 0;
  const limite = 25;

  const carregar = async () => {
    const idCarregamento = ++carregamento;
    alvo.innerHTML = `<div class="k-skeleton" style="height:340px"></div>`;
    let dados;
    try {
      dados = await api.sessoes({
        q: busca.value.trim(), status: filtro.value, tag: filtroTag.value,
        offset: deslocamento, limit: limite,
      });
    } catch (e) {
      if (idCarregamento !== carregamento) return;
      alvo.innerHTML = erroHtml(e, "Não foi possível carregar as sessões");
      return;
    }
    if (idCarregamento !== carregamento) return;
    filtroTag.innerHTML = `<option value="">Todas as tags</option>` +
      (dados.tag_counts || []).map((item) =>
        `<option value="${esc(item.tag)}">${esc(item.tag)} (${item.count})</option>`).join("");
    filtroTag.value = dados.filtro?.tag || filtroTag.value;
    const sessoes = dados.sessions || [];
    if (!sessoes.length) {
      alvo.innerHTML = `<div class="k-empty k-card">
        <h3>Nenhuma sessão encontrada</h3>
        <p>Ajuste a busca ou o filtro. Toda conversa com o agente aparece aqui.</p>
      </div>${paginacao(dados)}`;
      return;
    }
    alvo.innerHTML = `
      <div class="k-grid k-grid--stats" style="margin-bottom:var(--k-space-5)">
        <div class="k-card"><div class="k-stat__label">Sessões</div>
          <div class="k-stat__value">${sessoes.length}</div></div>
        <div class="k-card"><div class="k-stat__label">Abertas</div>
          <div class="k-stat__value">${dados.abertas ?? 0}</div></div>
        <div class="k-card"><div class="k-stat__label">Mensagens</div>
          <div class="k-stat__value">${milhares(sessoes.reduce((a, s) => a + (s.message_count || 0), 0))}</div></div>
        <div class="k-card"><div class="k-stat__label">Tokens</div>
          <div class="k-stat__value">${milhares(sessoes.reduce((a, s) => a + (s.input_tokens || 0) + (s.output_tokens || 0), 0))}</div>
          <div class="k-stat__hint">entrada + saída</div></div>
      </div>
      <div class="k-ses">
        <div class="k-card k-tabela-envolve k-ses__lista">
          <table class="k-tabela">
            <caption class="k-sr">Sessões guardadas</caption>
            <thead><tr>
              <th scope="col">Sessão</th><th scope="col">Estado</th>
              <th scope="col">Msgs</th><th scope="col">Início</th>
            </tr></thead>
            <tbody>${sessoes.map(linha).join("")}</tbody>
          </table>
        </div>
        <div class="k-ses__detalhe" data-detalhe>
          <div class="k-empty k-card"><h3>Selecione uma sessão</h3>
            <p>A transcrição aparece aqui.</p></div>
        </div>
        ${paginacao(dados)}
      </div>`;

    const painel = alvo.querySelector("[data-detalhe]");
    const selecionar = (tr) => {
      selecionadaId = tr.dataset.sessao;
      for (const outro of alvo.querySelectorAll("[data-sessao]")) {
        const ativa = outro === tr;
        outro.classList.toggle("k-tabela__destaque", ativa);
        outro.setAttribute("aria-selected", String(ativa));
      }
      void abrirSessao(selecionadaId, painel, carregar);
    };
    alvo.querySelector("tbody").addEventListener("click", (ev) => {
      const tr = ev.target.closest("[data-sessao]");
      if (tr) selecionar(tr);
    });
    alvo.querySelector("tbody").addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const tr = ev.target.closest("[data-sessao]");
      if (!tr) return;
      ev.preventDefault();
      selecionar(tr);
    });
    if (selecionadaId) {
      const tr = [...alvo.querySelectorAll("[data-sessao]")]
        .find((item) => item.dataset.sessao === selecionadaId);
      if (tr) selecionar(tr);
    }
  };
  busca.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { deslocamento = 0; void carregar(); }, 250);
  });
  filtro.addEventListener("change", () => { deslocamento = 0; void carregar(); });
  filtroTag.addEventListener("change", () => { deslocamento = 0; void carregar(); });
  atualizar.addEventListener("click", () => void carregar());
  raiz.addEventListener("click", (ev) => {
    const anterior = ev.target.closest("[data-pagina-anterior]");
    const proxima = ev.target.closest("[data-pagina-proxima]");
    if (anterior) { deslocamento = Math.max(0, deslocamento - limite); void carregar(); }
    if (proxima) { deslocamento += limite; void carregar(); }
  });
  void carregar();
}

function linha(s) {
  const nome = s.title || s.id;
  const fixada = s.pinned ? `<span class="k-badge k-badge--accent" aria-label="Sessão fixada">fixada</span> ` : "";
  const tags = (s.tags || []).map((tag) => `<span class="k-badge k-badge--accent">${esc(tag)}</span>`).join(" ");
  return `
    <tr data-sessao="${esc(s.id)}" tabindex="0" aria-selected="false">
      <td>${fixada}<span class="k-ses__nome">${esc(nome)}</span>
        <div class="k-tabela__sub"><code>${esc(s.source)}</code>${s.model ? " · " + esc(s.model) : ""}</div>
        ${tags ? `<div class="k-ses__tags">${tags}</div>` : ""}</td>
      <td><span class="k-badge ${s.status === "aberta" ? "k-badge--ok" : ""}">${esc(s.archived ? "arquivada" : s.status)}</span></td>
      <td style="font-variant-numeric:tabular-nums">${milhares(s.message_count)}</td>
      <td>${esc(quando(s.started_at))}</td>
    </tr>`;
}

function paginacao(dados) {
  const inicio = dados.total ? dados.offset + 1 : 0;
  const fim = Math.min(dados.total || 0, dados.offset + (dados.sessions || []).length);
  return `<nav class="k-ses__paginacao" aria-label="Paginação de sessões">
    <span>${inicio}–${fim} de ${dados.total || 0}</span>
    <span class="k-ses__pag-botoes">
      <button type="button" class="k-btn k-btn--ghost" data-pagina-anterior ${dados.offset <= 0 ? "disabled" : ""}>Anterior</button>
      <button type="button" class="k-btn k-btn--ghost" data-pagina-proxima ${dados.has_more ? "" : "disabled"}>Próxima</button>
    </span>
  </nav>`;
}

async function abrirSessao(id, painel, recarregar) {
  painel.innerHTML = `<div class="k-skeleton" style="height:320px"></div>`;
  let sessao, mensagens;
  try {
    [sessao, mensagens] = await Promise.all([api.sessao(id), api.mensagens(id)]);
  } catch (e) {
    painel.innerHTML = erroHtml(e, "Não foi possível abrir a sessão");
    return;
  }

  const msgs = mensagens.messages || mensagens || [];
  painel.innerHTML = `
    <div class="k-card">
      <header class="k-ses__cab">
        <div>
          <h3>${esc(sessao.title || sessao.id)}</h3>
          <span class="k-dialog__cat"><code>${esc(sessao.id)}</code></span>
        </div>
        <div class="k-ses__acoes">
          ${sessao.execution_kind === "agent_runtime"
            ? `<a class="k-btn k-btn--primary" href="#/runtime?session=${encodeURIComponent(sessao.id)}">Abrir runtime</a>`
            : ""}
          <button type="button" class="k-btn k-btn--ghost" data-exportar-sessao>Exportar JSON</button>
          <button type="button" class="k-btn k-btn--ghost" data-exportar-markdown>Exportar Markdown</button>
          <button type="button" class="k-btn k-btn--ghost" data-fixar-sessao>${sessao.pinned ? "Desafixar" : "Fixar"}</button>
          <button type="button" class="k-btn k-btn--ghost" data-arquivar-sessao>${sessao.archived ? "Desarquivar" : "Arquivar"}</button>
          <button type="button" class="k-btn k-btn--ghost" data-ocultar-sessao>${sessao.hidden ? "Mostrar" : "Ocultar"}</button>
          <span class="k-badge ${sessao.status === "aberta" ? "k-badge--ok" : ""}">${esc(sessao.archived ? "arquivada" : sessao.status)}</span>
        </div>
      </header>
      <dl class="k-det__meta">
        <div><dt>Origem</dt><dd>${esc(sessao.source)}</dd></div>
        ${sessao.model ? `<div><dt>Modelo</dt><dd>${esc(sessao.model)}</dd></div>` : ""}
        <div><dt>Início</dt><dd>${esc(quando(sessao.started_at))}</dd></div>
        <div><dt>Fim</dt><dd>${esc(sessao.ended_at ? quando(sessao.ended_at) : "—")}</dd></div>
        <div><dt>Ferramentas</dt><dd>${milhares(sessao.tool_call_count)}</dd></div>
        <div><dt>Tokens</dt><dd>${milhares((sessao.input_tokens || 0) + (sessao.output_tokens || 0))}</dd></div>
        <div class="k-ses__tags-edicao"><dt>Tags</dt><dd>
          <input class="k-input" data-tags-edit value="${esc((sessao.tags || []).join(", "))}" aria-label="Tags da sessão">
          <button type="button" class="k-btn k-btn--ghost" data-salvar-tags>Salvar tags</button>
        </dd></div>
      </dl>
      <h4 class="k-ses__titulo-msgs">Transcrição <span class="k-badge">${msgs.length}</span></h4>
      ${msgs.length
        ? `<ol class="k-ses__msgs">${msgs.map(mensagem).join("")}</ol>`
        : `<p class="k-ses__vazio">Esta sessão não tem mensagens guardadas.</p>`}
    </div>`;
  painel.querySelector("[data-exportar-sessao]").addEventListener("click", () => exportarSessao(sessao, msgs));
  painel.querySelector("[data-exportar-markdown]").addEventListener("click", () => exportarMarkdown(sessao, msgs));
  painel.querySelector("[data-fixar-sessao]").addEventListener("click", async (ev) => {
    const botao = ev.currentTarget;
    botao.disabled = true;
    try {
      await api.atualizarSessao(id, { pinned: !sessao.pinned });
      await recarregar();
    } catch (e) {
      painel.insertAdjacentHTML("afterbegin", erroHtml(e, "Não foi possível fixar a sessão"));
      botao.disabled = false;
    }
  });
  painel.querySelector("[data-arquivar-sessao]").addEventListener("click", async (ev) => {
    const botao = ev.currentTarget;
    botao.disabled = true;
    try {
      await api.atualizarSessao(id, { archived: !sessao.archived });
      await recarregar();
    } catch (e) {
      painel.insertAdjacentHTML("afterbegin", erroHtml(e, "Não foi possível atualizar a sessão"));
      botao.disabled = false;
    }
  });
  painel.querySelector("[data-ocultar-sessao]").addEventListener("click", async (ev) => {
    const botao = ev.currentTarget;
    botao.disabled = true;
    try {
      await api.atualizarSessao(id, { hidden: !sessao.hidden });
      await recarregar();
    } catch (e) {
      painel.insertAdjacentHTML("afterbegin", erroHtml(e, "Não foi possível atualizar a visibilidade"));
      botao.disabled = false;
    }
  });
  painel.querySelector("[data-salvar-tags]").addEventListener("click", async (ev) => {
    const botao = ev.currentTarget;
    const campo = painel.querySelector("[data-tags-edit]");
    const tags = campo.value.split(",").map((tag) => tag.trim()).filter(Boolean);
    botao.disabled = true;
    try {
      await api.atualizarSessao(id, { tags });
      await recarregar();
    } catch (e) {
      painel.insertAdjacentHTML("afterbegin", erroHtml(e, "Não foi possível salvar as tags"));
      botao.disabled = false;
    }
  });
}

function exportarSessao(sessao, msgs) {
  const conteudo = JSON.stringify({ session: sessao, messages: msgs }, null, 2);
  const url = URL.createObjectURL(new Blob([conteudo], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `kairos-session-${sessao.id}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function exportarMarkdown(sessao, msgs) {
  const linhas = [`# ${sessao.title || sessao.id}`, "", `- ID: ${sessao.id}`, `- Origem: ${sessao.source}`, ""];
  for (const m of msgs) {
    const papel = m.role || "unknown";
    const texto = typeof m.content === "string" ? m.content : JSON.stringify(m.content ?? "", null, 2);
    linhas.push(`## ${papel}`, "", texto, "");
  }
  const url = URL.createObjectURL(new Blob([linhas.join("\n")], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `kairos-session-${sessao.id}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function mensagem(m) {
  const papel = m.role || "?";
  const texto = typeof m.content === "string" ? m.content : JSON.stringify(m.content ?? "", null, 1);
  return `
    <li class="k-msg k-msg--${esc(papel)}">
      <div class="k-msg__cab">
        <span class="k-badge">${esc(papel)}</span>
        ${m.tool_name ? `<span class="k-badge k-badge--accent">${esc(m.tool_name)}</span>` : ""}
        <span class="k-msg__hora">${esc(quando(m.timestamp ?? m.created_at))}</span>
      </div>
      <div class="k-msg__corpo">${esc(texto).slice(0, 4000)}</div>
    </li>`;
}

void icons;
