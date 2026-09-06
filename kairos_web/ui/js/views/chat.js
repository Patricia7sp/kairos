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

const usableProviders = (providers) => providers.filter((provider) => provider.configured === true);

export function chatShellMarkup({
  providers = [], sessions = [], models = [], selection = {}, connected = false,
}) {
  const usable = usableProviders(providers);
  const selected = models.find((model) =>
    model.provider === selection.provider && model.id === selection.model);
  const disabled = usable.length === 0 || !connected;
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
      ${disabled ? `<div class="k-chat__blocked" role="status">Nenhum provider utilizável.
        <a href="#/provedores">Configurar um provedor</a></div>` : ""}
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
      <dl><dt>Provider</dt><dd>${esc(selection.provider || "—")}</dd>
        <dt>Modelo</dt><dd>${esc(selected?.name || selection.model || "—")}</dd>
        <dt>Preço</dt><dd>${selected?.is_free ? "Gratuito" : "Conforme catálogo"}</dd>
        <dt>Capacidades</dt><dd>${selected?.capabilities?.tools ? "Ferramentas" : "Chat"}</dd></dl>
      <a class="k-btn k-btn--ghost" href="#/modelos">Trocar modelo</a>
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

export async function chatView(root) {
  root.innerHTML = `<div class="k-page-head"><h1>Chat</h1><p>Conversas sobre o mesmo serviço usado pelo terminal.</p></div>
    <div data-chat-load><div class="k-skeleton" style="height:60vh"></div></div>`;
  const load = root.querySelector("[data-chat-load]");
  const [providerPayload, modelPayload, sessionPayload] = await Promise.all([
    api.provedores(), api.modelos(), api.sessoes({ status: "abertas", limit: 50 }),
  ]);
  const selection = {
    provider: modelPayload.default_provider,
    model: modelPayload.default_model,
  };
  load.innerHTML = chatShellMarkup({
    providers: providerPayload.providers || [], sessions: sessionPayload.sessions || [],
    models: modelPayload.models || [], selection,
  });
  const chat = load.querySelector("[data-chat]");
  const messages = chat.querySelector("[data-chat-messages]");
  const form = chat.querySelector("[data-chat-form]");
  const status = chat.querySelector("[data-chat-status]");
  const firstModelSession = sessionPayload.sessions?.find((item) => item.execution_kind !== "agent_runtime");
  let sessionId = firstModelSession?.id || crypto.randomUUID();
  let turn = initialTurnState();
  let frame = null;

  const setComposerConnected = (connected) => {
    if (!usableProviders(providerPayload.providers || []).length) return;
    form.elements.content.disabled = !connected;
    form.querySelector('button[type="submit"]').disabled = !connected;
  };

  const renderTurn = () => {
    frame = null;
    const previous = messages.querySelector("[data-active-turn]");
    if (previous) previous.outerHTML = turnMarkup(turn);
    else messages.insertAdjacentHTML("beforeend", turnMarkup(turn));
    messages.scrollTop = messages.scrollHeight;
  };
  const scheduleTurn = () => {
    if (frame == null) frame = requestAnimationFrame(renderTurn);
  };
  const client = new ChatClient({
    onOpen: () => {
      setComposerConnected(true);
      status.textContent = "Conectado.";
    },
    onAuthLost: () => location.reload(),
    onClose: ({ reconnectable }) => {
      setComposerConnected(false);
      status.textContent = reconnectable ? "Conexão interrompida; reabra o Chat." : "Conexão encerrada.";
    },
    onEvent: (event) => {
      turn = reduceTurn(turn, event);
      status.textContent = turn.status === "streaming" ? "Kairos está respondendo…"
        : turn.status === "error" ? "A resposta parcial foi preservada." : "";
      scheduleTurn();
    },
  });

  const ticket = await api.wsTicket();
  client.connect({ token: ticket.ticket });

  const loadSession = async (id) => {
    sessionId = id;
    const payload = await api.mensagens(id);
    messages.innerHTML = payload.messages.map(messageMarkup).join("");
  };
  if (firstModelSession) await loadSession(sessionId);
  chat.querySelector("[data-session-list]").addEventListener("click", (event) => {
    const button = event.target.closest("[data-session-id]");
    if (button) loadSession(button.dataset.sessionId);
  });
  chat.querySelector("[data-new-chat]").addEventListener("click", () => {
    sessionId = crypto.randomUUID();
    messages.innerHTML = "";
    form.elements.content.focus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const content = form.elements.content.value.trim();
    if (!content) return;
    messages.insertAdjacentHTML("beforeend", messageMarkup({ role: "user", content }));
    form.elements.content.value = "";
    turn = initialTurnState();
    client.sendMessage({
      sessionId, content, provider: selection.provider, model: selection.model,
    });
  });
}
