/* Painel do Agent Runtime Codex. Todo valor vindo do runtime entra no DOM por
 * textContent; os innerHTML abaixo contêm apenas estrutura estática. */

import { api, ApiError } from "../api.js";
import { RuntimeClient } from "../runtime-client.js";

const labelSandbox = {
  read_only: "Somente leitura",
  workspace_write: "Escrita no projeto",
  broad_access: "Acesso amplo",
};

const node = (tag, className, text) => {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
};

const featuresOf = (value) => new Set(
  Array.isArray(value) ? value : (value?.features || []),
);

const itemId = (payload, fallback) => String(
  payload?.itemId || payload?.item_id || payload?.item?.id || fallback,
);

const upsertItem = (items, incoming) => {
  const index = items.findIndex((item) => item.id === incoming.id);
  if (index < 0) return [...items, incoming];
  return items.map((item, position) => position === index ? { ...item, ...incoming } : item);
};

const snapshotItems = (snapshot) => (Array.isArray(snapshot?.items) ? snapshot.items : [])
  .map((item, index) => ({
    id: String(item?.id || item?.itemId || `snapshot-${index}`),
    type: String(item?.type || item?.kind || "output"),
    text: typeof item?.text === "string" ? item.text : undefined,
    value: item,
  }));

const reconcileItems = (items, snapshot) => snapshotItems(snapshot)
  .reduce((merged, item) => upsertItem(merged, item), items);

export function initialRuntimeState(sessionId, capabilities = []) {
  return {
    sessionId,
    cursor: null,
    lastSequence: 0,
    items: [],
    approvals: [],
    status: "idle",
    capabilities: [...featuresOf(capabilities)],
    usage: null,
    activeTurnId: null,
    gap: false,
    reconciled: false,
    queue: [],
  };
}

export function reduceRuntime(state, event) {
  if (!event || event.execution_kind !== "agent_runtime" || event.protocol_version !== 1
      || event.session_id !== state.sessionId || !Number.isInteger(event.sequence)) return state;
  if (event.sequence <= state.lastSequence) return state;
  if (state.lastSequence > 0 && event.sequence !== state.lastSequence + 1) {
    return { ...state, gap: true, status: "reconciling" };
  }

  const payload = event.payload || {};
  let next = { ...state, cursor: event.cursor, lastSequence: event.sequence };
  if (event.kind === "text" || event.kind === "reasoning") {
    const id = itemId(payload, `${event.kind}-${event.sequence}`);
    const previous = state.items.find((item) => item.id === id);
    const text = payload.replace === true
      ? String(payload.delta || payload.item?.text || "")
      : String(previous?.text || "") + String(payload.delta || payload.item?.text || "");
    next.items = upsertItem(state.items, {
      id, type: event.kind, text,
      value: payload.item || previous?.value || null,
    });
  } else if (event.kind === "tool") {
    const value = payload.item || payload;
    next.items = upsertItem(state.items, {
      id: itemId(payload, `tool-${event.sequence}`), type: "tool", value,
      text: typeof payload.delta === "string" ? payload.delta : undefined,
    });
  } else if (event.kind === "output") {
    if (Array.isArray(payload.items)) next.items = snapshotItems(payload);
    else if (typeof payload.text === "string") {
      next.items = upsertItem(state.items, {
        id: `output-${event.sequence}`, type: "output", text: payload.text, value: payload,
      });
    }
  } else if (event.kind === "snapshot") {
    next.items = snapshotItems(payload);
  } else if (event.kind === "reconciled") {
    next.items = reconcileItems(state.items, payload.snapshot);
    next.status = payload.snapshot?.state || state.status;
    next.reconciled = true;
    next.gap = state.gap;
  } else if (event.kind === "approval_request" || event.kind === "approval") {
    const approval = {
      id: String(payload.approval_id || payload.request_id || event.event_id),
      kind: String(payload.request_kind || "aprovação"),
      details: payload.details || {},
    };
    next.approvals = [...state.approvals.filter((item) => item.id !== approval.id), approval];
  } else if (["approval_decision", "approval_delivery", "approval_rejected"].includes(event.kind)) {
    const id = String(payload.approval_id || "");
    next.approvals = state.approvals.filter((item) => item.id !== id);
  } else if (event.kind === "turn_start") {
    next.status = "running";
    next.activeTurnId = event.turn_id;
    next.queue = state.queue.filter((turnId) => turnId !== event.turn_id);
  } else if (event.kind === "turn_state") {
    next.status = payload.state || state.status;
    next.queue = payload.state === "queued" && !state.queue.includes(event.turn_id)
      ? [...state.queue, event.turn_id] : state.queue;
  } else if (event.kind === "turn_end") {
    next.status = payload.state || "completed";
    next.activeTurnId = null;
    next.usage = payload.usage || { status: "unknown" };
    next.queue = state.queue.filter((turnId) => turnId !== event.turn_id);
  } else if (event.kind === "usage") {
    next.usage = { status: "known", value: { tokenUsage: payload.tokenUsage } };
  } else if (event.kind === "error") {
    next.status = "error";
  }
  return next;
}

