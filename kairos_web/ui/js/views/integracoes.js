/* Integrações: canais de mensageria e webhooks em abas próprias.
 *
 * Mensageria reutiliza a view do antigo painel (sem o webhook, que agora tem
 * gestão própria) e Webhooks edita endpoints nomeados você-a-você: o webhook
 * não tem segredo — o endpoint É a entrega — então lista/adiciona/remove/testa
 * direto no arquivo, sempre falhando fechado em 422 no backend. */

import { api } from "../api.js";
import { mensageriaView } from "./mensageria.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

const ABAS = [
  { id: "canais", rotulo: "Mensageria" },
  { id: "webhooks", rotulo: "Webhooks" },
];

function textoStatus(el, msg, kind = "") {
  el.className = `k-provider__status ${kind ? `k-provider__status--${kind}` : ""}`;
  el.textContent = msg;
}

function badgeWebhooks(estado) {
  if (estado.enabled && estado.endpoints.length) return '<span class="k-badge k-badge--ok">Entregando</span>';
  if (estado.enabled) return '<span class="k-badge k-badge--warn">Sem endpoints</span>';
  return '<span class="k-badge">Desativado</span>';
}

function montarWebhooks(painel, signal) {
  const listeners = new AbortController();
  const onAbort = () => listeners.abort();
  signal?.addEventListener("abort", onAbort, { once: true });

  painel.innerHTML = `<div class="k-page-head"><h2>Webhooks</h2>
    <p>Entrega por URL sem plataforma: defina endpoints nomeados e envie para
      <code>webhook:{nome}</code>. Salvar é escrever de verdade no arquivo de configuração;
      se algo estiver errado, o painel diz, e o backend recusa.</p></div>
    <p class="k-provider__status" aria-live="polite" data-wh-status></p>
    <section class="k-card k-skill" aria-label="Endpoints de webhook">
      <header class="k-skill__head"><div>
        <h3>Endpoints</h3>
        <div class="k-skill__meta" data-wh-meta></div>
      </div>
      <label class="k-switch"><input type="checkbox" name="enabled" data-wh-enabled>
        <span class="k-switch__track"></span><span class="k-switch__thumb"></span></label>
      </header>
      <div class="k-tabela-envolve"><table class="k-tabela" data-wh-tabela></table></div>
      <form data-wh-form>
        <div class="k-grid k-grid--2" style="margin-top: var(--k-space-3)">
          <label class="k-field"><span class="k-label">Nome do endpoint</span>
            <input class="k-input" name="nome" placeholder="alerta" required></label>
          <label class="k-field"><span class="k-label">URL (http/https)</span>
            <input class="k-input" name="url" placeholder="https://hooks.seu-servico.com/x" required></label>
        </div>
        <div class="k-provider__actions" style="margin-top: var(--k-space-2)">
          <button class="k-btn k-btn--primary" type="submit">Adicionar endpoint</button>
        </div>
      </form>
    </section>`;

  const status = painel.querySelector("[data-wh-status]");
  const meta = painel.querySelector("[data-wh-meta]");
  const tabela = painel.querySelector("[data-wh-tabela]");
  const liga = painel.querySelector("[data-wh-enabled]");
  const form = painel.querySelector("[data-wh-form]");
  const estado = { enabled: false, endpoints: [] };

  const render = () => {
    meta.innerHTML = badgeWebhooks(estado);
    liga.checked = estado.enabled;
    tabela.innerHTML = estado.endpoints.length
      ? `<thead><tr><th>Endpoint</th><th>URL</th><th></th></tr></thead><tbody>
          ${estado.endpoints.map((e) => `<tr>
            <td><strong>${esc(e.name)}</strong><div class="k-tabela__sub"><code>webhook:${esc(e.name)}</code></div></td>
            <td><code>${esc(e.url)}</code></td>
            <td><div class="k-provider__actions">
              <button class="k-btn k-btn--ghost" type="button" data-wh-test="${esc(e.name)}">Testar</button>
              <button class="k-btn k-btn--ghost" type="button" data-wh-remove="${esc(e.name)}">Remover</button>
            </div></td>
          </tr>`).join("")}</tbody>`
      : '<tbody><tr><td>Nenhum endpoint configurado ainda.</td></tr></tbody>';
  };

  const carregar = async (mensagem = "") => {
    if (signal?.aborted) return;
    try {
      const dados = await api.endpointsWebhook();
      estado.enabled = dados.enabled;
      estado.endpoints = dados.endpoints || [];
      render();
      textoStatus(status, mensagem || `Endpoints carregados (${estado.endpoints.length}).`, mensagem ? "ok" : "");
    } catch (error) {
      if (signal?.aborted) return;
      textoStatus(status, error.body?.detail || error.message || "Falha ao carregar endpoints.", "error");
    }
  };

  liga.addEventListener("change", async () => {
    liga.disabled = true;
    try {
      await api.salvarMensageria("webhook", { enabled: liga.checked, endpoints: estado.endpoints });
      if (!signal?.aborted) await carregar(liga.checked ? "Webhooks habilitados." : "Webhooks desabilitados.");
    } catch (error) {
      if (signal?.aborted) return;
      textoStatus(status, error.body?.detail || error.message || "Falha ao alternar webhooks.", "error");
    } finally {
      if (!signal?.aborted) liga.disabled = false;
    }
  }, { signal: listeners.signal });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nome = form.elements.nome.value.trim();
    const url = form.elements.url.value.trim();
    if (!/^https?:\/\//.test(url)) {
      textoStatus(status, "URL inválida: use http(s).", "error");
      return;
    }
    const botao = form.querySelector('button[type="submit"]');
    botao.disabled = true;
    textoStatus(status, "Adicionando endpoint…");
    try {
      await api.criarEndpointWebhook(nome, url);
      form.elements.nome.value = "";
      form.elements.url.value = "";
      await carregar(`Endpoint ${nome} adicionado.`);
    } catch (error) {
      if (signal?.aborted) return;
      textoStatus(status, error.body?.detail || error.message || "Falha ao adicionar endpoint.", "error");
    } finally {
      if (!signal?.aborted) botao.disabled = false;
    }
  }, { signal: listeners.signal });

  tabela.addEventListener("click", async (event) => {
    const alvo = event.target.closest("[data-wh-test], [data-wh-remove]");
    if (!alvo || signal?.aborted) return;
    const nome = alvo.dataset.whTest || alvo.dataset.whRemove;
    if (alvo.dataset.whRemove && !window.confirm(`Remover o endpoint "${nome}"? Envios para webhook:${nome} deixam de ter destino.`)) return;
    if (alvo.dataset.whRemove) {
      textoStatus(status, "Removendo endpoint…");
      try {
        await api.removerEndpointWebhook(nome);
        await carregar(`Endpoint ${nome} removido.`);
      } catch (error) {
        if (signal?.aborted) return;
        textoStatus(status, error.body?.detail || error.message || "Falha ao remover endpoint.", "error");
      }
      return;
    }
    alvo.disabled = true;
    textoStatus(status, `Testando webhook:${nome}…`);
    try {
      const r = await api.testarMensageria("webhook", `webhook:${nome}`);
      if (signal?.aborted) return;
      textoStatus(status, r.sent ? (r.ok ? "Mensagem de teste enviada." : "Envio de teste falhou.") : (r.ok ? "OK." : `Sem resposta: ${r.message || "indisponível"}.`), r.ok ? "ok" : "error");
    } catch (error) {
      if (signal?.aborted) return;
      textoStatus(status, error.body?.detail || error.message || "Falha ao testar.", "error");
    } finally {
      if (!signal?.aborted) alvo.disabled = false;
    }
  }, { signal: listeners.signal });

  void carregar();
}

