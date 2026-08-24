/**
 * Endurecimento do renderer.
 *
 * **G-17 fechado aqui.** A verificação da lacuna apurou duas coisas com
 * desfechos opostos:
 *
 *  - o **bloqueio de navegação externa EXISTE** no legado e é robusto;
 *  - a **CSP estrita realmente NÃO existe** — zero ocorrências de
 *    `Content-Security-Policy` ou `onHeadersReceived` em `apps/desktop`.
 *
 * A CSP entra no Kairos como decisão. A defesa restante do legado —
 * `sandbox` + `contextIsolation` + `webSecurity` + origem `file:` — é boa,
 * mas não cobre o caso em que conteúdo renderizado tenta buscar um script
 * remoto: sem CSP, nada impede a requisição de sair.
 */

/** Origens que a janela principal pode carregar em produção. */
export const ALLOWED_LOCAL_SCHEMES = ["file:"] as const;

export interface CspOptions {
  /** Em dev, o servidor do Vite precisa de origem própria e de eval. */
  devServer?: string | null;
}

/**
 * A política. Restritiva por padrão, com relaxamento **explícito** só em dev.
 *
 * `default-src 'self'` mais um `connect-src` fechado é o que impede
 * exfiltração: mesmo que algo consiga executar script no renderer, não
 * consegue enviar nada para fora.
 */
export function buildContentSecurityPolicy(opts: CspOptions = {}): string {
  const dev = opts.devServer ?? null;

  const scriptSrc = ["'self'"];
  const connectSrc = ["'self'"];
  const styleSrc = ["'self'", "'unsafe-inline'"]; // estilos inline do React

  if (dev) {
    // Só em desenvolvimento: o HMR do Vite precisa de eval e de websocket.
    scriptSrc.push("'unsafe-eval'", dev);
    connectSrc.push(dev, dev.replace(/^http/, "ws"));
  }

  return [
    "default-src 'self'",
    `script-src ${scriptSrc.join(" ")}`,
    `style-src ${styleSrc.join(" ")}`,
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connectSrc.join(" ")}`,
    // Sem plugins, sem <object>, sem <embed>.
    "object-src 'none'",
    // Nada pode nos colocar num iframe, e nós não viramos base para URLs.
    "frame-ancestors 'none'",
    "base-uri 'none'",
    // Formulário que posta para fora é exfiltração com aparência de UI.
    "form-action 'none'",
  ].join("; ");
}

export interface NavigationDecision {
  action: "allow" | "deny-and-open-externally";
  reason: string;
}

/**
 * Decisão de navegação na janela atual.
 *
 * Só o dev server (em dev) ou `file:` (em produção) passam. Todo o resto é
 * impedido **e** desviado para o browser do sistema — negar sem abrir faria
 * links legítimos simplesmente não funcionarem, e o usuário não saberia por
 * quê.
 */
export function decideNavigation(url: string, devServer?: string | null): NavigationDecision {
  if (devServer && url.startsWith(devServer)) {
    return { action: "allow", reason: "dev server" };
  }
  if (!devServer && ALLOWED_LOCAL_SCHEMES.some((s) => url.startsWith(s))) {
    return { action: "allow", reason: "origem local em produção" };
  }
  return {
    action: "deny-and-open-externally",
    reason: "navegação externa: abre no browser do sistema",
  };
}

/** Janela nova: **sempre** negada, sempre desviada. */
export function decideWindowOpen(_url: string): NavigationDecision {
  return {
    action: "deny-and-open-externally",
    reason: "nenhuma janela Electron é aberta por conteúdo",
  };
}

/** As três flags de isolamento do renderer, obrigatórias em toda janela. */
export interface WebPreferences {
  contextIsolation: boolean;
  nodeIntegration: boolean;
  sandbox: boolean;
  webSecurity: boolean;
}

export const SECURE_WEB_PREFERENCES: WebPreferences = {
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
};

export function isSecure(prefs: Partial<WebPreferences>): boolean {
  return (
    prefs.contextIsolation === true &&
    prefs.nodeIntegration === false &&
    prefs.sandbox === true &&
    prefs.webSecurity !== false
  );
}

/**
 * Permissões do Chromium: **negadas por padrão**, exceto captura de mídia.
 *
 * O handler síncrono existe porque o Windows consulta esse caminho para
 * `getUserMedia` — e sem ele a checagem cai em `false` antes de o handler
 * assíncrono rodar, quebrando a captura só naquela plataforma.
 */
export const MEDIA_PERMISSIONS = new Set(["media", "audioCapture", "videoCapture"]);

export function allowPermission(permission: string): boolean {
  return MEDIA_PERMISSIONS.has(permission);
}
