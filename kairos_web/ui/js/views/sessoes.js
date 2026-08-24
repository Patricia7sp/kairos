/* Sessões: as conversas guardadas no state.db. */

import { api, ApiError } from "../api.js";
import { icons } from "../icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function quando(valor) {
  if (!valor) return "—";
  const d = new Date(typeof valor === "number" ? valor * (valor < 1e12 ? 1000 : 1) : valor);
  if (Number.isNaN(d.getTime())) return String(valor);
  return d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export async function sessoesView(raiz) {
  raiz.innerHTML = `
    <div class="k-page-head">
      <h1>Sessões</h1>
      <p>Conversas guardadas em <code>state.db</code>.</p>
    </div>
    <div data-lista><div class="k-skeleton" style="height:220px"></div></div>`;

  const lista = raiz.querySelector("[data-lista]");
  try {
    const sessoes = (await api.sessoes()).sessions || [];
    if (!sessoes.length) {
      lista.innerHTML = `<div class="k-empty k-card">
        <h3>Nenhuma sessão ainda</h3>
        <p>Toda conversa com o agente aparece aqui — pelo chat, pela CLI ou por uma plataforma conectada.</p>
      </div>`;
      return;
    }
    lista.innerHTML = `
      <div class="k-card k-tabela-envolve">
        <table class="k-tabela">
          <caption class="k-sr">Sessões guardadas</caption>
          <thead><tr>
            <th scope="col">Sessão</th><th scope="col">Estado</th>
            <th scope="col">Mensagens</th><th scope="col">Atualizada</th>
          </tr></thead>
          <tbody>${sessoes.map((s) => `
            <tr>
              <td><code>${esc(s.id ?? s.session_id ?? "—")}</code>
                  ${s.title ? `<div class="k-tabela__sub">${esc(s.title)}</div>` : ""}</td>
              <td><span class="k-badge ${s.status === "active" ? "k-badge--ok" : ""}">${esc(s.status ?? "—")}</span></td>
              <td>${esc(s.message_count ?? s.messages ?? "—")}</td>
              <td>${esc(quando(s.updated_at ?? s.created_at))}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    const dica = e instanceof ApiError && e.naoImplementado
      ? "Esta rota ainda não existe no servidor."
      : "Os logs do container têm o detalhe.";
    lista.innerHTML = `<div class="k-error" role="alert">
      <h3>Não foi possível carregar as sessões</h3>
      <p>${esc(e.message)}</p><p>${dica}</p>
      <code>${esc(e.path || "")} ${e.status ? "→ HTTP " + e.status : ""}</code></div>`;
  }
  void icons;
}