export async function integracoesView(raiz, _rota, { signal } = {}) {
  if (signal?.aborted) return;
  const cleanups = [];
  const dispose = () => {
    for (const cleanup of cleanups) {
      try { cleanup(); } catch { /* já desmontado */ }
    }
    signal?.removeEventListener("abort", dispose);
  };
  signal?.addEventListener("abort", dispose, { once: true });

  raiz.innerHTML = `<div class="k-page-head"><h1>Integrações</h1>
    <p>Entrega de mensagens e comandos: canais de mensageria com segredo no cofre
      e webhooks com endpoints nomeados, cada um com sua gestão.</p></div>
    <div class="k-tabs" role="tablist" aria-label="Integrações">
      ${ABAS.map((aba, i) => `<button class="k-tabs__tab" id="integ-tab-${aba.id}" role="tab"
        aria-selected="${i === 0}" aria-controls="integ-painel-${aba.id}" data-tab="${aba.id}"
        ${i === 0 ? "" : 'tabindex="-1"'}>${esc(aba.rotulo)}</button>`).join("")}
    </div>
    ${ABAS.map((aba, i) => `<section class="k-tabs__painel" id="integ-painel-${aba.id}"
      role="tabpanel" aria-labelledby="integ-tab-${aba.id}" data-painel="${aba.id}"
      ${i === 0 ? "" : "hidden"}></section>`).join("")}`;

  const paineis = {};
  const montados = {};
  for (const aba of ABAS) paineis[aba.id] = raiz.querySelector(`[data-painel="${aba.id}"]`);

  const ativar = (id, moverFoco = false) => {
    if (signal?.aborted) return;
    for (const aba of ABAS) {
      const painel = paineis[aba.id];
      const tab = raiz.querySelector(`[data-tab="${aba.id}"]`);
      painel.hidden = aba.id !== id;
      const ativo = aba.id === id;
      tab.setAttribute("aria-selected", String(ativo));
      tab.tabIndex = ativo ? 0 : -1;
      if (moverFoco && ativo) tab.focus();
    }
    if (!montados[id]) {
      montados[id] = true;
      if (id === "webhooks") montarWebhooks(paineis.webhooks, signal);
      else void mensageriaView(paineis.canais, {}, { signal }).then((cleanup) => {
        if (typeof cleanup === "function") cleanups.push(cleanup);
      });
    }
  };

  raiz.querySelector('[role="tablist"]').addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (tab) ativar(tab.dataset.tab);
  }, { signal });

  raiz.querySelector('[role="tablist"]').addEventListener("keydown", (event) => {
    if (!["ArrowRight", "ArrowLeft"].includes(event.key)) return;
    const ordem = ABAS.map((aba) => aba.id);
    const atual = ordem.indexOf(raiz.querySelector('[aria-selected="true"]').dataset.tab);
    const proximo = (atual + (event.key === "ArrowRight" ? 1 : -1) + ordem.length) % ordem.length;
    event.preventDefault();
    ativar(ordem[proximo], true);
  }, { signal });

  ativar("canais");
  return dispose;
}