export function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value, globalThis.location?.href || "http://localhost/");
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed : null;
  } catch {
    return null;
  }
}

const appendMeta = (root, label, value) => {
  const wrap = node("div");
  wrap.append(node("dt", "", label), node("dd", "", value ?? "—"));
  root.append(wrap);
};

const toolText = (item) => {
  try {
    return JSON.stringify(item.value ?? item.text ?? {}, null, 2);
  } catch {
    return String(item.text || "Ferramenta");
  }
};

const usageText = (usage) => {
  if (!usage || usage.status !== "known") return "Uso desconhecido";
  const tokens = usage.value?.tokenUsage?.total?.totalTokens
    ?? usage.value?.tokenUsage?.totalTokens;
  return typeof tokens === "number" ? `${tokens.toLocaleString("pt-BR")} tokens` : "Uso conhecido";
};

export function renderRuntimeSession(root, state, session, {
  connected = false,
  runtimeReady = true,
  error = null,
} = {}) {
  const previousComposer = root.dataset.runtimeSession === session.session_id
    ? root.querySelector("[data-runtime-composer] [name=content]")
    : null;
  const draft = previousComposer ? {
    value: previousComposer.value,
    focused: document.activeElement === previousComposer,
    selectionStart: previousComposer.selectionStart,
    selectionEnd: previousComposer.selectionEnd,
  } : null;
  root.replaceChildren();
  root.dataset.runtimeSession = session.session_id;
  const features = featuresOf(session?.capabilities || state.capabilities);
  const card = node("section", "k-runtime k-card");
  const heading = node("header", "k-runtime__head");
  const title = node("div");
  title.append(node("h2", "", "Sessão Codex"), node("p", "k-runtime__session-id", session.session_id));
  heading.append(title, node("span", connected ? "k-badge k-badge--ok" : "k-badge", connected ? "Conectado" : "Desconectado"));
  card.append(heading);

  const meta = node("dl", "k-det__meta");
  appendMeta(meta, "Modo", session.runtime_kind || "codex");
  appendMeta(meta, "Projeto fixado", session.canonical_cwd || session.cwd);
  appendMeta(meta, "Sandbox fixado", labelSandbox[session.sandbox] || session.sandbox);
  appendMeta(meta, "Estado", session.state || session.runtime_state || state.status);
  card.append(meta);

  if (state.gap) {
    card.append(node("div", "k-runtime__notice", state.reconciled
      ? "Lacuna detectada; snapshot reconciliado aplicado."
      : "Lacuna de sequência detectada; buscando reconciliação."));
  }
  if (error) {
    const message = error.error === "cancel_partial"
      ? "Cancelamento ainda não confirmado. A saída parcial foi preservada."
      : String(error.message || error.error || "Falha no runtime.");
    card.append(node("div", "k-error", message));
  }
  card.append(node("p", "k-runtime__queue", `Fila: ${state.queue.length}`));

  const transcript = node("div", "k-runtime__items");
  transcript.dataset.runtimeItems = "";
  for (const item of state.items) {
    const article = node("article", `k-runtime-item k-runtime-item--${item.type}`);
    article.append(node("header", "", item.type === "tool" ? "Ferramenta" : item.type));
    article.append(node("pre", "", item.type === "tool" ? toolText(item) : (item.text ?? toolText(item))));
    transcript.append(article);
  }
  if (!state.items.length) transcript.append(node("p", "k-runtime__empty", "Aguardando eventos desta sessão."));
  card.append(transcript);

  const approvals = node("section", "k-runtime__approvals");
  approvals.append(node("h3", "", "Aprovações pendentes"));
  for (const approval of state.approvals) {
    const row = node("div", "k-runtime-approval");
    row.dataset.runtimeApproval = approval.id;
    row.append(node("strong", "", approval.kind), node("pre", "", toolText({ value: approval.details })));
    const actions = node("div", "k-runtime__actions");
    for (const [decision, label] of [["accept", "Aceitar"], ["decline", "Negar"]]) {
      const button = node("button", `k-btn ${decision === "accept" ? "k-btn--primary" : "k-btn--ghost"}`, label);
      button.type = "button";
      button.dataset.runtimeDecision = decision;
      button.disabled = !connected || !runtimeReady || !features.has("approvals");
      actions.append(button);
    }
    row.append(actions);
    if (!connected) row.append(node("small", "", "Reconecte para responder."));
    approvals.append(row);
  }
  if (!state.approvals.length) approvals.append(node("p", "k-runtime__empty", "Nenhum pedido pendente."));
  card.append(approvals);

  card.append(node("p", "k-runtime__usage", usageText(state.usage)));
  const durableState = session.state || session.runtime_state;
  const interrupted = ["interrupted", "unavailable"].includes(durableState)
    || state.status === "interrupted";
  if (interrupted) {
    const recovery = node("div", "k-runtime__recovery");
    recovery.append(node("p", "", durableState === "unavailable"
      ? "A thread desta sessão está indisponível. O histórico permanece disponível."
      : "Esta sessão foi interrompida. O histórico permanece disponível."));
    const resume = node("button", "k-btn k-btn--primary", "Continuar nesta sessão");
    resume.type = "button";
    resume.dataset.runtimeResume = "";
    resume.disabled = durableState === "unavailable"
      || !connected || !runtimeReady || !features.has("resume");
    const successor = node("button", "k-btn k-btn--ghost", "Criar sessão sucessora");
    successor.type = "button";
    successor.dataset.runtimeSuccessor = "";
    successor.disabled = !runtimeReady;
    recovery.append(resume, successor);
    card.append(recovery);
  }

  const composer = node("form", "k-runtime__composer");
  composer.dataset.runtimeComposer = "";
  const textarea = node("textarea");
  textarea.name = "content";
  textarea.rows = 3;
  textarea.placeholder = "Envie uma instrução ao Codex";
  const send = node("button", "k-btn k-btn--primary", "Enviar");
  send.type = "submit";
  const canSend = connected && runtimeReady && features.has("text")
    && !["ended", "unavailable"].includes(durableState);
  textarea.disabled = !canSend;
  send.disabled = !canSend;
  composer.append(textarea, send);
  const cancel = node("button", "k-btn k-btn--ghost", "Cancelar turno");
  cancel.type = "button";
  cancel.dataset.runtimeCancel = "";
  cancel.disabled = !connected || !runtimeReady || !features.has("cancel") || !state.activeTurnId;
  composer.append(cancel);
  card.append(composer);
  root.append(card);
  if (draft) {
    textarea.value = draft.value;
    if (draft.focused && !textarea.disabled) {
      textarea.focus();
      textarea.setSelectionRange(draft.selectionStart, draft.selectionEnd);
    }
  }
}

