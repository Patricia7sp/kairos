/**
 * Cliente da API do dashboard.
 *
 * Duas correções que a revisão da spec apurou e que valem no código:
 *  - o escopo de perfil vai por **query param**, não por header;
 *  - o tratamento de 401 é **seletivo por código de erro**, não global.
 */

export const PROFILE_QUERY_PARAM = "profile";

/**
 * Códigos de erro que significam "reautentique".
 *
 * Tratar todo 401 como sessão expirada derruba o usuário por causa de um
 * endpoint que exige escopo maior — e ensina a reautenticar por reflexo, que
 * é o hábito que um phishing explora.
 */
export const REAUTH_ERROR_CODES = new Set([
  "session_expired",
  "invalid_session",
  "token_revoked",
]);

export interface ApiError {
  status: number;
  code?: string;
  message?: string;
}

export function withProfile(path: string, profile?: string | null): string {
  if (!profile) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}${PROFILE_QUERY_PARAM}=${encodeURIComponent(profile)}`;
}

/** Só um 401 com código conhecido derruba a sessão. */
export function shouldReauthenticate(err: ApiError): boolean {
  if (err.status !== 401) return false;
  if (!err.code) return false;
  return REAUTH_ERROR_CODES.has(err.code);
}

/**
 * Backoff de reconexão do stream de eventos, com jitter.
 *
 * Sem jitter, todas as abas abertas reconectam no mesmo milissegundo depois
 * de uma queda do servidor — e a rajada derruba o servidor de novo, no exato
 * momento em que ele está subindo.
 */
export const RECONNECT_BASE_MS = 500;
export const RECONNECT_MAX_MS = 30_000;

export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  if (attempt < 0) throw new RangeError(`attempt não pode ser negativo: ${attempt}`);
  const exp = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
  // Jitter total (0..exp), não parcial: escalona melhor sob muitas abas.
  return Math.floor(exp * random());
}
