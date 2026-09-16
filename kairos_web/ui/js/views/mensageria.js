/* Mensageria: configuração de canais (Telegram, WhatsApp, Slack) e entrega à
 * vista. Segredos vão ao cofre e nunca voltam a esta tela; o botão Testar
 * realmente conversa com a plataforma, e Enviar passa pelo ledger durável —
 * sem efeito sem rastro. Webhook tem gestão própria na aba de Integrações. */

import { api } from "../api.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

const EXEMPLOS = {
  telegram: "telegram:123456789",
  whatsapp: "whatsapp:+5511999999999",
  slack: "slack:#geral",
};

const badge = (p) => {
  if (p.deliverable) return '<span class="k-badge k-badge--ok">Entregando</span>';
  if (p.enabled) return '<span class="k-badge k-badge--warn">Sem credencial</span>';
  if (p.configured) return '<span class="k-badge k-badge--warn">Suspenso</span>';
  return '<span class="k-badge">Não configurado</span>';
};

function camposConfig(p) {
  return p.campos.map((c) => `<label class="k-field"><span class="k-label">${esc(c.label)}</span>
    <input class="k-input" name="${esc(c.key)}" value="${esc(p.valores[c.key] || "")}"></label>`).join("");
}

function credencialMarkup(p) {
  const acao = p.configured ? "Substituir" : "Salvar";
  return `<form class="k-provider__credential" data-cm-credential-form>
    <label for="cm-cred-${esc(p.platform)}">${esc(p.secreto_campo)}</label>
    <div class="k-provider__actions">
      <input id="cm-cred-${esc(p.platform)}" name="secret" type="password" autocomplete="off"
             placeholder="${p.configured ? "" : "cole aqui"}" required>
      <button class="k-btn k-btn--primary" type="submit">${acao}</button>
    </div>
    <small>O segredo vai direto ao cofre e não volta para esta tela.</small>
    ${p.configured ? `<button type="button" class="k-btn k-btn--ghost" data-cm-credential-remove>Remover do cofre</button>` : ""}
  </form>`;
}

function plataformaCard(p) {
  return `<article class="k-card k-skill" data-cm="${esc(p.platform)}">
    <header class="k-skill__head"><div>
      <h2>${esc(p.label)}</h2>
      <div class="k-skill__meta">${badge(p)}
        <span class="k-badge">Ex.: <code>${esc(EXEMPLOS[p.platform] || "")}</code></span>
      </div>
    </div>
    <label class="k-switch"><input type="checkbox" name="enabled" data-cm-enabled ${p.enabled ? "checked" : ""}>
      <span class="k-switch__track"></span><span class="k-switch__thumb"></span></label>
    </header>
    <div class="k-provider__status" aria-live="polite" data-cm-status></div>
    <form data-cm-config-form>
      <fieldset>${camposConfig(p)}</fieldset>
      <div class="k-provider__actions">
        <button class="k-btn k-btn--ghost" type="submit" data-cm-save>Salvar configuração</button>
        <button class="k-btn k-btn--ghost" type="button" data-cm-test>${p.platform === "slack" ? "Enviar teste" : "Testar conexão"}</button>
      </div>
    </form>
    ${credencialMarkup(p)}
  </article>`;
}

function textoStatus(el, msg, kind = "") {
  el.className = `k-provider__status ${kind ? `k-provider__status--${kind}` : ""}`;
  el.textContent = msg;
}

