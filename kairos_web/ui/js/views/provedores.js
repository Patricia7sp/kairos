/* Configuração segura de providers; nenhum identificador de segredo é renderizado. */

import { api } from "../api.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

const stateLabel = (provider) => {
  if (!provider.requires_credential) return "Sem credencial necessária";
  if (provider.credential_source === "external") return "Credencial externa · somente leitura";
  if (provider.credential_state === "locked") return "Cofre bloqueado";
  return provider.configured ? "Credencial configurada" : "Não configurado";
};

const initialConnectionLabel = (provider) => {
  if (provider.credential_state === "locked") return "Cofre bloqueado";
  if (provider.requires_credential && !provider.configured) return "Não configurado";
  return "Conexão não testada";
};

function settingsMarkup(provider) {
  if (!["custom", "openrouter"].includes(provider.id)) return "";
  const custom = provider.id === "custom";
  const id = esc(provider.id);
  return `<button type="button" class="k-btn k-btn--ghost" data-edit-provider-settings
      aria-expanded="false" aria-controls="settings-${id}">Configurações avançadas</button>
    <form id="settings-${id}" data-provider-settings-form hidden>
      <fieldset disabled><legend>${custom ? "Endpoint compatível com OpenAI" : "Atribuição OpenRouter"}</legend>
        ${custom ? `<label for="base-${id}">URL base</label>
          <input id="base-${id}" name="base_url" type="url" required placeholder="https://api.exemplo.com/v1">
          <label for="models-${id}">URL do catálogo de modelos</label>
          <input id="models-${id}" name="models_url" type="url" required>
          <small>O catálogo deve usar a mesma origem da URL base, sem credenciais ou parâmetros na URL.</small>
          <label><input name="trusted_remote" type="checkbox">Confio neste servidor remoto para receber minhas mensagens e credenciais.</label>
          <label data-transfer-consent hidden><input name="confirm_transfer" type="checkbox">
            Autorizo enviar a credencial já armazenada para a nova origem: <strong data-transfer-origin></strong></label>` : ""}
        <label for="referer-${id}">Site de atribuição (opcional)</label>
        <input id="referer-${id}" name="referer" type="url" placeholder="https://meu-site.com" maxlength="2048">
        <label for="title-${id}">Nome do aplicativo (opcional)</label>
        <input id="title-${id}" name="title" type="text" maxlength="128">
        <small>Estes campos são públicos. Não insira chaves, tokens ou outros segredos.</small>
        <button type="submit" class="k-btn k-btn--primary">Salvar configurações</button>
      </fieldset>
      <p data-settings-status role="status" aria-live="polite"></p>
    </form>`;
}

