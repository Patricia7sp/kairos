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

async function request(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
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
  quemSou: () => request("/api/auth/me"),
  logout:  () => request("/api/auth/logout", { method: "POST" }),

  health:      () => request("/api/health"),
  status:      () => request("/api/status"),
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
  modelos:   () => request("/api/models"),
  provedores:() => request("/api/providers"),
  toolsets:  () => request("/api/tools/toolsets"),
  uso:       () => request("/api/analytics/usage"),
};
