/* Shell do Kairos: navegação, rota e tema.
 *
 * Sem framework, sem build. São ~200 linhas de DOM que o navegador executa
 * direto — não porque frameworks sejam ruins, mas porque um passo de build a
 * mais é um passo que pode ficar dessincronizado do que está servido, e este
 * projeto já pagou esse preço com o dist herdado.
 */

import { icons } from "./icons.js";
import { aplicarTema, temaAtual } from "./ui.js";
import { dashboardView } from "./views/dashboard.js";
import { chatView } from "./views/chat.js";
import { skillsView } from "./views/skills.js";
import { sessoesView } from "./views/sessoes.js";
import { ferramentasView } from "./views/ferramentas.js";
import { modelosView } from "./views/modelos.js";
import { provedoresView } from "./views/provedores.js";
import { ajustesView } from "./views/ajustes.js";
import { loginView } from "./views/login.js";
import { runtimeView } from "./views/runtime.js";
import { api } from "./api.js";

const ROTAS = [
  { id: "visao-geral", titulo: "Visão geral", icone: "dashboard", grupo: "Agente", view: dashboardView },
  { id: "chat",        titulo: "Chat",        icone: "sessions",  grupo: "Agente", view: chatView },
  { id: "skills",      titulo: "Skills",      icone: "skills",    grupo: "Agente", view: skillsView },
  { id: "sessoes",     titulo: "Sessões",     icone: "sessions",  grupo: "Agente", view: sessoesView },
  { id: "runtime",     titulo: "Agent Runtime", icone: "tools",     grupo: "Agente", view: runtimeView },
  { id: "modelos",     titulo: "Modelos",     icone: "models",    grupo: "Configuração", view: modelosView },
  { id: "provedores",  titulo: "Provedores",  icone: "providers", grupo: "Configuração", view: provedoresView },
  { id: "ferramentas", titulo: "Ferramentas", icone: "tools",     grupo: "Configuração", view: ferramentasView },
  { id: "ajustes",     titulo: "Ajustes",     icone: "config",    grupo: "Configuração", view: ajustesView },
];

const PADRAO = "visao-geral";
const rotaPorId = (id) => ROTAS.find((r) => r.id === id);
let viewCleanup = null;
let viewAbort = null;

function disposeView() {
  viewAbort?.abort();
  viewAbort = null;
  if (typeof viewCleanup === "function") viewCleanup();
  viewCleanup = null;
}

function montarNav() {
  const grupos = [];
  for (const r of ROTAS) {
    let g = grupos.find((x) => x.nome === r.grupo);
    if (!g) grupos.push((g = { nome: r.grupo, itens: [] }));
    g.itens.push(r);
  }
  return grupos
    .map(
      (g) => `<div class="k-nav__group">${g.nome}</div>` +
        g.itens
          .map(
            (r) => `<a class="k-nav__item" href="#/${r.id}" data-rota="${r.id}">
                      ${icons[r.icone]}<span>${r.titulo}</span></a>`
          )
          .join("")
    )
    .join("");
}

function montarShell() {
  document.body.innerHTML = `
    <a class="k-skip" href="#conteudo">Ir para o conteúdo</a>
    <div class="k-app">
      <aside class="k-sidebar" data-sidebar>
        <div class="k-sidebar__brand">
          <img src="/ui/kairos-symbol.svg" alt="">
          <strong>Kairos</strong>
        </div>
        <nav class="k-nav" aria-label="Principal">${montarNav()}</nav>
        <div class="k-sidebar__foot">
          <button class="k-btn k-btn--ghost" data-tema style="width:100%">
            <span data-tema-icone></span><span data-tema-texto></span>
          </button>
          <button class="k-btn k-btn--ghost" data-sair style="width:100%">
            ${icons.logout}<span>Sair</span>
          </button>
        </div>
      </aside>
      <div>
        <header class="k-header">
          <button class="k-btn k-btn--ghost k-menu-toggle" data-menu aria-label="Abrir menu"
                  aria-expanded="false">${icons.menu}</button>
          <strong data-titulo></strong>
          <span class="k-badge" data-saude>verificando…</span>
        </header>
        <main class="k-main" id="conteudo" tabindex="-1"></main>
      </div>
    </div>`;
}