export function renderRuntimeSetup(root, status, account, loginResult = null) {
  root.replaceChildren();
  const ready = status?.enabled === true && status?.state === "ready";
  const wrapper = node("div", "k-runtime-setup");
  const card = node("section", "k-card");
  card.append(node("h2", "", "Nova sessão Codex"));
  card.append(node("p", "", ready ? "Escolha um projeto autorizado e um perfil de sandbox." :
    status?.state === "disabled" ? "O Agent Runtime está desabilitado." : "O Agent Runtime está indisponível."));
  const form = node("form", "k-runtime-setup__form");
  form.dataset.runtimeCreateForm = "";
  const projectLabel = node("label", "", "Projeto autorizado");
  const project = node("select", "k-select");
  project.name = "cwd";
  for (const value of status?.authorized_projects || []) {
    const option = node("option", "", value);
    option.value = value;
    project.append(option);
  }
  projectLabel.append(project);
  const sandboxLabel = node("label", "", "Sandbox");
  const sandbox = node("select", "k-select");
  sandbox.name = "sandbox";
  for (const value of status?.sandbox_profiles || []) {
    const option = node("option", "", labelSandbox[value] || value);
    option.value = value;
    sandbox.append(option);
  }
  sandboxLabel.append(sandbox);
  const consent = node("label", "k-runtime__consent");
  consent.dataset.runtimeBroadConsent = "";
  const checkbox = node("input");
  checkbox.type = "checkbox";
  checkbox.name = "consent";
  consent.append(checkbox, document.createTextNode(" Confirmo que esta sessão pode acessar caminhos fora do projeto."));
  const create = node("button", "k-btn k-btn--primary", "Criar sessão");
  create.type = "submit";
  create.dataset.runtimeCreate = "";
  const update = () => {
    const broad = sandbox.value === "broad_access";
    consent.hidden = !broad;
    create.disabled = !ready || !project.value || !sandbox.value || (broad && !checkbox.checked);
  };
  sandbox.addEventListener("change", update);
  checkbox.addEventListener("change", update);
  checkbox.addEventListener("click", update);
  form.append(projectLabel, sandboxLabel, consent, create);
  card.append(form);
  wrapper.append(card);
  update();

  if (account?.requires_openai_auth) wrapper.append(runtimeAuthCard(account, loginResult));
  root.append(wrapper);
}

