/* Chat central da SPA sobre o protocolo versionado do InteractionService. */

import { api } from "../api.js";
import { ChatClient } from "../chat-client.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

export function initialTurnState() {
  return { status: "idle", text: "", reasoning: "", tools: [], usage: null, error: null };
}

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
  if (event.type === "tool_result" && event.tool_result) {
    return { ...state, tools: state.tools.map((tool) =>
      tool.id === event.tool_result.tool_call_id
        ? { ...tool, status: event.tool_result.is_error ? "error" : "done" } : tool) };
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
      <nav data-session-list>${sessions.map((session) => session.execution_kind === "agent_runtime"
        ? `<a class="k-chat__session" href="#/runtime?session=${encodeURIComponent(session.id)}">
          <strong>${esc(session.title || "Sessão Codex")}</strong><small>Agent Runtime · ${esc(session.runtime_state || "")}</small></a>`
        : `<button class="k-chat__session" type="button" data-session-id="${esc(session.id)}">
          <strong>${esc(session.title || "Sem título")}</strong><small>${esc(session.model || "")}</small></button>`
      ).join("") || "<p>Nenhuma conversa ainda.</p>"}</nav>
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
      </form>
      <p class="k-chat__live" aria-live="polite" data-chat-status></p>
    </section>
    <aside class="k-chat__context" aria-label="Contexto do modelo">
      <h2>Contexto</h2>
      <dl><dt>Provider</dt><dd data-context-provider>${esc(selection.provider || "—")}</dd>
        <dt>Modelo</dt><dd data-context-model>${esc(selected?.name || selection.model || "—")}</dd>
        <dt>Preço</dt><dd data-context-price>${selected?.is_free ? "Gratuito" : "Conforme catálogo"}</dd>
        <dt>Capacidades</dt><dd data-context-capabilities>${selected?.capabilities?.tools ? "Ferramentas" : "Chat"}</dd></dl>
      <a class="k-btn k-btn--ghost" href="#/modelos" data-change-model>Trocar modelo</a>
    </aside>
  </div>`;
}

const messageMarkup = (message) => `<article class="k-chat-message k-chat-message--${esc(message.role)}">
  <header>${message.role === "user" ? "Você" : "Kairos"}</header><p>${esc(message.content)}</p></article>`;

export const turnMarkup = (turn) => `<article class="k-chat-message k-chat-message--assistant"${turn.status === "streaming" ? " data-active-turn" : ""}>
  <header>Kairos</header><p>${esc(turn.text)}${turn.status === "streaming" ? '<span class="k-chat__cursor" aria-hidden="true"></span>' : ""}</p>
  ${turn.reasoning ? `<details><summary>Raciocínio</summary><p>${esc(turn.reasoning)}</p></details>` : ""}
  ${turn.tools.map((tool) => `<div class="k-chat-tool">${esc(tool.name)} · ${esc(tool.status)}</div>`).join("")}
  ${turn.error ? `<div class="k-error" role="alert">${esc(turn.error)}</div>` : ""}
  ${turn.usage ? `<small>${Number(turn.usage.total_tokens || 0).toLocaleString("pt-BR")} tokens</small>` : ""}
  </article>`;

const parsedChatRoute = () => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  return {
    wantsNew: params.get("new") === "1",
    sessionId: params.get("session") || "",
    provider: params.get("provider") || "",
    model: params.get("model") || "",
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
  return `#/chat?${params}`;
};

const modelsHash = (sessionId, selection, persisted) => {
  const params = persisted
    ? new URLSearchParams({ session: sessionId })
    : new URLSearchParams({
      provider: selection.provider, model: selection.model, new: "1", session: sessionId,
    });
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
  let selection = route.wantsNew && route.provider && route.model
    ? { provider: route.provider, model: route.model, parameters: {} }
    : { provider: modelPayload.default_provider, model: modelPayload.default_model, parameters: {} };
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
  let turn = initialTurnState();
  let socketConnected = false;
  let ready = false;
  let busy = false;
  let sessionLoading = false;
  let pendingAdmission = null;
  let activeStream = null;

  const updateNavigation = () => {
    changeModel.href = modelsHash(sessionId, selection, persisted);
    changeModel.setAttribute("aria-disabled", String(busy));
    for (const button of chat.querySelectorAll("[data-session-id], [data-new-chat]")) {
      button.disabled = busy;
    }
  };
  const updateComposer = () => {
    const disabled = !socketConnected || !ready || busy || sessionLoading;
    form.elements.content.disabled = disabled;
    form.querySelector('button[type="submit"]').disabled = disabled;
    updateNavigation();
  };
  const showReadiness = (message, { retry = false } = {}) => {
    readiness.replaceChildren();
    readiness.append(document.createTextNode(message));
    if (retry) {
      const button = document.createElement("button");
      button.className = "k-btn k-btn--ghost";
      button.type = "button";
      button.dataset.chatRetry = "";
      button.textContent = "Tentar novamente";
      readiness.append(" ", button);
      button.addEventListener("click", () => void probeSelectedProvider());
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
      status.textContent = "Conectado.";
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
      persisted = true;
      selection = detail.selection || { provider: "", model: "", parameters: {} };
      messages.innerHTML = (payload.messages || []).map(messageMarkup).join("");
      activeTurnNode = null;
      replaceHash(`#/chat?session=${encodeURIComponent(id)}`);
      syncSelection();
      await probeSelectedProvider();
      if (disposed || signal?.aborted || currentGeneration !== generation) return;
      sessionLoading = false;
      updateComposer();
    } catch (error) {
      if (disposed || signal?.aborted || currentGeneration !== generation) return;
      sessionLoading = false;
      status.textContent = error.message || "Falha ao carregar a conversa.";
      await probeSelectedProvider();
    }
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
    const button = event.target.closest("[data-session-id]");
    if (button && !busy) void loadSession(button.dataset.sessionId);
  });
  chat.querySelector("[data-new-chat]").addEventListener("click", () => {
    if (busy) return;
    generation += 1;
    probeGeneration += 1;
    sessionLoading = false;
    resetTurnRendering();
    activeStream = null;
    sessionId = newConversationId();
    persisted = false;
    selection = {
      provider: modelPayload.default_provider,
      model: modelPayload.default_model,
      parameters: {},
    };
    messages.innerHTML = "";
    replaceHash(draftHash(sessionId, selection));
    syncSelection();
    void probeSelectedProvider();
    form.elements.content.focus();
  });
  changeModel.addEventListener("click", (event) => {
    if (busy) event.preventDefault();
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
    try {
      client.sendMessage({
        sessionId, content, provider: selection.provider, model: selection.model,
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