export function providerCardMarkup(provider) {
  const id = esc(provider.id || provider.provider);
  const configured = provider.configured === true;
  const authMethods = Array.isArray(provider.auth_methods) ? provider.auth_methods : [];
  return `<article class="k-card k-provider" data-provider="${id}">
    <header class="k-provider__head">
      <div><h2>${esc(provider.name || provider.provider)}</h2>
        <p data-provider-credential-state>${esc(stateLabel(provider))}</p></div>
      <span class="k-badge k-badge--warn" data-provider-connection>
        ${esc(initialConnectionLabel(provider))}
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
      <button type="button" class="k-btn k-btn--ghost" data-remove-credential
        ${provider.can_remove_credential === true ? "" : "hidden"}>Remover credencial do cofre</button>
    </form>` : `<p class="k-provider__local">Sem credencial necessária</p>`}
    <div class="k-provider__actions">
      <button class="k-btn k-btn--ghost" type="button" data-test-provider>Testar conexão</button>
      <button class="k-btn k-btn--ghost" type="button" data-refresh-provider>Atualizar catálogo</button>
    </div>
    ${settingsMarkup(provider)}
    ${authMethods.length ? `<details><summary>Métodos aceitos</summary><p>${authMethods.map(esc).join(", ")}</p></details>` : ""}
  </article>`;
}

function status(card, message, kind = "") {
  const target = card.querySelector("[data-provider-status]");
  target.className = `k-provider__status ${kind ? `k-provider__status--${kind}` : ""}`;
  target.textContent = message;
}

function connectionStatus(card, message, kind = "warn") {
  const badge = card.querySelector("[data-provider-connection]");
  badge.className = `k-badge ${kind ? `k-badge--${kind}` : ""}`;
  badge.textContent = message;
}

function bindProviderCard(card, provider, isActive) {
  let credentialRevision = 0;
  let probeRevision = 0;
  let savingCredential = false;
  let savingSettings = false;
  let refreshRevision = 0;
  const testButton = card.querySelector("[data-test-provider]");
  const refreshButton = card.querySelector("[data-refresh-provider]");
  const form = card.querySelector("[data-credential-form]");
  const removeButton = card.querySelector("[data-remove-credential]");

  async function mutateCredential(remove = false) {
    if (savingCredential || savingSettings || !isActive()) return;
    const input = form.elements.secret;
    const secret = input.value;
    if (remove) {
      if (!provider.can_remove_credential || !window.confirm(
        "Remover a credencial principal do cofre? Outras credenciais e fontes externas serão preservadas."
      )) return;
    } else if (provider.configured && !window.confirm("Substituir a credencial armazenada?")) return;
    const saveButton = form.querySelector('button[type="submit"]');
    savingCredential = true;
    input.value = "";
    input.disabled = true;
    saveButton.disabled = true;
    if (removeButton) removeButton.disabled = true;
    credentialRevision += 1;
    probeRevision += 1;
    refreshRevision += 1;
    const revision = credentialRevision;
    testButton.disabled = true;
    refreshButton.disabled = true;
    connectionStatus(card, "Conexão não testada");
    status(card, remove ? "Removendo credencial…" : "Salvando…");
    let mutated = false;
    try {
      if (remove) await api.removerCredencial(provider.id);
      else await api.salvarCredencial(provider.id, secret);
      mutated = true;
      if (!isActive() || revision !== credentialRevision) return;
      const result = await api.provedores();
      if (!isActive() || revision !== credentialRevision) return;
      const current = result.providers.find((item) => item.id === provider.id);
      if (!current) throw new Error("Provedor ausente na atualização do estado.");
      Object.assign(provider, current);
      card.querySelector("[data-provider-credential-state]").textContent = stateLabel(provider);
      saveButton.textContent = provider.configured ? "Substituir" : "Salvar";
      if (removeButton) removeButton.hidden = provider.can_remove_credential !== true;
      connectionStatus(card, initialConnectionLabel(provider));
      status(card, remove ? "Credencial removida do cofre. Outras credenciais e fontes externas foram preservadas."
        : "Credencial salva no cofre.", "ok");
    } catch (error) {
      if (!isActive() || revision !== credentialRevision) return;
      if (mutated) {
        provider.can_remove_credential = false;
        if (removeButton) removeButton.hidden = true;
        card.querySelector("[data-provider-credential-state]").textContent = "Estado não atualizado";
        status(card, "Alteração concluída, mas o estado não pôde ser atualizado. Reabra Provedores para consultar.", "error");
      } else {
        status(card, error.body?.detail || error.message || "Falha ao alterar a credencial.", "error");
      }
    } finally {
      if (isActive() && revision === credentialRevision) {
        savingCredential = false;
        input.disabled = false;
        saveButton.disabled = false;
        if (removeButton) removeButton.disabled = false;
        testButton.disabled = false;
        refreshButton.disabled = false;
      }
    }
  }
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    mutateCredential();
  });
  removeButton?.addEventListener("click", () => mutateCredential(true));

  testButton.addEventListener("click", async () => {
    if (savingCredential || savingSettings || !isActive()) return;
    const testedCredentialRevision = credentialRevision;
    const revision = ++probeRevision;
    status(card, "Testando conexão…");
    try {
      const result = await api.testarProvedor(provider.id);
      if (!isActive() || revision !== probeRevision ||
          testedCredentialRevision !== credentialRevision) return;
      connectionStatus(card, result.connected ? "Conexão verificada" : "Falha na conexão",
        result.connected ? "ok" : "error");
      status(card, result.message || (result.connected ? "Conectado." : "Indisponível."),
        result.connected ? "ok" : "error");
    } catch (error) {
      if (!isActive() || revision !== probeRevision ||
          testedCredentialRevision !== credentialRevision) return;
      connectionStatus(card, "Falha na conexão", "error");
      status(card, error.message || "Falha ao testar conexão.", "error");
    }
  });

  refreshButton.addEventListener("click", async () => {
    if (savingCredential || savingSettings || !isActive()) return;
    const revision = ++refreshRevision;
    status(card, "Atualizando catálogo…");
    try {
      const result = await api.atualizarCatalogo(provider.id);
      if (!isActive() || revision !== refreshRevision) return;
      status(card, `${result.models.length} modelos · origem ${result.source}.`, "ok");
    } catch (error) {
      if (!isActive() || revision !== refreshRevision) return;
      status(card, error.message || "Falha ao atualizar catálogo.", "error");
    }
  });

  const settingsForm = card.querySelector("[data-provider-settings-form]");
  if (!settingsForm) return;
  const editButton = card.querySelector("[data-edit-provider-settings]");
  const fieldset = settingsForm.querySelector("fieldset");
  const settingsStatus = settingsForm.querySelector("[data-settings-status]");
  const fields = settingsForm.elements;
  let loadedSettings = null;
  let loading = false;
  let settingsRevision = 0;
  let previousBase = "";
  const custom = provider.id === "custom";
  const origin = (url) => { try { return new URL(url).origin; } catch { return ""; } };
  const changedOrigin = () => custom && loadedSettings &&
    origin(fields.base_url.value) !== origin(loadedSettings.base_url);

  function fillSettings(settings) {
    loadedSettings = settings;
    if (custom) {
      fields.base_url.value = settings.base_url;
      fields.models_url.value = settings.models_url;
      fields.trusted_remote.checked = settings.trusted_remote === true;
      previousBase = settings.base_url;
      fields.confirm_transfer.checked = false;
      settingsForm.querySelector("[data-transfer-consent]").hidden = true;
    }
    const headers = Object.fromEntries(Object.entries(settings.headers || {}).map(([key, value]) => [key.toLowerCase(), value]));
    fields.referer.value = (custom ? headers["http-referer"] : settings.referer) || "";
    fields.title.value = (custom ? headers["x-title"] : settings.title) || "";
  }

  editButton.addEventListener("click", async () => {
    if (!isActive()) return;
    settingsForm.hidden = !settingsForm.hidden;
    editButton.setAttribute("aria-expanded", String(!settingsForm.hidden));
    if (settingsForm.hidden || loadedSettings || loading) return;
    loading = true;
    settingsStatus.textContent = "Carregando configurações…";
    const revision = ++settingsRevision;
    try {
      const result = await api.configuracaoProvedor(provider.id);
      if (!isActive() || revision !== settingsRevision) return;
      fillSettings(result.settings);
      fieldset.disabled = false;
      settingsStatus.textContent = "";
    } catch (error) {
      if (!isActive() || revision !== settingsRevision) return;
      settingsStatus.textContent = error.message || "Falha ao carregar configurações.";
    } finally {
      if (isActive() && revision === settingsRevision) loading = false;
    }
  });

  if (custom) fields.base_url.addEventListener("input", () => {
    if (fields.models_url.value === `${previousBase.replace(/\/$/, "")}/models`) {
      fields.models_url.value = `${fields.base_url.value.replace(/\/$/, "")}/models`;
    }
    previousBase = fields.base_url.value;
    fields.confirm_transfer.checked = false;
    settingsForm.querySelector("[data-transfer-consent]").hidden = !changedOrigin();
    settingsForm.querySelector("[data-transfer-origin]").textContent = origin(fields.base_url.value);
  });

  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!isActive() || !loadedSettings || savingSettings || savingCredential) return;
    if (changedOrigin() && !fields.confirm_transfer.checked) {
      settingsStatus.textContent = "Confirme o envio da credencial armazenada para a nova origem.";
      return;
    }
    const referer = fields.referer.value.trim();
    const title = fields.title.value.trim();
    const settings = custom ? {
      base_url: fields.base_url.value.trim(), models_url: fields.models_url.value.trim(),
      trusted_remote: fields.trusted_remote.checked,
      headers: { ...(referer ? { "HTTP-Referer": referer } : {}), ...(title ? { "X-Title": title } : {}) },
    } : { referer: referer || null, title: title || null };
    const confirmTransfer = custom && fields.confirm_transfer.checked;
    const revision = ++settingsRevision;
    savingSettings = true;
    probeRevision += 1;
    refreshRevision += 1;
    fieldset.disabled = true;
    testButton.disabled = true;
    refreshButton.disabled = true;
    if (form) for (const field of form.elements) field.disabled = true;
    connectionStatus(card, "Conexão não testada");
    settingsStatus.textContent = "Salvando configurações…";
    try {
      const result = await api.salvarConfiguracaoProvedor(provider.id, settings, confirmTransfer);
      if (!isActive() || revision !== settingsRevision) return;
      fillSettings(result.settings);
      settingsStatus.textContent = "Configurações salvas. Valem para as próximas mensagens. Atualize o catálogo e teste a conexão.";
      status(card, "Configurações salvas.", "ok");
    } catch (error) {
      if (!isActive() || revision !== settingsRevision) return;
      settingsStatus.textContent = error.body?.detail || error.message || "Falha ao salvar configurações.";
    } finally {
      if (isActive() && revision === settingsRevision) {
        savingSettings = false;
        fieldset.disabled = false;
        testButton.disabled = false;
        refreshButton.disabled = false;
        if (form) for (const field of form.elements) field.disabled = false;
      }
    }
  });
}

export async function provedoresView(root, _route, { signal } = {}) {
  let disposed = false;
  const dispose = () => { disposed = true; };
  const isActive = () => !disposed && !signal?.aborted;
  signal?.addEventListener("abort", dispose, { once: true });
  if (!isActive()) return dispose;
  root.innerHTML = `<div class="k-page-head"><h1>Provedores</h1>
    <p>Conexões, credenciais e atualização do catálogo de modelos.</p></div>
    <div class="k-provider-grid" data-provider-list>
      <div class="k-skeleton" style="height:220px"></div>
    </div>`;
  const list = root.querySelector("[data-provider-list]");
  try {
    const { providers = [] } = await api.provedores();
    if (!isActive()) return dispose;
    list.innerHTML = providers.map(providerCardMarkup).join("");
    for (const provider of providers) {
      const card = list.querySelector(`[data-provider="${CSS.escape(provider.id)}"]`);
      if (card) bindProviderCard(card, provider, isActive);
    }
  } catch (error) {
    if (!isActive()) return dispose;
    list.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar os provedores</h2>
      <p>${esc(error.message || error)}</p></div>`;
  }
  return dispose;
}
