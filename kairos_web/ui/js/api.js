/* Camada de acesso à API.
 *
 * Todo request passa por aqui por um motivo: o servidor responde 404 em JSON
 * para rota inexistente, e boa parte da API ainda não existe. Tratar isso em
 * cada view produziria telas em branco outra vez — que foi exatamente o
 * defeito que este projeto acabou de corrigir.
 */

const HEADER = "X-Kairos-Session-Token";

export function sessionToken() {
  return window.__KAIROS_SESSION_TOKEN__ || "";
}

export class ApiError extends Error {
  constructor(message, { status, path, body } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.body = body;
  }
  /** Rota que o servidor ainda não implementa — distinta de uma falha real. */
  get naoImplementado() {
    return this.status === 404 && this.body?.error === "not_found";
  }
}

async function request(path, { method = "GET", body, signal } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      ...(signal ? { signal } : {}),
      // O cookie de sessão é httpOnly e o navegador o envia sozinho em
      // same-origin. O header só entra se alguém injetou um token na página —
      // o caso da interface herdada, não o desta.
      credentials: "same-origin",
      headers: {
        ...(sessionToken() ? { [HEADER]: sessionToken() } : {}),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (causa) {
    throw new ApiError("Sem resposta do servidor.", { path, body: { causa: String(causa) } });
  }

  const texto = await res.text();
  let dados = null;
  try {
    dados = texto ? JSON.parse(texto) : null;
  } catch {
    // HTML numa rota de API significa que ela caiu no catch-all da SPA.
    throw new ApiError("O servidor respondeu algo que não é JSON.", {
      status: res.status,
      path,
      body: { trecho: texto.slice(0, 120) },
    });
  }

  if (!res.ok) {
    throw new ApiError(dados?.error || `HTTP ${res.status}`, {
      status: res.status,
      path,
      body: dados,
    });
  }
  return dados;
}

export const api = {
  agendamentos: () => request("/api/cron/jobs"),
  statusAgendamentos: () => request("/api/cron/status"),
  criarAgendamento: (body) => request("/api/cron/jobs", {method:"POST", body}),
  pausarAgendamento: (id, paused) => request(`/api/cron/jobs/${encodeURIComponent(id)}`, {method:"PATCH", body:{paused}}),
  excluirAgendamento: (id) => request(`/api/cron/jobs/${encodeURIComponent(id)}`, {method:"DELETE"}),
  historicoAgendamento: (id) => request(`/api/cron/jobs/${encodeURIComponent(id)}/history`),
  monitorAgendamento: (id) => request(`/api/cron/jobs/${encodeURIComponent(id)}/monitor`),
  definirMonitor: (id, script) => request(`/api/cron/jobs/${encodeURIComponent(id)}/monitor`, {method:"PUT", body:{script}}),
  removerMonitor: (id) => request(`/api/cron/jobs/${encodeURIComponent(id)}/monitor`, {method:"DELETE"}),
  rodarMonitor: (id) => request(`/api/cron/jobs/${encodeURIComponent(id)}/monitor/run`, {method:"POST"}),
  blocoNotas: (id) => request(`/api/cron/jobs/${encodeURIComponent(id)}/notepad`),
  definirNota: (id, key, value) => request(`/api/cron/jobs/${encodeURIComponent(id)}/notepad/${encodeURIComponent(key)}`, {method:"PUT", body:{value}}),
  removerNota: (id, key) => request(`/api/cron/jobs/${encodeURIComponent(id)}/notepad/${encodeURIComponent(key)}`, {method:"DELETE"}),
  blueprints: () => request("/api/cron/blueprints"),
  criarBlueprint: (key, values) => request(`/api/cron/blueprints/${encodeURIComponent(key)}/jobs`, {method:"POST", body:{values}}),
  alvosEntrega: () => request("/api/cron/delivery-targets"),
  quemSou: () => request("/api/auth/me"),
  logout:  () => request("/api/auth/logout", { method: "POST" }),

  health:      () => request("/api/health"),
  logs: ({ limit = 50, service, level, signal } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (service) params.set("service", service);
    if (level) params.set("level", level);
    return request(`/api/logs?${params}`, { signal });
  },
  status:      () => request("/api/status"),
  ajustes:     () => request("/api/settings"),
  salvarAjustes: (generation) => request("/api/settings", { method: "PUT", body: { generation } }),
  config:      () => request("/api/config"),
  salvarConfig: (config) => request("/api/config", { method: "PUT", body: { config } }),

  skills:        () => request("/api/skills"),
  skillConteudo: (name) => request(`/api/skills/content?name=${encodeURIComponent(name)}`),
  salvarSkill:   (name, content) => request("/api/skills/content", { method: "PUT", body: { name, content } }),
  alternarSkill: (name, enabled) => request("/api/skills/toggle", { method: "PUT", body: { name, enabled } }),

  sessoes:   (filtros = {}) => {
    const params = new URLSearchParams();
    if (filtros.q) params.set("q", filtros.q);
    if (filtros.status && filtros.status !== "todas") params.set("status", filtros.status);
    if (filtros.tag) params.set("tag", filtros.tag);
    if (filtros.offset) params.set("offset", String(filtros.offset));
    if (filtros.limit) params.set("limit", String(filtros.limit));
    const sufixo = params.toString();
    return request(`/api/sessions${sufixo ? "?" + sufixo : ""}`);
  },
  sessao:    (id) => request(`/api/sessions/${encodeURIComponent(id)}`),
  mensagens: (id) => request(`/api/sessions/${encodeURIComponent(id)}/messages`),
  atualizarSessao: (id, campos) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: campos }),
  renomearSessao: (id, displayName) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: { display_name: displayName } }),
  excluirSessao: (id) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  modelos:   (filtros = {}) => {
    const params = new URLSearchParams();
    if (filtros.provider) params.set("provider", filtros.provider);
    if (filtros.freeOnly) params.set("free_only", "true");
    if (filtros.includePreview) params.set("include_preview", "true");
    const sufixo = params.toString();
    return request(`/api/models${sufixo ? "?" + sufixo : ""}`);
  },
  atualizarCatalogo: (provider) => request("/api/models/refresh", {
    method: "POST", body: { provider },
  }),
  selecionarModelo: (selection) => request("/api/models/selection", {
    method: "POST", body: selection,
  }),
  provedores:() => request("/api/providers"),
  configuracaoProvedor: (provider) =>
    request(`/api/providers/${encodeURIComponent(provider)}/settings`),
  salvarConfiguracaoProvedor: (provider, settings, confirmCredentialTransfer = false) =>
    request(`/api/providers/${encodeURIComponent(provider)}/settings`, {
      method: "PUT", body: { settings, confirm_credential_transfer: confirmCredentialTransfer },
    }),
  salvarCredencial: (provider, secret, authMethod = "api_key") =>
    request(`/api/providers/${encodeURIComponent(provider)}/credentials`, {
      method: "POST", body: { secret, auth_method: authMethod },
    }),
  removerCredencial: (provider) =>
    request(`/api/providers/${encodeURIComponent(provider)}/credentials`, { method: "DELETE" }),
  testarProvedor: (provider) =>
    request(`/api/providers/${encodeURIComponent(provider)}/test`, { method: "POST" }),
  wsTicket: () => request("/api/auth/ws-ticket", { method: "POST" }),
  runtimeChanges: (sessionId) => request(`/api/runtime/sessions/${encodeURIComponent(sessionId)}/changes`),
  runtimeStatus:  () => request("/api/runtime/status"),
  runtimeAccount: () => request("/api/runtime/account"),
  runtimeLogin: (method, apiKey) => request("/api/runtime/login", {
    method: "POST", body: { method, ...(apiKey ? { api_key: apiKey } : {}) },
  }),
  runtimeLoginCancel: (loginId) => request("/api/runtime/login/cancel", {
    method: "POST", body: { login_id: loginId },
  }),
  runtimeLogout: () => request("/api/runtime/logout", { method: "POST" }),
  runtimeCreate: (session) => request("/api/runtime/sessions", {
    method: "POST", body: session,
  }),
  runtimeTurn: (sessionId, turn) => request(`/api/runtime/sessions/${encodeURIComponent(sessionId)}/turns`,
    { method: "POST", body: turn },
  ),
  runtimeEnd: (sessionId) => request(`/api/runtime/sessions/${encodeURIComponent(sessionId)}/end`,
    { method: "POST" },
  ),
  runtimeCancel: (sessionId, turnId) => request(`/api/runtime/sessions/${encodeURIComponent(sessionId)}/cancel`,
    { method: "POST", body: { turn_id: turnId } },
  ),
  runtimeApprove: (sessionId, approvalId, decision) => request(`/api/runtime/sessions/${encodeURIComponent(sessionId)}/approvals/${encodeURIComponent(approvalId)}`,
    { method: "POST", body: { decision } },
  ),
  toolsets:  () => request("/api/tools/toolsets"),
  uso:       () => request("/api/analytics/usage"),
};