function runtimeAuthCard(account, loginResult) {
  const card = node("section", "k-card k-runtime-auth");
  card.append(node("h2", "", "Conta OpenAI do runtime"));
  if (account.authenticated) {
    card.append(node("p", "", `${account.email || "Conta autenticada"}${account.plan_type ? ` · ${account.plan_type}` : ""}`));
    const logout = node("button", "k-btn k-btn--ghost", "Sair da conta do runtime");
    logout.type = "button";
    logout.dataset.runtimeLogout = "";
    card.append(logout);
    return card;
  }
  const form = node("form", "k-runtime-auth__form");
  form.dataset.runtimeLoginForm = "";
  const method = node("select", "k-select");
  method.name = "method";
  for (const [value, label] of [["chatgpt", "Abrir no navegador"], ["chatgptDeviceCode", "Código do dispositivo"], ["apiKey", "Chave de API"]]) {
    const option = node("option", "", label);
    option.value = value;
    method.append(option);
  }
  const key = node("input", "k-input");
  key.type = "password";
  key.name = "api_key";
  key.autocomplete = "off";
  key.placeholder = "Chave de API (não será armazenada)";
  key.hidden = true;
  method.addEventListener("change", () => { key.hidden = method.value !== "apiKey"; });
  const submit = node("button", "k-btn k-btn--primary", "Entrar");
  submit.type = "submit";
  form.append(method, key, submit);
  card.append(form, node("p", "k-runtime__auth-state", `Estado: ${account.login_state}`));
  if (loginResult?.state === "pending") {
    if (loginResult.user_code) card.append(node("code", "k-runtime-auth__code", loginResult.user_code));
    const url = safeHttpUrl(loginResult.auth_url || loginResult.verification_url);
    if (url) {
      const link = node("a", "k-btn k-btn--ghost", "Continuar autenticação");
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
    }
    const cancel = node("button", "k-btn k-btn--ghost", "Cancelar login");
    cancel.type = "button";
    cancel.dataset.runtimeLoginCancel = loginResult.login_id;
    card.append(cancel);
  }
  return card;
}

const errorValue = (error) => {
  if (error instanceof ApiError) {
    return { ...(error.body || {}), message: error.body?.message || error.message };
  }
  return { message: String(error?.message || error) };
};

const hashSessionId = () => {
  const query = String(globalThis.location?.hash || "").split("?", 2)[1] || "";
  return new URLSearchParams(query).get("session");
};

