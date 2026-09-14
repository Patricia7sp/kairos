/* Chat central da SPA sobre o protocolo versionado do InteractionService. */

import { api } from "../api.js";
import { ChatClient } from "../chat-client.js";
import { mergeSelectionParameters } from "../selection-parameters.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

export function initialTurnState() {
  return { status: "idle", text: "", reasoning: "", tools: [], usage: null, cost: null, error: null };
}

const validCost = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const costLabel = (cost) => {
  const actual = validCost(cost?.actual_usd);
  const amount = actual ? cost.actual_usd : cost?.estimated_usd;
  if (!validCost(amount)) return "Custo desconhecido";
  const value = amount > 0 && amount < 0.00000001 ? "< US$ 0,00000001"
    : `US$ ${amount.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 8 })}`;
  return `Custo ${actual ? "informado" : "estimado"}: ${value}`;
};

const routingLabel = (selection) => {
  if (selection.provider !== "openrouter") return "Não se aplica a este provedor.";
  const routing = selection.parameters?.routing || {};
  return `Coleta de dados: ${routing.data_collection === "allow" ? "permitida" : "negada"} · `
    + `Parâmetros obrigatórios: ${routing.require_parameters === false ? "não" : "sim"} · `
    + `Fallbacks: ${routing.allow_fallbacks === false ? "não" : "sim"}`
    + (routing.data_collection === "allow" ? "" : ". Exclui endpoints que coletam dados para treinamento.");
};

const searchKey = (sessionId) => `kairos.chat.web-search.${sessionId}`;
const loadSearch = (sessionId) => {
  try { return sessionStorage.getItem(searchKey(sessionId)) === "true"; } catch { return false; }
};
const storeSearch = (sessionId, enabled) => {
  try { sessionStorage.setItem(searchKey(sessionId), String(enabled)); } catch { /* Storage may be disabled. */ }
};

const draftKey = (sessionId) => `kairos.chat.draft.${sessionId}`;
const storeDraft = (sessionId, selection) => {
  try { sessionStorage.setItem(draftKey(sessionId), JSON.stringify(selection)); } catch { /* Storage may be disabled. */ }
};
const newSelection = (route, models) => {
  const provider = route.provider || models.default_provider;
  const model = route.model || models.default_model;
  let draft = null;
  try { draft = JSON.parse(sessionStorage.getItem(draftKey(route.sessionId)) || "null"); } catch { /* Ignore invalid drafts. */ }
  const matches = draft?.provider === provider && draft?.model === model
    && (!route.profile || draft.profile === route.profile);
  const profileName = route.profile || (matches ? draft.profile : "");
  const profile = (models.profiles || []).find((item) => item.name === profileName
    && item.provider === provider && item.model === model);
  return {
    provider, model,
    parameters: mergeSelectionParameters(models.default_parameters, profile?.parameters, matches ? draft.parameters : null),
    ...(profile || (matches && draft.profile) ? { profile: matches && draft.profile ? draft.profile : profile.name } : {}),
  };
};

export function reduceTurn(state, event) {
  if (!event || event.protocol !== 1) return state;
  if (event.type === "turn_start") {
    return { ...initialTurnState(), status: "streaming", selection: {
      provider: event.provider, model: event.model, reason: event.selection_reason,
    } };
  }
  if (event.type === "delta") return { ...state, text: state.text + String(event.text || "") };
  if (event.type === "reasoning_delta") {
    return { ...state, reasoning: state.reasoning + String(event.reasoning || event.text || "") };
  }
  if (event.type === "tool_call" && event.tool_call) {
    return { ...state, tools: [...state.tools, {
      id: event.tool_call.id, name: event.tool_call.name, status: "running",
    }] };
  }
  if (event.type === "tool_approval_request" && event.tool_call && event.approval_id) {
    const call = event.tool_call;
    const approval = {
      id: call.id, name: call.name, status: "awaiting",
      approvalId: event.approval_id, arguments: String(call.arguments ?? ""),
    };
    const running = state.tools.some((tool) => tool.id === call.id
      && (tool.status === "running" || tool.status === "awaiting"));
    return { ...state, tools: running
      ? state.tools.map((tool) => tool.id === call.id
          && (tool.status === "running" || tool.status === "awaiting") ? approval : tool)
      : [...state.tools, approval] };
  }
  if (event.type === "tool_result" && event.tool_result) {
    return { ...state, tools: state.tools.map((tool) =>
      tool.id === event.tool_result.tool_call_id
        && (tool.status === "running" || tool.status === "awaiting")
        ? { ...tool, status: event.tool_result.is_error ? "error" : "done",
          content: String(event.tool_result.content ?? "") } : tool) };
  }
  if (event.type === "usage") return { ...state, usage: event.usage || null, cost: event.cost || null };
  if (event.type === "turn_error") {
    return { ...state, status: "error", error: event.error || event.error_kind || "Falha no turno." };
  }
  if (event.type === "turn_end") return { ...state, status: "done", finishReason: event.finish_reason };
  return state;
}

