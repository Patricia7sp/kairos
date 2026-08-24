/* Skills — a tela prioritária.
 *
 * Faz três coisas que a anterior não fazia: mostra o motivo quando algo falha,
 * distingue skill embarcada de skill do usuário, e deixa editar o SKILL.md sem
 * sair da página.
 */

import { api, ApiError } from "../api.js";
import { icons } from "../icons.js";
import { toast } from "../ui.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function cartao(s) {
  const origem = s.source === "builtin"
    ? '<span class="k-badge" title="Vem com a imagem do Kairos">embarcada</span>'
    : '<span class="k-badge k-badge--accent" title="Instalada no seu KAIROS_HOME">sua</span>';
  return `
    <article class="k-card k-skill" data-skill="${esc(s.id)}">
      <header class="k-skill__head">
        <div>
          <h3>${esc(s.name)}</h3>
          <div class="k-skill__meta">${origem}
            <span class="k-badge ${s.enabled ? "k-badge--ok" : ""}">${s.enabled ? "ativa" : "inativa"}</span>
          </div>
        </div>
        <label class="k-switch" title="${s.enabled ? "Desativar" : "Ativar"} ${esc(s.name)}">
          <input type="checkbox" ${s.enabled ? "checked" : ""}
                 aria-label="Ativar a skill ${esc(s.name)}" data-acao="alternar">
          <span class="k-switch__track"></span><span class="k-switch__thumb"></span>
        </label>
      </header>
      <p class="k-skill__desc">${esc(s.description) || "<em>sem descrição no frontmatter</em>"}</p>
      <footer class="k-skill__foot">
        <button class="k-btn k-btn--ghost" data-acao="ver">${icons.skills} Ver SKILL.md</button>
      </footer>
    </article>`;
}

function esqueleto() {
  return `<div class="k-grid k-grid--cards">${
    Array.from({ length: 3 }, () => '<div class="k-skeleton" style="height:186px"></div>').join("")
  }</div>`;
}

function erro(e) {
  const dica = e instanceof ApiError && e.naoImplementado
    ? "Esta rota ainda não existe no servidor."
    : "Tente recarregar. Se persistir, os logs do container têm o detalhe.";
  return `<div class="k-error" role="alert">
    <h3>Não foi possível carregar as skills</h3>
    <p>${esc(e.message)}</p><p>${dica}</p>
    <code>${esc(e.path || "")} ${e.status ? "→ HTTP " + e.status : ""}</code>
  </div>`;
}