const settled = (promise) => promise.then(
  (value) => ({ value }),
  (error) => ({ error }),
);

const persistedItems = (payload) => (payload?.messages || payload || []).map((message, index) => ({
  id: String(message?.id || `message-${index}`),
  type: String(message?.role || "message"),
  text: String(message?.content || ""),
  value: null,
  historical: true,
}));

export async function runtimeView(root, _route, { signal } = {}) {
  root.innerHTML = `<div class="k-page-head"><h1>Agent Runtime</h1>
    <p>Sessões locais do Codex com projeto, sandbox e aprovações explícitas.</p></div>
    <div data-runtime-root><div class="k-skeleton" style="height:55vh"></div></div>`;
  const mount = root.querySelector("[data-runtime-root]");
  let disposed = false;
  let client = null;
  let loginResult = null;
  let poll = null;
  let status;
  let account;
  const requested = hashSessionId();
  const statusLoad = settled(api.runtimeStatus());
  const accountLoad = settled(api.runtimeAccount());
  const sessionLoad = requested ? settled(api.sessao(requested)) : null;
  const messagesLoad = requested ? settled(api.mensagens(requested)) : null;

  const dispose = () => {
    disposed = true;
    client?.dispose();
    if (poll !== null) clearTimeout(poll);
    loginResult = null;
  };
  signal?.addEventListener("abort", dispose, { once: true });

  const statusResult = await statusLoad;
  status = statusResult.value || {
    enabled: true, state: "unavailable", authorized_projects: [], sandbox_profiles: [],
  };
  if (disposed) return dispose;
  if (!requested) {
    const accountResult = await accountLoad;
    account = accountResult.value || null;
    if (disposed) return dispose;
    const renderSetup = () => {
      renderRuntimeSetup(mount, status, account, loginResult);
      bindSetup();
    };
    const refreshAccount = async () => {
      try {
        account = await api.runtimeAccount();
        if (account.login_state !== "pending") {
          if (poll !== null) clearTimeout(poll);
          poll = null;
          loginResult = null;
        }
        if (!disposed) {
          renderSetup();
          if (account.login_state === "pending" && loginResult?.state === "pending") {
            poll = setTimeout(() => {
              poll = null;
              void refreshAccount();
            }, 1500);
          }
        }
      } catch (error) {
        if (!disposed) mount.append(node("div", "k-error", errorValue(error).message));
      }
    };
    const bindSetup = () => {
      mount.querySelector("[data-runtime-create-form]")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const button = form.querySelector("[data-runtime-create]");
        button.disabled = true;
        try {
          const created = await api.runtimeCreate({
            cwd: form.elements.cwd.value,
            sandbox: form.elements.sandbox.value,
            consent: form.elements.consent.checked,
          });
          if (!disposed) globalThis.location.hash = `#/runtime?session=${encodeURIComponent(created.session_id)}`;
        } catch (error) {
          if (!disposed) mount.append(node("div", "k-error", errorValue(error).message));
        }
      });
      mount.querySelector("[data-runtime-login-form]")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const method = form.elements.method.value;
        let key = form.elements.api_key.value;
        form.elements.api_key.value = "";
        try {
          const login = api.runtimeLogin(method, method === "apiKey" ? key : undefined);
          key = "";
          loginResult = await login;
          if (disposed) {
            loginResult = null;
            return;
          }
          await refreshAccount();
        } catch (error) {
          if (!disposed) mount.append(node("div", "k-error", errorValue(error).message));
        }
      });
      mount.querySelector("[data-runtime-login-cancel]")?.addEventListener("click", async (event) => {
        const loginId = event.currentTarget.dataset.runtimeLoginCancel;
        try {
          if (poll !== null) clearTimeout(poll);
          poll = null;
          await api.runtimeLoginCancel(loginId);
          loginResult = null;
          await refreshAccount();
        } catch (error) {
          if (!disposed) mount.append(node("div", "k-error", errorValue(error).message));
        }
      });
      mount.querySelector("[data-runtime-logout]")?.addEventListener("click", async () => {
        try {
          await api.runtimeLogout();
          await refreshAccount();
        } catch (error) {
          if (!disposed) mount.append(node("div", "k-error", errorValue(error).message));
        }
      });
    };
    renderSetup();
    const setupError = statusResult.error || accountResult.error;
    if (setupError) mount.append(node("div", "k-error", errorValue(setupError).message));
    return dispose;
  }

  const [sessionResult, messagesResult] = await Promise.all([sessionLoad, messagesLoad]);
  if (sessionResult.error) {
    mount.replaceChildren(node("div", "k-error", errorValue(sessionResult.error).message || "Sessão não encontrada."));
    return dispose;
  }
  let session = sessionResult.value;
  if (session.execution_kind !== "agent_runtime") {
    mount.replaceChildren(node("div", "k-error", "Esta sessão usa o runtime de modelo."));
    return dispose;
  }
  session = { ...session, session_id: session.id, state: session.runtime_state };
  let state = initialRuntimeState(session.session_id, session.capabilities);
  const runtimeReady = status.enabled === true && status.state === "ready";
  if (!runtimeReady && !messagesResult.error) {
    state = { ...state, items: persistedItems(messagesResult.value) };
  }
  let viewError = statusResult.error
    ? errorValue(statusResult.error)
    : messagesResult.error ? errorValue(messagesResult.error) : null;
  let reconciling = false;
  const render = () => {
    if (disposed) return;
    renderRuntimeSession(mount, state, session, {
      connected: Boolean(client?.socket && client.socket.readyState === 1),
      runtimeReady,
      error: viewError,
    });
    bindSession();
  };
  const run = async (operation) => {
    viewError = null;
    try {
      await operation();
    } catch (error) {
      viewError = errorValue(error);
    }
    render();
  };
  const bindSession = () => {
    mount.querySelector("[data-runtime-composer]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const content = form.elements.content.value.trim();
      if (!content) return;
      form.elements.content.value = "";
      void run(async () => {
        const accepted = await api.runtimeTurn(session.session_id, {
          content, idempotency_key: crypto.randomUUID(),
        });
        state = { ...state, queue: [...state.queue, accepted.turn_id] };
      });
    });
    mount.querySelector("[data-runtime-cancel]")?.addEventListener("click", () => {
      const turnId = state.activeTurnId;
      if (turnId) void run(() => api.runtimeCancel(session.session_id, turnId));
    });
    mount.querySelector("[data-runtime-resume]")?.addEventListener("click", () => {
      mount.querySelector("[name=content]")?.focus();
    });
    mount.querySelector("[data-runtime-successor]")?.addEventListener("click", () => void run(async () => {
      const created = await api.runtimeCreate({
        cwd: session.cwd || session.canonical_cwd,
        sandbox: session.sandbox,
        consent: session.sandbox === "broad_access" && session.broad_consent === true,
        parent_session_id: session.session_id,
      });
      if (!disposed) globalThis.location.hash = `#/runtime?session=${encodeURIComponent(created.session_id)}`;
    }));
    for (const row of mount.querySelectorAll("[data-runtime-approval]")) {
      row.addEventListener("click", (event) => {
        const button = event.target.closest("[data-runtime-decision]");
        if (!button || button.disabled) return;
        void run(async () => {
          await api.runtimeApprove(session.session_id, row.dataset.runtimeApproval, button.dataset.runtimeDecision);
          state = { ...state, approvals: state.approvals.filter((item) => item.id !== row.dataset.runtimeApproval) };
        });
      });
    }
  };
  client = new RuntimeClient({
    getCursor: () => state.cursor,
    onOpen: () => {
      reconciling = false;
      render();
    },
    onClose: render,
    onError: (error) => { viewError = errorValue(error); render(); },
    onEvent: (event) => {
      const previous = state;
      state = reduceRuntime(state, event);
      const sequenceGap = state !== previous && state.lastSequence === previous.lastSequence
        && event.sequence > previous.lastSequence + 1;
      if (sequenceGap && !reconciling) {
        reconciling = true;
        client.reconnect();
      }
      if (event.kind === "reconciled") reconciling = false;
      render();
    },
  });
  render();
  if (runtimeReady) {
    void client.connect(session.session_id).catch((error) => {
      viewError = errorValue(error);
      render();
    });
  }
  return dispose;
}
