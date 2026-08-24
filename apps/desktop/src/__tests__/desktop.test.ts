import { describe, expect, it } from "vitest";
import {
  MEDIA_PERMISSIONS,
  SECURE_WEB_PREFERENCES,
  allowPermission,
  buildContentSecurityPolicy,
  decideNavigation,
  decideWindowOpen,
  isSecure,
} from "../../electron/hardening.js";
import {
  ConnectionDivergence,
  DESKTOP_HAS_NO_GRACE_TIMER,
  assertAtomsAgree,
  swapProfile,
} from "../../electron/connection.js";

describe("G-17 — a CSP que o legado não tinha", () => {
  const csp = buildContentSecurityPolicy();

  it("existe e é restritiva por padrão", () => {
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("base-uri 'none'");
  });

  it("connect-src fechado impede EXFILTRAÇÃO", () => {
    // Mesmo que algo consiga executar script no renderer, não consegue
    // enviar nada para fora.
    expect(csp).toContain("connect-src 'self'");
    expect(csp).not.toContain("connect-src *");
  });

  it("form-action bloqueado: formulário que posta para fora é exfiltração", () => {
    expect(csp).toContain("form-action 'none'");
  });

  it("em produção NÃO há unsafe-eval", () => {
    expect(csp).not.toContain("unsafe-eval");
  });

  it("o relaxamento de dev é EXPLÍCITO e só em dev", () => {
    const dev = buildContentSecurityPolicy({ devServer: "http://127.0.0.1:5174" });
    expect(dev).toContain("'unsafe-eval'");
    expect(dev).toContain("http://127.0.0.1:5174");
    expect(dev).toContain("ws://127.0.0.1:5174");
  });
});

describe("navegação — o que o legado JÁ tinha", () => {
  it("janela nova é SEMPRE negada e desviada", () => {
    expect(decideWindowOpen("https://qualquer").action)
      .toBe("deny-and-open-externally");
  });

  it("em produção só file: navega na janela", () => {
    expect(decideNavigation("file:///app/index.html").action).toBe("allow");
    expect(decideNavigation("https://exemplo.com").action)
      .toBe("deny-and-open-externally");
  });

  it("em dev, o dev server navega", () => {
    const dev = "http://127.0.0.1:5174";
    expect(decideNavigation(`${dev}/x`, dev).action).toBe("allow");
    expect(decideNavigation("https://exemplo.com", dev).action)
      .toBe("deny-and-open-externally");
    // Em dev, file: NÃO é liberado: a origem esperada é o dev server.
    expect(decideNavigation("file:///x", dev).action)
      .toBe("deny-and-open-externally");
  });

  it("negar sem abrir faria links legítimos não funcionarem sem explicação", () => {
    expect(decideNavigation("https://docs.exemplo").reason).toContain("browser do sistema");
  });
});

describe("as três flags de isolamento", () => {
  it("o padrão seguro tem as três mais webSecurity", () => {
    expect(isSecure(SECURE_WEB_PREFERENCES)).toBe(true);
  });

  it("qualquer uma faltando reprova", () => {
    for (const quebra of [
      { contextIsolation: false }, { nodeIntegration: true },
      { sandbox: false }, { webSecurity: false },
    ]) {
      expect(isSecure({ ...SECURE_WEB_PREFERENCES, ...quebra })).toBe(false);
    }
  });
});

describe("permissões do Chromium", () => {
  it("negadas por padrão, exceto captura de mídia", () => {
    expect(allowPermission("media")).toBe(true);
    expect(allowPermission("videoCapture")).toBe(true);
    for (const p of ["geolocation", "notifications", "clipboard-read", "midi"]) {
      expect(allowPermission(p)).toBe(false);
    }
  });

  it("o conjunto de mídia é pequeno e nomeado", () => {
    expect(MEDIA_PERMISSIONS.size).toBe(3);
  });
});

describe("invariante 12 — os três átomos concordam", () => {
  it("estado desconectado é coerente", () => {
    expect(() => assertAtomsAgree({
      socketProfile: null, activeProfile: null, connectionId: null,
    })).not.toThrow();
  });

  it("socket e perfil divergentes são DETECTADOS", () => {
    // A falha silenciosa: o app mostra dados de A e escreve em B.
    expect(() => assertAtomsAgree({
      socketProfile: "a", activeProfile: "b", connectionId: "c1",
    })).toThrow(ConnectionDivergence);
  });

  it("perfil ativo sem connectionId é swap incompleto", () => {
    expect(() => assertAtomsAgree({
      socketProfile: "a", activeProfile: "a", connectionId: null,
    })).toThrow(ConnectionDivergence);
  });

  it("o swap muda os três JUNTOS", () => {
    const antes = { socketProfile: "a", activeProfile: "a", connectionId: "c1" };
    const depois = swapProfile(antes, "b", "c2");
    expect(() => assertAtomsAgree(depois)).not.toThrow();
    expect(depois).toEqual({ socketProfile: "b", activeProfile: "b", connectionId: "c2" });
  });
});

describe("G-18 — a durabilidade de shutdown é do backend", () => {
  it("o supervisor desktop NÃO tem temporizador de graça", () => {
    // Um número fixo no Electron seria adivinhação sobre um processo que ele
    // não controla.
    expect(DESKTOP_HAS_NO_GRACE_TIMER).toBe(true);
  });
});