const selectedProvider = (providers, selection) => providers.find((provider) =>
  (provider.id || provider.provider) === selection.provider);

export function newConversationId(cryptoImpl = globalThis.crypto) {
  if (typeof cryptoImpl?.randomUUID === "function") return cryptoImpl.randomUUID();
  if (typeof cryptoImpl?.getRandomValues !== "function") {
    throw new Error("O navegador não oferece geração segura de identificadores.");
  }
  const bytes = cryptoImpl.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const sessionsNavMarkup = (sessions) => `${sessions.map((session) => session.execution_kind === "agent_runtime"
  ? `<a class="k-chat__session" href="#/runtime?session=${encodeURIComponent(session.id)}">
    <strong>${esc(session.title || "Sessão Codex")}</strong><small>Agent Runtime · ${esc(session.runtime_state || "")}</small></a>`
  : `<div class="k-chat__session-row">
    <button class="k-chat__session" type="button" data-session-id="${esc(session.id)}"
      aria-label="Abrir conversa ${esc(session.title || "sem título")}">
      <strong>${esc(session.title || "Sem título")}</strong><small>${esc(session.model || "")}</small></button>
    <details class="k-chat__session-menu" data-session-menu="${esc(session.id)}">
      <summary aria-label="Opções da conversa ${esc(session.title || "sem título")}">⋯</summary>
      <ul role="menu" aria-label="Ações da conversa">
        <li><button type="button" role="menuitem" data-menu-action="rename">Renomear</button></li>
        <li><button type="button" role="menuitem" data-menu-action="archive">Arquivar</button></li>
        <li><button type="button" role="menuitem" data-menu-action="delete" class="k-chat__menu--perigo">Excluir</button></li>
      </ul>
    </details>
  </div>`
).join("") || "<p>Nenhuma conversa ainda.</p>"}`;

export function chatShellMarkup({
  providers = [], sessions = [], models = [], selection = {}, connected = false,
}) {
  const selected = models.find((model) =>
    model.provider === selection.provider && model.id === selection.model);
  const provider = selectedProvider(providers, selection);
  const disabled = provider?.configured !== true || !connected;
  return `<div class="k-chat" data-chat>
    <aside class="k-chat__sessions" aria-label="Conversas">
      <button class="k-btn k-btn--primary" type="button" data-new-chat>Nova conversa</button>
      <nav data-session-list>${sessionsNavMarkup(sessions)}</nav>
    </aside>
    <section class="k-chat__conversation" aria-label="Conversa">
      <div class="k-chat__messages" data-chat-messages aria-live="polite"></div>
      <div class="k-chat__blocked" role="status" data-chat-readiness>${provider?.configured === true
        ? "Aguardando conexão do Chat."
        : `O provider ${esc(selection.provider || "selecionado")} não está configurado.
          <a href="#/provedores">Configurar um provedor</a>`}</div>
      <form class="k-chat__composer" data-chat-form>
        <label class="k-sr" for="chat-message">Mensagem</label>
        <textarea ${disabled ? "disabled" : ""} id="chat-message" name="content" rows="3"
          placeholder="Escreva uma mensagem"></textarea>
        <button class="k-btn k-btn--primary" ${disabled ? "disabled" : ""} type="submit">Enviar</button>
        <div class="k-chat__search">
          <label><input type="checkbox" name="web_search" ${disabled ? "disabled" : ""}
            aria-describedby="chat-search-notice"> Buscar na web</label>
          <small id="chat-search-notice">A consulta será enviada a um buscador externo.
            Escolha lembrada por conversa neste navegador, nesta aba.</small>
        </div>
      </form>
      <p class="k-chat__live" aria-live="polite" data-chat-status></p>
    </section>
    <aside class="k-chat__context" aria-label="Contexto do modelo">
      <h2>Contexto</h2>
      <dl><dt>Provider</dt><dd data-context-provider>${esc(selection.provider || "—")}</dd>
        <dt>Modelo</dt><dd data-context-model>${esc(selected?.name || selection.model || "—")}</dd>
        <dt>Preço de catálogo</dt><dd data-context-price>${selected?.is_free ? "Gratuito" : "Conforme catálogo"}</dd>
        <dt>Capacidades</dt><dd data-context-capabilities>${selected?.capabilities?.tools ? "Ferramentas" : "Chat"}</dd>
        <dt>Último turno</dt><dd data-context-cost>Nenhum turno nesta conversa.</dd>
        <dt>Perfil do próximo turno</dt><dd data-context-profile>${esc(selection.profile || "Sem perfil")}</dd>
        <dt>Roteamento do próximo turno</dt><dd data-context-routing>${esc(routingLabel(selection))}</dd></dl>
      <a class="k-btn k-btn--ghost" href="#/modelos" data-change-model>Trocar modelo</a>
    </aside>
  </div>`;
}

const searchResultMarkup = (content) => {
  let result;
  try { result = JSON.parse(content); } catch { /* Legacy and malformed output remains plain text. */ }
  if (typeof result?.error === "string") return `<p>${esc(result.error)}</p>`;
  if (!Array.isArray(result?.results) || !result.results.every((item) => item
      && [item.title, item.url, item.snippet].every((value) => typeof value === "string"))) {
    return `<pre>${esc(content)}</pre>`;
  }
  const query = typeof result.query === "string" ? `<p>Consulta: ${esc(result.query)}</p>` : "";
  if (!result.results.length) return `${query}<p>Nenhum resultado encontrado.</p>`;
  return `${query}<ol>${result.results.map((item) => {
    let url;
    try {
      const candidate = new URL(item.url);
      if (["http:", "https:"].includes(candidate.protocol)) url = candidate.href;
    } catch { /* Unsupported links are displayed as text. */ }
    return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>`
      : `<strong>${esc(item.title)}</strong>`}<small>${esc(item.url)}</small><p>${esc(item.snippet)}</p></li>`;
  }).join("")}</ol>`;
};