export async function skillsView(raiz) {
  raiz.innerHTML = `
    <div class="k-page-head">
      <h1>Skills</h1>
      <p>Capacidades que o agente carrega. As embarcadas vêm com a imagem; as suas vivem em <code>KAIROS_HOME/skills</code>.</p>
      <p class="k-skill-contagem" data-contagem></p>
    </div>
    <div class="k-skill-bar">
      <label class="k-field k-skill-busca">
        <span class="k-sr">Filtrar skills</span>
        <input class="k-input" type="search" placeholder="Filtrar por nome ou descrição…" data-filtro>
      </label>
      <button class="k-btn" data-recarregar>${icons.refresh} Recarregar</button>
    </div>
    <div data-lista>${esqueleto()}</div>`;

  const lista = raiz.querySelector("[data-lista]");
  let skills = [];

  async function carregar() {
    lista.innerHTML = esqueleto();
    try {
      skills = (await api.skills()).skills || [];
      pintar();
    } catch (e) {
      lista.innerHTML = erro(e);
    }
  }

  function pintar() {
    const termo = (raiz.querySelector("[data-filtro]").value || "").trim().toLowerCase();
    const vis = termo
      ? skills.filter((s) => `${s.name} ${s.description}`.toLowerCase().includes(termo))
      : skills;

    if (!skills.length) {
      lista.innerHTML = `<div class="k-empty"><h3>Nenhuma skill instalada</h3>
        <p>Coloque um diretório com <code>SKILL.md</code> em <code>KAIROS_HOME/skills</code>.</p></div>`;
      return;
    }
    if (!vis.length) {
      lista.innerHTML = `<div class="k-empty"><h3>Nada corresponde a “${esc(termo)}”</h3></div>`;
      return;
    }

    // Agrupadas por categoria: 85 cartões numa grade única viram uma parede
    // onde nada se acha. O cabeçalho de grupo é o que devolve a orientação.
    const grupos = new Map();
    for (const s of vis) {
      const g = s.category || "geral";
      if (!grupos.has(g)) grupos.set(g, []);
      grupos.get(g).push(s);
    }
    const ativas = skills.filter((s) => s.enabled).length;
    raiz.querySelector("[data-contagem]").textContent =
      `${skills.length} skills em ${grupos.size} categorias · ${ativas} ativas` +
      (termo ? ` · ${vis.length} correspondem ao filtro` : "");

    lista.innerHTML = [...grupos.entries()]
      .map(([nome, itens]) => `
        <section class="k-skill-grupo">
          <h2 class="k-skill-grupo__titulo">
            ${esc(nome)} <span class="k-badge">${itens.length}</span>
          </h2>
          <div class="k-grid k-grid--cards">${itens.map(cartao).join("")}</div>
        </section>`)
      .join("");
  }

  raiz.querySelector("[data-filtro]").addEventListener("input", pintar);
  raiz.querySelector("[data-recarregar]").addEventListener("click", carregar);

  lista.addEventListener("change", async (ev) => {
    const input = ev.target.closest('[data-acao="alternar"]');
    if (!input) return;
    const id = input.closest("[data-skill]").dataset.skill;
    const desejado = input.checked;
    input.disabled = true;
    try {
      await api.alternarSkill(id, desejado);
      const s = skills.find((x) => x.id === id);
      if (s) s.enabled = desejado;
      toast(`${id} ${desejado ? "ativada" : "desativada"}.`);
      pintar();
    } catch (e) {
      // Reverter é o ponto: sem isso o interruptor mostra um estado que o
      // servidor recusou, e o usuário confia nele.
      input.checked = !desejado;
      toast(`Não foi possível alterar ${id}: ${e.message}`, "erro");
    } finally {
      input.disabled = false;
    }
  });

  lista.addEventListener("click", async (ev) => {
    const botao = ev.target.closest('[data-acao="ver"]');
    if (!botao) return;
    abrirEditor(botao.closest("[data-skill]").dataset.skill);
  });

  await carregar();
}

async function abrirEditor(id) {
  const dlg = document.createElement("dialog");
  dlg.className = "k-dialog";
  dlg.innerHTML = `
    <form method="dialog" class="k-dialog__head">
      <h3>${esc(id)}</h3>
      <button class="k-btn k-btn--ghost" aria-label="Fechar">${icons.close}</button>
    </form>
    <div class="k-dialog__body"><div class="k-skeleton" style="height:340px"></div></div>`;
  document.body.append(dlg);
  dlg.showModal();
  dlg.addEventListener("close", () => dlg.remove());

  const corpo = dlg.querySelector(".k-dialog__body");
  try {
    const { content } = await api.skillConteudo(id);
    corpo.innerHTML = `
      <label class="k-field">
        <span class="k-sr">Conteúdo de SKILL.md</span>
        <textarea class="k-textarea" spellcheck="false"></textarea>
      </label>
      <div class="k-dialog__foot">
        <span class="k-dialog__aviso">Salvar grava em <code>KAIROS_HOME/skills/${esc(id)}</code>; a embarcada não é tocada.</span>
        <button class="k-btn k-btn--primary" data-salvar>${icons.save} Salvar</button>
      </div>`;
    corpo.querySelector("textarea").value = content;
    corpo.querySelector("[data-salvar]").addEventListener("click", async (ev) => {
      const b = ev.currentTarget;
      b.disabled = true;
      try {
        await api.salvarSkill(id, corpo.querySelector("textarea").value);
        toast(`${id} salva.`);
        dlg.close();
      } catch (e) {
        toast(`Falha ao salvar: ${e.message}`, "erro");
      } finally {
        b.disabled = false;
      }
    });
  } catch (e) {
    corpo.innerHTML = erro(e);
  }
}
