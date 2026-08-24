/* Dashboard: o estado do sistema numa olhada. */

import { api } from "../api.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function stat(label, valor, dica = "") {
  return `<div class="k-card"><div class="k-stat__label">${esc(label)}</div>
    <div class="k-stat__value">${esc(valor)}</div>
    ${dica ? `<div class="k-stat__hint">${esc(dica)}</div>` : ""}</div>`;
}

export async function dashboardView(raiz) {
  raiz.innerHTML = `
    <div class="k-page-head"><h1>Visão geral</h1><p>Estado do agente e do que ele carrega.</p></div>
    <div class="k-grid k-grid--stats" data-stats>${
      Array.from({ length: 4 }, () => '<div class="k-skeleton" style="height:104px"></div>').join("")
    }</div>`;

  const alvo = raiz.querySelector("[data-stats]");
  // Uma tela que morre inteira porque um endpoint falhou é o padrão que este
  // projeto está corrigindo: cada tile resolve o seu próprio destino.
  const [saude, skills, sessoes, modelos] = await Promise.allSettled([
    api.health(), api.skills(), api.sessoes(), api.modelos(),
  ]);

  const v = (r, fn, alt = "—") => (r.status === "fulfilled" ? fn(r.value) : alt);

  alvo.innerHTML = [
    stat("Serviço", v(saude, (d) => d.status === "ok" ? "no ar" : d.status, "indisponível"),
         v(saude, (d) => `versão ${d.version}`, "sem resposta de /api/health")),
    stat("Skills ativas", v(skills, (d) => `${d.skills.filter((s) => s.enabled).length}/${d.skills.length}`),
         v(skills, (d) => `${d.skills.filter((s) => s.source === "user").length} suas`)),
    stat("Sessões", v(sessoes, (d) => (d.sessions || []).length)),
    stat("Modelos", v(modelos, (d) => (d.models || []).length),
         v(modelos, (d) => `padrão: ${d.default_model || "—"}`)),
  ].join("");
}