function alternarTema() {
  const ordem = ["auto", "light", "dark"];
  const proximo = ordem[(ordem.indexOf(temaAtual()) + 1) % ordem.length];
  aplicarTema(proximo);
  pintarBotaoTema();
}

function pintarBotaoTema() {
  const t = temaAtual();
  document.querySelector("[data-tema-icone]").innerHTML = t === "dark" ? icons.moon : icons.sun;
  document.querySelector("[data-tema-texto]").textContent =
    { auto: "Tema do sistema", light: "Tema claro", dark: "Tema escuro" }[t];
}

async function navegar() {
  disposeView();
  const controller = new AbortController();
  viewAbort = controller;
  const id = (location.hash.replace(/^#\/?/, "") || PADRAO).split("?")[0];
  const rota = rotaPorId(id) || rotaPorId(PADRAO);

  for (const a of document.querySelectorAll("[data-rota]")) {
    // aria-current é o que anuncia a página atual a um leitor de tela; a cor
    // sozinha não diz nada a quem não a vê.
    if (a.dataset.rota === rota.id) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  document.querySelector("[data-titulo]").textContent = rota.titulo;
  document.title = `${rota.titulo} · Kairos`;
  document.querySelector("[data-sidebar]").dataset.open = "false";

  const main = document.querySelector("#conteudo");
  main.innerHTML = "";
  try {
    const cleanup = await rota.view(main, rota, { signal: controller.signal });
    if (controller.signal.aborted) {
      if (typeof cleanup === "function") cleanup();
      return;
    }
    viewCleanup = typeof cleanup === "function" ? cleanup : null;
  } catch (e) {
    if (controller.signal.aborted) return;
    main.innerHTML = `<div class="k-error" role="alert"><h3>Esta tela falhou ao montar</h3><p></p></div>`;
    main.querySelector(".k-error p").textContent = String(e.message || e);
  }
  main.focus({ preventScroll: true });
}

async function pintarSaude() {
  const alvo = document.querySelector("[data-saude]");
  try {
    const { status, version } = await api.health();
    alvo.className = "k-badge k-badge--ok";
    alvo.textContent = `${status} · v${version}`;
  } catch {
    alvo.className = "k-badge k-badge--danger";
    alvo.textContent = "sem resposta";
  }
}

async function sair() {
  disposeView();
  try {
    await api.logout();
  } finally {
    // Mesmo se o servidor não responder, a sessão desta aba acabou: manter a
    // interface montada daria a impressão de continuar autenticado.
    montarLogin();
  }
}

function montarApp() {
  aplicarTema(temaAtual());
  montarShell();
  pintarBotaoTema();
  document.querySelector("[data-tema]").addEventListener("click", alternarTema);
  document.querySelector("[data-sair]").addEventListener("click", sair);
  document.querySelector("[data-menu]").addEventListener("click", (ev) => {
    const barra = document.querySelector("[data-sidebar]");
    const aberto = barra.dataset.open === "true";
    barra.dataset.open = String(!aberto);
    ev.currentTarget.setAttribute("aria-expanded", String(!aberto));
  });
  addEventListener("hashchange", navegar);
  navegar();
  pintarSaude();
}

function montarLogin() {
  aplicarTema(temaAtual());
  document.title = "Entrar · Kairos";
  loginView({ aoEntrar: montarApp });
}

export async function iniciar() {
  let autenticado = false;
  try {
    autenticado = (await api.quemSou()).authenticated === true;
  } catch {
    autenticado = false; // sem resposta: a tela de login é o destino seguro
  }
  if (autenticado) montarApp();
  else montarLogin();
}
