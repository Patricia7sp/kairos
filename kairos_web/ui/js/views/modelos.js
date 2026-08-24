/* Modelos: o catálogo dos provedores e qual é o padrão. */

import { api, ApiError } from "../api.js";
import { icons } from "../icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const milhares = (n) => (typeof n === "number" ? n.toLocaleString("pt-BR") : "—");

export async function modelosView(raiz) {
  raiz.innerHTML = `
    <div class="k-page-head">
      <h1>Modelos</h1>
      <p>O que cada provedor oferece, e qual responde por padrão.</p>
    </div>
    <div data-conteudo><div class="k-skeleton" style="height:260px"></div></div>`;

  const alvo = raiz.querySelector("[data-conteudo]");
  // Provedores e modelos são pedidos juntos, mas falham separados: sem chave
  // configurada o catálogo ainda tem valor, e um erro num não deve apagar o outro.
  const [rModelos, rProvedores] = await Promise.allSettled([api.modelos(), api.provedores()]);

  if (rModelos.status === "rejected") {
    const e = rModelos.reason;
    const dica = e instanceof ApiError && e.naoImplementado
      ? "Esta rota ainda não existe no servidor." : "Os logs do container têm o detalhe.";
    alvo.innerHTML = `<div class="k-error" role="alert"><h3>Não foi possível carregar os modelos</h3>
      <p>${esc(e.message)}</p><p>${dica}</p></div>`;
    return;
  }

  const { models = [], default_model: padrao, default_provider: provPadrao } = rModelos.value;
  const provedores = rProvedores.status === "fulfilled" ? rProvedores.value.providers || [] : [];
  const porProvedor = new Map();
  for (const m of models) {
    if (!porProvedor.has(m.provider)) porProvedor.set(m.provider, []);
    porProvedor.get(m.provider).push(m);
  }

  const estado = (nome) => {
    const p = provedores.find((x) => x.provider === nome);
    if (!p) return '<span class="k-badge">sem estado</span>';
    return p.connected
      ? '<span class="k-badge k-badge--ok">conectado</span>'
      : `<span class="k-badge k-badge--warn" title="${esc(p.message)}">sem chave</span>`;
  };

  alvo.innerHTML = `
    <div class="k-card k-modelo-padrao">
      <div>
        <div class="k-stat__label">Padrão</div>
        <div class="k-modelo-padrao__valor"><code>${esc(padrao ?? "—")}</code></div>
      </div>
      <span class="k-badge k-badge--accent">${esc(provPadrao ?? "—")}</span>
    </div>
    ${[...porProvedor.entries()].map(([prov, itens]) => `
      <section class="k-skill-grupo">
        <h2 class="k-skill-grupo__titulo">
          ${esc(prov)} <span class="k-badge">${itens.length}</span> ${estado(prov)}
        </h2>
        <div class="k-card k-tabela-envolve">
          <table class="k-tabela">
            <caption class="k-sr">Modelos de ${esc(prov)}</caption>
            <thead><tr>
              <th scope="col">Modelo</th><th scope="col">Contexto</th>
              <th scope="col">Recursos</th>
            </tr></thead>
            <tbody>${itens.map((m) => `
              <tr${m.id === padrao ? ' class="k-tabela__destaque"' : ""}>
                <td><code>${esc(m.id)}</code><div class="k-tabela__sub">${esc(m.name)}</div></td>
                <td>${milhares(m.context_length ?? m.context_window)}</td>
                <td>
                  ${m.supports_tools ? '<span class="k-badge">ferramentas</span>' : ""}
                  ${m.supports_vision ? '<span class="k-badge">visão</span>' : ""}
                </td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </section>`).join("")}`;
  void icons;
}
