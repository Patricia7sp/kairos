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
    <div data-conteudo><div class="k-skeleton" style="height:340px"></div></div>`;

  const alvo = raiz.querySelector("[data-conteudo]");
  let dados;
  try {
    dados = await api.sessoes();
  } catch (e) {
    alvo.innerHTML = erroHtml(e, "Não foi possível carregar as sessões");
    return;
  }

  const sessoes = dados.sessions || [];
  if (!sessoes.length) {
    alvo.innerHTML = `<div class="k-empty k-card">
      <h3>Nenhuma sessão ainda</h3>
      <p>Toda conversa com o agente aparece aqui, com as mensagens, o modelo usado e o custo em tokens.</p>
    </div>`;
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
    </div>`;

  const painel = alvo.querySelector("[data-detalhe]");
  alvo.querySelector("tbody").addEventListener("click", (ev) => {
    const tr = ev.target.closest("[data-sessao]");
    if (!tr) return;
    for (const outro of alvo.querySelectorAll("[data-sessao]")) {
      outro.classList.toggle("k-tabela__destaque", outro === tr);
    }
    abrirSessao(tr.dataset.sessao, painel);
  });
}

function linha(s) {
  const nome = s.title || s.id;
  return `
    <tr data-sessao="${esc(s.id)}" tabindex="0">
      <td><span class="k-ses__nome">${esc(nome)}</span>
        <div class="k-tabela__sub"><code>${esc(s.source)}</code>${s.model ? " · " + esc(s.model) : ""}</div></td>
      <td><span class="k-badge ${s.status === "aberta" ? "k-badge--ok" : ""}">${esc(s.status)}</span></td>
      <td style="font-variant-numeric:tabular-nums">${milhares(s.message_count)}</td>
      <td>${esc(quando(s.started_at))}</td>
    </tr>`;
}

async function abrirSessao(id, painel) {
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
        <span class="k-badge ${sessao.status === "aberta" ? "k-badge--ok" : ""}">${esc(sessao.status)}</span>
      </header>
      <dl class="k-det__meta">
        <div><dt>Origem</dt><dd>${esc(sessao.source)}</dd></div>
        ${sessao.model ? `<div><dt>Modelo</dt><dd>${esc(sessao.model)}</dd></div>` : ""}
        <div><dt>Início</dt><dd>${esc(quando(sessao.started_at))}</dd></div>
        <div><dt>Fim</dt><dd>${esc(sessao.ended_at ? quando(sessao.ended_at) : "—")}</dd></div>
        <div><dt>Ferramentas</dt><dd>${milhares(sessao.tool_call_count)}</dd></div>
        <div><dt>Tokens</dt><dd>${milhares((sessao.input_tokens || 0) + (sessao.output_tokens || 0))}</dd></div>
      </dl>
      <h4 class="k-ses__titulo-msgs">Transcrição <span class="k-badge">${msgs.length}</span></h4>
      ${msgs.length
        ? `<ol class="k-ses__msgs">${msgs.map(mensagem).join("")}</ol>`
        : `<p class="k-ses__vazio">Esta sessão não tem mensagens guardadas.</p>`}
    </div>`;
}

function mensagem(m) {
  const papel = m.role || "?";
  const texto = typeof m.content === "string" ? m.content : JSON.stringify(m.content ?? "", null, 1);
  return `
    <li class="k-msg k-msg--${esc(papel)}">
      <div class="k-msg__cab">
        <span class="k-badge">${esc(papel)}</span>
        ${m.tool_name ? `<span class="k-badge k-badge--accent">${esc(m.tool_name)}</span>` : ""}
        <span class="k-msg__hora">${esc(quando(m.timestamp))}</span>
      </div>
      <div class="k-msg__corpo">${esc(texto).slice(0, 4000)}</div>
    </li>`;
}

void icons;