export async function mensageriaView(raiz, _rota, { signal } = {}) {
  if (signal?.aborted) return;
  const listeners = new AbortController();
  let disposed = false;
  const dispose = () => {
    disposed = true;
    listeners.abort();
    signal?.removeEventListener("abort", dispose);
  };
  signal?.addEventListener("abort", dispose, { once: true });
  const isActive = () => !disposed && !signal?.aborted;

  raiz.innerHTML = `<div class="k-page-head"><h2>Mensageria</h2>
    <p>Canais de entrega para agendamentos e comandos. O envio passa pela fila durável;
      se a plataforma cair, a mensagem continua pendente e o gateway reentrega.
      Webhooks se configuram na aba própria.</p></div>
    <p class="k-sk__resumo" data-cm-vault role="status"></p>
    <section data-cm-send class="k-card k-skill" aria-label="Envio imediato">
      <header class="k-skill__head"><div><h2>Enviar agora</h2>
        <div class="k-skill__meta"><span class="k-badge">Ex.: <code>telegram:123456789</code></span></div></div></header>
      <form data-cm-send-form>
        <label class="k-field"><span class="k-label">Destino (plataforma:endereço)</span>
          <input class="k-input" name="target" placeholder="telegram:123456789 ou webhook:nome-do-endpoint" required></label>
        <label class="k-field"><span class="k-label">Texto</span>
          <textarea class="k-textarea" name="text" rows="2" required></textarea></label>
        <p class="k-provider__status" aria-live="polite" data-cm-send-status></p>
        <button class="k-btn k-btn--primary" type="submit">Enviar</button>
      </form>
    </section>
    <div class="k-grid k-grid--cards" style="align-items:start" data-cm-lista></div>`;

  const lista = raiz.querySelector("[data-cm-lista]");
  const vaultAlerta = raiz.querySelector("[data-cm-vault]");

  const avisarVault = (estado) => {
    if (!isActive()) return;
    if (estado === "locked") {
      vaultAlerta.textContent = "O cofre de credenciais está bloqueado: desbloqueie-o para salvar ou testar segredos.";
      vaultAlerta.className = "k-sk__resumo k-badge--danger";
    } else if (estado !== "unlocked") {
      vaultAlerta.textContent = "Cofre de credenciais não disponível: configurações não-secretas continuam funcionando; segredos não podem ser salvos agora.";
      vaultAlerta.className = "k-sk__resumo k-badge--warn";
    } else {
      vaultAlerta.textContent = "";
      vaultAlerta.className = "k-sk__resumo";
    }
  };

  const lerValores = (p, form, card) => {
    const habilitado = card.querySelector("[data-cm-enabled]").checked;
    const out = { enabled: habilitado };
    for (const campo of p.campos) out[campo.key] = form.elements[campo.key].value.trim();
    return out;
  };

  const salvarConfig = async (card, p, form, resposta) => {
    if (!isActive()) return;
    try {
      const config = lerValores(p, form, card);
      await api.salvarMensageria(p.platform, config);
      if (!isActive()) return;
      const payload = await api.mensageriaStatus();
      if (!isActive()) return;
      const meta = card.querySelector(".k-skill__meta");
      if (meta) meta.innerHTML = badgeFromApi(payload, p.platform);
      textoStatus(resposta, "Configuração salva. O cofre decide a entrega: segredo presente e plataforma habilitada.", "ok");
    } catch (error) {
      if (!isActive()) return;
      textoStatus(resposta, error.body?.detail || error.message || "Falha ao salvar configuração.", "error");
    }
  };

  const testar = async (card, p, alvo, resposta, botao) => {
    if (!isActive()) return;
    botao.disabled = true;
    textoStatus(resposta, "Testando…");
    try {
      const result = await api.testarMensageria(p.platform, alvo);
      if (!isActive()) return;
      const msg = result.sent
        ? (result.ok ? "Mensagem de teste enviada." : "Envio de teste falhou.")
        : (result.ok ? "Conexão verificada." : `Sem resposta: ${result.message || "indisponível."}`);
      textoStatus(resposta, msg, result.ok ? "ok" : "error");
    } catch (error) {
      if (!isActive()) return;
      textoStatus(resposta, error.body?.detail || error.message || "Falha ao testar.", "error");
    } finally {
      if (isActive()) botao.disabled = false;
    }
  };

  const mudarCredencial = async (card, p, form, resposta, aria, remover = false) => {
    if (!isActive()) return;
    if (remover && !window.confirm(`Remover a credencial de ${p.label} do cofre? A plataforma deixa de entregar.`)) return;
    const input = form.elements.secret;
    const submit = form.querySelector('button[type="submit"]');
    textoStatus(resposta, remover ? "Removendo credencial…" : "Salvando credencial…");
    input.disabled = true;
    submit.disabled = true;
    try {
      if (remover) await api.removerCredencialMensageria(p.platform);
      else {
        const segredo = input.value.trim();
        if (!segredo) throw new Error("O segredo não pode ficar vazio.");
        await api.salvarCredencialMensageria(p.platform, segredo);
      }
      if (!isActive()) return;
      input.value = "";
      textoStatus(resposta, remover ? "Credencial removida do cofre." : "Credencial salva no cofre.", "ok");
      const flag = badgeFromApi(await api.mensageriaStatus(), p.platform);
      if (!isActive()) return;
      const meta = card.querySelector(".k-skill__meta");
      if (meta) meta.innerHTML = flag;
      const removerBtn = card.querySelector("[data-cm-credential-remove]");
      if (removerBtn) removerBtn.remove();
    } catch (error) {
      if (!isActive()) return;
      if (error.status === 409) textoStatus(resposta, error.body?.detail || "Cofre bloqueado.", "error");
      else textoStatus(resposta, error.body?.detail || error.message || "Falha ao alterar credencial.", "error");
    } finally {
      if (isActive()) {
        input.disabled = false;
        submit.disabled = false;
      }
    }
  };

  const formEnvio = raiz.querySelector("[data-cm-send-form]");
  formEnvio.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!isActive()) return;
    const status = raiz.querySelector("[data-cm-send-status]");
    const botao = formEnvio.querySelector('button[type="submit"]');
    botao.disabled = true;
    textoStatus(status, "Enviando…");
    try {
      const r = await api.enviarMensagem(formEnvio.elements.target.value.trim(), formEnvio.elements.text.value);
      if (!isActive()) return;
      if (r.delivered) textoStatus(status, `Entregue (${r.obligation_id ? r.obligation_id.slice(0, 8) : "ok"}).`, "ok");
      else if (r.pending) textoStatus(status, "Mensagem agendada: a plataforma não respondeu agora; o gateway reentrega. Pendente na fila.", "warn");
      else textoStatus(status, `Falha permanente na entrega: ${r.detail || "sem detalhe."}`, "error");
      formEnvio.elements.text.value = "";
    } catch (error) {
      if (!isActive()) return;
      textoStatus(status, error.body?.detail || error.message || "Falha ao enviar.", "error");
    } finally {
      if (isActive()) botao.disabled = false;
    }
  }, { signal: listeners.signal });

  try {
    const dados = await api.mensageriaStatus();
    if (!isActive()) return dispose;
    avisarVault(dados.platforms?.[0]?.vault);
    const plataformas = (dados.platforms || []).filter((p) => p.platform !== "webhook");
    lista.innerHTML = plataformas.map(plataformaCard).join("");
    for (const p of plataformas) {
      const card = lista.querySelector(`[data-cm="${p.platform}"]`);
      if (!card) continue;
      const status = card.querySelector("[data-cm-status]");
      const form = card.querySelector("[data-cm-config-form]");
      const response = card.querySelector("[data-cm-credential-form]");
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        void salvarConfig(card, p, form, status);
      }, { signal: listeners.signal });
      form.querySelector("[data-cm-save]").addEventListener("click", (e) => {
        e.preventDefault();
        void salvarConfig(card, p, form, status);
      }, { signal: listeners.signal });
      const toggle = form.querySelector("[data-cm-enabled]");
      toggle?.addEventListener("change", () => {
        void salvarConfig(card, p, form, status);
      }, { signal: listeners.signal });
      form.querySelector("[data-cm-test]").addEventListener("click", (e) => {
        e.preventDefault();
        const alvo = p.platform === "slack" ? null : "";
        void testar(card, p, alvo, status, e.currentTarget);
      }, { signal: listeners.signal });
      response?.addEventListener("submit", (e) => {
        e.preventDefault();
        void mudarCredencial(card, p, response, status);
      }, { signal: listeners.signal });
      response?.querySelector("[data-cm-credential-remove]")?.addEventListener("click", () => {
        void mudarCredencial(card, p, response, status, undefined, true);
      }, { signal: listeners.signal });
    }
    const entregues = dados.obligations || {};
    const pendentes = entregues.pending || 0;
    const rota = raiz.querySelector("[data-cm-send]");
    if (pendentes) rota.querySelector(".k-skill__meta").insertAdjacentHTML("beforeend",
      `<span class="k-badge k-badge--warn">${pendentes} pendente(s) aguardando reentrega</span>`);
  } catch (error) {
    if (!isActive()) return dispose;
    lista.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar a mensageria</h2>
      <p>${esc(error.message || error)}</p></div>`;
  }
  return dispose;
}

function badgeFromApi(payload, plataforma) {
  const p = (payload.platforms || []).find((x) => x.platform === plataforma);
  return p ? badge(p) : "";
}