const genericResultMarkup = (content) => {
  if (content == null) return "";
  if (typeof content !== "string") return `<pre>${esc(String(content))}</pre>`;
  let parsed;
  try { parsed = JSON.parse(content); } catch { /* Plain text stays as-is. */ }
  if (typeof parsed?.error === "string") return `<p>${esc(parsed.error)}</p>`;
  return `<pre>${esc(content)}</pre>`;
};

const toolLabel = (name) => name === "web_search" ? "Busca web" : name || "Ferramenta";

const approvalMarkup = (tool) => {
  const args = tool.arguments
    ? `<pre class="k-chat-tool__args">${esc(tool.arguments)}</pre>` : "";
  const pending = tool.status === "running"
    ? '<p class="k-chat-tool__pending">Aguardando execução…</p>'
    : `<div class="k-chat-tool__actions">
        <button type="button" class="k-btn k-btn--primary" data-approval="allow">Permitir</button>
        <button type="button" class="k-btn" data-approval="deny">Recusar</button>
      </div>`;
  return `<div class="k-chat-tool__approval" data-approval-id="${esc(tool.approvalId)}" data-tool="${esc(tool.id)}">
    <p><strong>Execução requer sua aprovação.</strong> A ferramenta <code>${esc(tool.name || "desconhecida")}</code>
      pode alterar arquivos ou o ambiente.</p>${args}${pending}</div>`;
};

