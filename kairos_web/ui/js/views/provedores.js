/* Configuração segura de providers; nenhum identificador de segredo é renderizado. */

import { api } from "../api.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

const stateLabel = (provider) => {
  if (!provider.requires_credential) return "Sem credencial necessária";
  if (provider.credential_state === "locked") return "Cofre bloqueado";
  return provider.configured ? "Credencial configurada" : "Não configurado";
};

export function providerCardMarkup(provider) {
  const id = esc(provider.id || provider.provider);
  const configured = provider.configured === true;
  const authMethods = Array.isArray(provider.auth_methods) ? provider.auth_methods : [];
  return `<article class="k-card k-provider" data-provider="${id}">
    <header class="k-provider__head">
      <div><h2>${esc(provider.name || provider.provider)}</h2>
        <p>${esc(stateLabel(provider))}</p></div>
      <span class="k-badge ${configured ? "k-badge--ok" : "k-badge--warn"}">
        ${configured ? "disponível" : "pendente"}
      </span>
    </header>
    <div class="k-provider__status" aria-live="polite" data-provider-status></div>
    ${provider.requires_credential ? `<form class="k-provider__credential" data-credential-form>
      <label for="credential-${id}">Chave de API</label>
      <div class="k-provider__actions">
        <input id="credential-${id}" name="secret" type="password" autocomplete="off"
               required aria-describedby="credential-help-${id}">
        <button class="k-btn k-btn--primary" type="submit">${configured ? "Substituir" : "Salvar"}</button>
      </div>
      <small id="credential-help-${id}">A chave vai diretamente para o cofre e não volta para esta tela.</small>
    </form>` : `<p class="k-provider__local">Sem credencial necessária</p>`}
    <div class="k-provider__actions">
      <button class="k-btn k-btn--ghost" type="button" data-test-provider>Testar conexão</button>
      <button class="k-btn k-btn--ghost" type="button" data-refresh-provider>Atualizar catálogo</button>
    </div>
    ${authMethods.length ? `<details><summary>Métodos aceitos</summary><p>${authMethods.map(esc).join(", ")}</p></details>` : ""}
  </article>`;
}

function status(card, message, kind = "") {
  const target = card.querySelector("[data-provider-status]");
  target.className = `k-provider__status ${kind ? `k-provider__status--${kind}` : ""}`;
  target.textContent = message;
}

function bindProviderCard(card, provider) {
  const form = card.querySelector("[data-credential-form]");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.elements.secret;
    const secret = input.value;
    input.value = "";
    if (provider.configured && !window.confirm("Substituir a credencial armazenada?")) return;
    status(card, "Salvando…");
    try {
      await api.salvarCredencial(provider.id, secret);
      provider.configured = true;
      provider.credential_state = "configured";
      status(card, "Credencial salva no cofre.", "ok");
    } catch (error) {
      status(card, error.message || "Falha ao salvar a credencial.", "error");
    }
  });

  card.querySelector("[data-test-provider]").addEventListener("click", async () => {
    status(card, "Testando conexão…");
    try {
      const result = await api.testarProvedor(provider.id);
      status(card, result.message || (result.connected ? "Conectado." : "Indisponível."),
        result.connected ? "ok" : "error");
    } catch (error) {
      status(card, error.message || "Falha ao testar conexão.", "error");
    }
  });

  card.querySelector("[data-refresh-provider]").addEventListener("click", async () => {
    status(card, "Atualizando catálogo…");
    try {
      const result = await api.atualizarCatalogo(provider.id);
      status(card, `${result.models.length} modelos · origem ${result.source}.`, "ok");
    } catch (error) {
      status(card, error.message || "Falha ao atualizar catálogo.", "error");
    }
  });
}

export async function provedoresView(root) {
  root.innerHTML = `<div class="k-page-head"><h1>Provedores</h1>
    <p>Conexões, credenciais e atualização do catálogo de modelos.</p></div>
    <div class="k-provider-grid" data-provider-list>
      <div class="k-skeleton" style="height:220px"></div>
    </div>`;
  const list = root.querySelector("[data-provider-list]");
  try {
    const { providers = [] } = await api.provedores();
    list.innerHTML = providers.map(providerCardMarkup).join("");
    for (const provider of providers) {
      const card = list.querySelector(`[data-provider="${CSS.escape(provider.id)}"]`);
      if (card) bindProviderCard(card, provider);
    }
  } catch (error) {
    list.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar os provedores</h2>
      <p>${esc(error.message || error)}</p></div>`;
  }
}