const toolResultMarkup = (tool) => tool.name === "web_search"
  ? searchResultMarkup(tool.content)
  : genericResultMarkup(tool.content);

const toolStateLabel = (tool) => tool.status === "running"
  ? tool.approvalId ? "Aguardando execução…" : tool.name === "web_search" ? "Buscando…" : "Executando…"
  : tool.status === "awaiting" ? "Requer aprovação"
  : tool.status === "error" ? (tool.decision === "deny" ? "Recusada" : "Falha")
  : "Concluída";

const toolMarkup = (tool) => tool.status === "awaiting" || tool.status === "running" && tool.approvalId
  ? `<section class="k-chat-tool k-chat-tool--approval"><header>${esc(toolLabel(tool.name))} · ${toolStateLabel(tool)}</header>${approvalMarkup(tool)}</section>`
  : `<details class="k-chat-tool" ${tool.content == null ? "" : "open"}>
  <summary>${esc(toolLabel(tool.name))} · ${toolStateLabel(tool)}</summary>
  ${toolResultMarkup(tool)}</details>`;

const messageMarkup = (message) => message.role === "tool"
  ? `<article class="k-chat-message k-chat-message--tool">${toolMarkup({
    name: message.tool_name, content: message.content,
    status: message.is_error === true ? "error" : "done",
  })}</article>`
  : `<article class="k-chat-message k-chat-message--${esc(message.role)}">
  <header>${message.role === "user" ? "Você" : "Kairos"}</header><p>${esc(message.content)}</p>
  ${message.role === "assistant" && message.is_interrupted === true
    ? '<p class="k-error" role="status">Resposta interrompida.</p>' : ""}
  ${message.role === "assistant" ? `<small data-turn-cost>${esc(costLabel(message.cost))}</small>` : ""}</article>`;

export const turnMarkup = (turn) => `<article class="k-chat-message k-chat-message--assistant"${turn.status === "streaming" ? " data-active-turn" : ""}>
  <header>Kairos</header><p>${esc(turn.text)}${turn.status === "streaming" ? '<span class="k-chat__cursor" aria-hidden="true"></span>' : ""}</p>
  ${turn.reasoning ? `<details><summary>Raciocínio</summary><p>${esc(turn.reasoning)}</p></details>` : ""}
  ${turn.tools.map(toolMarkup).join("")}
  ${turn.error ? `<div class="k-error" role="alert">${esc(turn.error)}</div>` : ""}
  ${turn.usage ? `<small>${Number(turn.usage.total_tokens || 0).toLocaleString("pt-BR")} tokens</small>` : ""}
  <small data-turn-cost>${esc(costLabel(turn.cost))}</small>
  </article>`;

const parsedChatRoute = () => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  return {
    wantsNew: params.get("new") === "1",
    sessionId: params.get("session") || "",
    provider: params.get("provider") || "",
    model: params.get("model") || "",
    profile: params.get("profile") || "",
  };
};

const replaceHash = (hash) => history.replaceState(history.state, "", hash);

const draftHash = (sessionId, selection) => {
  const params = new URLSearchParams({
    provider: selection.provider,
    model: selection.model,
    new: "1",
    session: sessionId,
  });
  if (selection.profile) params.set("profile", selection.profile);
  return `#/chat?${params}`;
};

const modelsHash = (sessionId, selection, persisted) => {
  const params = persisted
    ? new URLSearchParams({ session: sessionId })
    : new URLSearchParams({
      provider: selection.provider, model: selection.model, new: "1", session: sessionId,
    });
  if (!persisted && selection.profile) params.set("profile", selection.profile);
  return `#/modelos?${params}`;
};

export async function chatView(root, _route, { signal } = {}) {
  let disposed = false;
  let client = null;
  let frame = null;
  let activeTurnNode = null;
  let generation = 0;
  let probeGeneration = 0;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    generation += 1;
    probeGeneration += 1;
    if (frame != null) cancelAnimationFrame(frame);
    frame = null;
    client?.close();
  };
  signal?.addEventListener("abort", dispose, { once: true });
  if (signal?.aborted) {
    dispose();
    return dispose;
  }

  const route = parsedChatRoute();
  root.innerHTML = `<div class="k-page-head"><h1>Chat</h1><p>Conversas sobre o mesmo serviço usado pelo terminal.</p></div>
    <div data-chat-load><div class="k-skeleton" style="height:60vh"></div></div>`;
  const load = root.querySelector("[data-chat-load]");
  const [providerPayload, modelPayload, sessionPayload] = await Promise.all([
    api.provedores(), api.modelos(), api.sessoes({ status: "abertas", limit: 50 }),
  ]);
  if (disposed || signal?.aborted) return dispose;
  let selection = newSelection(route.wantsNew ? route : {}, modelPayload);
  load.innerHTML = chatShellMarkup({
    providers: providerPayload.providers || [], sessions: sessionPayload.sessions || [],
    models: modelPayload.models || [], selection,
  });
  const chat = load.querySelector("[data-chat]");
  const messages = chat.querySelector("[data-chat-messages]");
  const form = chat.querySelector("[data-chat-form]");
  const status = chat.querySelector("[data-chat-status]");
  const readiness = chat.querySelector("[data-chat-readiness]");
  const changeModel = chat.querySelector("[data-change-model]");
  const firstModelSession = sessionPayload.sessions?.find((item) => item.execution_kind !== "agent_runtime");
  let sessionId = route.wantsNew
    ? (route.sessionId || newConversationId())
    : (route.sessionId || firstModelSession?.id || newConversationId());
  let persisted = !route.wantsNew && Boolean(route.sessionId || firstModelSession);
  let webSearch = loadSearch(sessionId);
  const searchControl = form.elements.web_search;
  let turn = initialTurnState();
  let socketConnected = false;
  let ready = false;
  let busy = false;
  let sessionLoading = false;
  let pendingAdmission = null;
  let activeStream = null;
  let hasActiveSelection = !persisted;
  let blockingLoadError = false;

  const updateNavigation = () => {
    if (!persisted) storeDraft(sessionId, selection);
    changeModel.href = modelsHash(sessionId, selection, persisted);
    changeModel.setAttribute("aria-disabled", String(busy));
    for (const button of chat.querySelectorAll("[data-session-id], [data-new-chat], [data-menu-action]")) {
      button.disabled = busy;
    }
    chat.querySelectorAll("[data-session-menu] > summary").forEach((summary) => {
      summary.setAttribute("aria-disabled", String(busy));
    });
  };
  const updateComposer = () => {
    const disabled = !socketConnected || !ready || busy || sessionLoading || blockingLoadError;
    form.elements.content.disabled = disabled;
    searchControl.disabled = disabled;
    searchControl.checked = webSearch;
    form.querySelector('button[type="submit"]').disabled = disabled;
    updateNavigation();
  };
  const showReadiness = (message, { retry = false, onRetry = null } = {}) => {
    readiness.replaceChildren();
    readiness.append(document.createTextNode(message));
    if (retry) {
      const button = document.createElement("button");
      button.className = "k-btn k-btn--ghost";
      button.type = "button";
      button.dataset.chatRetry = "";
      button.textContent = "Tentar novamente";
      readiness.append(" ", button);
      button.addEventListener("click", () => void (onRetry || probeSelectedProvider)());
    }
  };

  const renderTurn = (expectedGeneration = generation) => {
    frame = null;
    if (disposed || expectedGeneration !== generation) return;
    const template = document.createElement("template");
    template.innerHTML = turnMarkup(turn).trim();
    const rendered = template.content.firstElementChild;
    if (!activeTurnNode?.isConnected) {
      messages.append(rendered);
      activeTurnNode = rendered;
    } else {
      activeTurnNode.className = rendered.className;
      if (rendered.hasAttribute("data-active-turn")) activeTurnNode.setAttribute("data-active-turn", "");
      else activeTurnNode.removeAttribute("data-active-turn");
      activeTurnNode.innerHTML = rendered.innerHTML;
    }
    messages.scrollTop = messages.scrollHeight;
    chat.querySelector("[data-context-cost]").textContent = costLabel(turn.cost);
  };
  const scheduleTurn = () => {
    if (!disposed && frame == null) {
      const expectedGeneration = generation;
      frame = requestAnimationFrame(() => renderTurn(expectedGeneration));
    }
  };
  const flushTurn = () => {
    if (frame != null) cancelAnimationFrame(frame);
    frame = null;
    renderTurn(generation);
  };
  const resetTurnRendering = () => {
    if (frame != null) cancelAnimationFrame(frame);
    frame = null;
    turn = initialTurnState();
    activeTurnNode = null;
  };
  const restorePendingDraft = () => {
    if (!pendingAdmission) return;
    const pending = pendingAdmission;
    pendingAdmission = null;
    if (pending.sessionId !== sessionId || pending.generation !== generation || persisted) return;
    pending.userNode?.remove();
    form.elements.content.value = pending.content;
  };
  client = new ChatClient({
    onOpen: () => {
      if (disposed) return;
      socketConnected = true;
      updateComposer();
      if (!blockingLoadError) status.textContent = "Conectado.";
    },
    onAuthLost: () => { if (!disposed) location.reload(); },
    onClose: ({ reconnectable }) => {
      if (disposed) return;
      socketConnected = false;
      restorePendingDraft();
      activeStream = null;
      busy = false;
      updateComposer();
      status.textContent = reconnectable ? "Conexão interrompida; reabra o Chat." : "Conexão encerrada.";
    },
    onEvent: (event) => {
      if (disposed) return;
      if (event.session_id && (event.session_id !== activeStream?.sessionId
          || activeStream.generation !== generation)) return;
      if (event.type === "turn_start" && pendingAdmission
          && event.session_id === pendingAdmission.sessionId
          && pendingAdmission.generation === generation) {
        persisted = true;
        pendingAdmission = null;
        try { sessionStorage.removeItem(draftKey(sessionId)); } catch { /* Storage may be disabled. */ }
        replaceHash(`#/chat?session=${encodeURIComponent(sessionId)}`);
        updateNavigation();
      }
      turn = reduceTurn(turn, event);
      status.textContent = turn.status === "streaming" ? "Kairos está respondendo…"
        : turn.status === "error" ? "A resposta parcial foi preservada." : "";
      if (turn.status === "done" || turn.status === "error") {
        flushTurn();
        if (turn.status === "error") restorePendingDraft();
        busy = false;
        updateComposer();
      } else {
        scheduleTurn();
      }
    },
  });

  const syncSelection = () => {
    chat.querySelector("[data-context-provider]").textContent = selection.provider || "—";
    const selected = (modelPayload.models || []).find((model) =>
      model.provider === selection.provider && model.id === selection.model);
    chat.querySelector("[data-context-model]").textContent = selected?.name || selection.model || "—";
    chat.querySelector("[data-context-price]").textContent = selected?.is_free
      ? "Gratuito" : "Conforme catálogo";
    chat.querySelector("[data-context-capabilities]").textContent = selected?.capabilities?.tools
      ? "Ferramentas" : "Chat";
    chat.querySelector("[data-context-routing]").textContent = routingLabel(selection);
    chat.querySelector("[data-context-profile]").textContent = selection.profile || "Sem perfil";
    updateNavigation();
  };

  const probeSelectedProvider = async () => {
    const currentProbe = ++probeGeneration;
    const provider = selectedProvider(providerPayload.providers || [], selection);
    ready = false;
    if (!provider || provider.configured !== true) {
      showReadiness(`O provider ${selection.provider || "selecionado"} não está configurado.`);
      updateComposer();
      return;
    }
    if (provider.requires_credential) {
      ready = true;
      showReadiness("Credencial configurada; a conexão será verificada ao enviar.");
      updateComposer();
      return;
    }
    showReadiness(`Testando conexão com ${provider.name || provider.id}…`);
    updateComposer();
    try {
      const result = await api.testarProvedor(provider.id || provider.provider);
      if (disposed || signal?.aborted || currentProbe !== probeGeneration) return;
      ready = result.connected === true;
      showReadiness(result.message || (ready ? `${provider.name || provider.id} conectado.`
        : `${provider.name || provider.id} indisponível.`), { retry: !ready });
    } catch (error) {
      if (disposed || signal?.aborted || currentProbe !== probeGeneration) return;
      ready = false;
      showReadiness(error.message || "Falha ao testar a conexão.", { retry: true });
    }
    updateComposer();
  };

  const loadSession = async (id) => {
    if (disposed || busy) return;
    const currentGeneration = ++generation;
    const canRestoreActiveSelection = hasActiveSelection;
    resetTurnRendering();
    activeStream = null;
    probeGeneration += 1;
    sessionLoading = true;
    ready = false;
    updateComposer();
    try {
      const [detail, payload] = await Promise.all([api.sessao(id), api.mensagens(id)]);
      if (disposed || signal?.aborted || currentGeneration !== generation) return;
      sessionId = id;
      webSearch = loadSearch(sessionId);
      persisted = true;
      selection = detail.selection || { provider: "", model: "", parameters: {} };
      const profile = (modelPayload.profiles || []).find((item) => item.name === selection.profile
        && item.provider === selection.provider && item.model === selection.model);
      selection = { ...selection, parameters: mergeSelectionParameters(
        modelPayload.default_parameters, profile?.parameters, selection.parameters,
      ) };
      hasActiveSelection = Boolean(selection.provider && selection.model);
      blockingLoadError = false;
      messages.innerHTML = (payload.messages || []).map(messageMarkup).join("");
      const latestAssistant = [...(payload.messages || [])].reverse().find((message) => message.role === "assistant");
      chat.querySelector("[data-context-cost]").textContent = latestAssistant
        ? costLabel(latestAssistant.turn_cost ?? latestAssistant.cost) : "Nenhum turno nesta conversa.";
      activeTurnNode = null;
      replaceHash(`#/chat?session=${encodeURIComponent(id)}`);
      syncSelection();
      await probeSelectedProvider();
      if (disposed || signal?.aborted || currentGeneration !== generation) return;
      sessionLoading = false;
      status.textContent = socketConnected ? "Conectado." : "";
      updateComposer();
    } catch (error) {
      if (disposed || signal?.aborted || currentGeneration !== generation) return;
      sessionLoading = false;
      status.textContent = error.message || "Falha ao carregar a conversa.";
      if (canRestoreActiveSelection) {
        blockingLoadError = false;
        await probeSelectedProvider();
      } else {
        blockingLoadError = true;
        ready = false;
        showReadiness(status.textContent, { retry: true, onRetry: () => loadSession(id) });
        updateComposer();
      }
    }
  };

  const reloadSessions = async () => {
    const frescas = await api.sessoes({ status: "abertas", limit: 50 });
    if (disposed || signal?.aborted) return;
    chat.querySelector("[data-session-list]").innerHTML = sessionsNavMarkup(frescas.sessions || []);
  };

  const startNewConversation = () => {
    generation += 1;
    probeGeneration += 1;
    sessionLoading = false;
    resetTurnRendering();
    activeStream = null;
    sessionId = newConversationId();
    webSearch = false;
    persisted = false;
    hasActiveSelection = true;
    blockingLoadError = false;
    status.textContent = socketConnected ? "Conectado." : "";
    selection = newSelection({}, modelPayload);
    messages.innerHTML = "";
    chat.querySelector("[data-context-cost]").textContent = "Nenhum turno nesta conversa.";
    replaceHash(draftHash(sessionId, selection));
    syncSelection();
    void probeSelectedProvider();
    form.elements.content.focus();
  };

  const handleSessionMenu = async (id, action) => {
    if (busy) return;
    try {
      if (action === "rename") {
        const atual = (sessionPayload.sessions || []).find((session) => session.id === id);
        const resposta = prompt("Renomear conversa:", atual?.title || "");
        if (resposta === null) return;
        const nome = resposta.trim().slice(0, 120);
        await api.renomearSessao(id, nome || null);
        await reloadSessions();
      } else if (action === "archive") {
        await api.atualizarSessao(id, { archived: true });
        await reloadSessions();
      } else if (action === "delete") {
        const confirma = confirm("Excluir esta conversa? As mensagens serão apagadas e esta ação não pode ser desfeita.");
        if (!confirma) return;
        await api.excluirSessao(id);
        await reloadSessions();
      }
      if (sessionId === id && (action === "archive" || action === "delete")) {
        status.textContent = socketConnected ? "Conectado." : "";
        startNewConversation();
        return;
      }
      status.textContent = socketConnected ? "Conectado." : "";
    } catch (error) {
      status.textContent = error.message || "Falha ao atualizar a conversa.";
    }
    updateComposer();
  };

  if (persisted) {
    await loadSession(sessionId);
    if (disposed || signal?.aborted) return dispose;
  } else {
    replaceHash(draftHash(sessionId, selection));
    syncSelection();
    await probeSelectedProvider();
    if (disposed || signal?.aborted) return dispose;
  }

  const ticket = await api.wsTicket();
  if (disposed || signal?.aborted) return dispose;
  client.connect({ token: ticket.ticket });

  chat.querySelector("[data-session-list]").addEventListener("click", (event) => {
    if (busy) return;
    const itemMenu = event.target.closest("[data-menu-action]");
    if (itemMenu) {
      const linha = itemMenu.closest("[data-session-menu]");
      event.preventDefault();
      void handleSessionMenu(linha.dataset.sessionMenu, itemMenu.dataset.menuAction);
      return;
    }
    const button = event.target.closest("[data-session-id]");
    if (button && !busy) void loadSession(button.dataset.sessionId);
  });
  messages.addEventListener("click", (event) => {
    const button = event.target.closest("[data-approval]");
    if (!button) return;
    const card = button.closest("[data-approval-id]");
    const approvalId = card?.dataset.approvalId;
    const toolId = card?.dataset.tool;
    const decision = button.dataset.approval;
    if (!approvalId || !toolId || !["allow", "deny"].includes(decision)) return;
    turn = { ...turn, tools: turn.tools.map((tool) =>
      tool.id === toolId && tool.status === "awaiting"
        ? { ...tool, status: "running", decision,
          approvalId, arguments: tool.arguments } : tool) };
    scheduleTurn();
    api.decidirFerramentaChat(sessionId, approvalId, decision)
      .catch((error) => { status.textContent = error.message || "Falha ao confirmar a ferramenta."; });
  });
  chat.querySelector("[data-new-chat]").addEventListener("click", () => {
    if (busy) return;
    startNewConversation();
  });
  changeModel.addEventListener("click", (event) => {
    if (busy) event.preventDefault();
  });
  searchControl.addEventListener("change", () => {
    if (searchControl.disabled) {
      searchControl.checked = webSearch;
      return;
    }
    webSearch = searchControl.checked;
    storeSearch(sessionId, webSearch);
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (busy || !socketConnected || !ready) return;
    const content = form.elements.content.value.trim();
    if (!content) return;
    busy = true;
    updateComposer();
    messages.insertAdjacentHTML("beforeend", messageMarkup({ role: "user", content }));
    const userNode = messages.lastElementChild;
    form.elements.content.value = "";
    turn = initialTurnState();
    activeTurnNode = null;
    if (!persisted) {
      pendingAdmission = { sessionId, content, userNode, generation };
    }
    activeStream = { sessionId, generation };
    const selectedModel = (modelPayload.models || []).find((model) =>
      model.provider === selection.provider && model.id === selection.model);
    const toolsEnabled = selectedModel?.capabilities?.tools === true;
    try {
      client.sendMessage({
        sessionId, content, provider: selection.provider, model: selection.model,
        webSearch, tools: toolsEnabled,
        profile: selection.profile,
        parameters: Object.keys(selection.parameters || {}).length ? selection.parameters : undefined,
      });
    } catch (error) {
      restorePendingDraft();
      activeStream = null;
      busy = false;
      status.textContent = error.message || "Falha ao enviar a mensagem.";
      updateComposer();
    }
  });
  updateComposer();
  return dispose;
}
