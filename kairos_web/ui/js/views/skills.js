/* Skills — 201 capacidades em 27 categorias.
 *
 * Com esse volume, uma grade única é uma parede onde nada se acha. A tela é
 * um navegador em três tempos: escolher a categoria, filtrar dentro dela, ver
 * o detalhe. O filtro e a busca compõem — buscar dentro de "finance" procura
 * só ali, que é o que o rótulo promete.
 */

import { api, ApiError } from "../api.js";
import { icons } from "../icons.js";
import { toast } from "../ui.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/* Os nomes das categorias vêm do caminho, em inglês e em kebab-case. Traduzir
 * na exibição é diferente de renomear os diretórios: o identificador continua
 * sendo o que as skills usam para se referirem umas às outras. */
const ROTULOS = {
  "apple": "Apple",
  "autonomous-ai-agents": "Agentes autônomos",
  "blockchain": "Blockchain",
  "communication": "Comunicação",
  "creative": "Criatividade",
  "data-science": "Ciência de dados",
  "devops": "DevOps",
  "dogfood": "Uso interno",
  "email": "E-mail",
  "finance": "Finanças",
  "gaming": "Jogos",
  "geral": "Geral",
  "git": "Git",
  "github": "GitHub",
  "health": "Saúde",
  "mcp": "MCP",
  "media": "Mídia",
  "migration": "Migração",
  "mlops": "MLOps",
  "note-taking": "Notas",
  "payments": "Pagamentos",
  "productivity": "Produtividade",
  "python-dev": "Python",
  "research": "Pesquisa",
  "security": "Segurança",
  "smart-home": "Casa inteligente",
  "social-media": "Redes sociais",
  "software-development": "Desenvolvimento",
  "web-development": "Web",
  "web-research": "Pesquisa web",
  "yuanbao": "Yuanbao",
};
const rotulo = (id) => ROTULOS[id] || id.replace(/-/g, " ");

let estado = { skills: [], categorias: [], categoria: null, termo: "", selecionada: null };

export async function skillsView(raiz) {
  raiz.innerHTML = `
    <div class="k-page-head">
      <h1>Skills</h1>
      <p>Capacidades que o agente carrega. As embarcadas vêm com a imagem; as suas vivem em <code>KAIROS_HOME/skills</code>.</p>
    </div>
    <div class="k-sk" data-raiz>
      <div class="k-skeleton" style="height:420px"></div>
    </div>`;

  const alvo = raiz.querySelector("[data-raiz]");
  try {
    const dados = await api.skills();
    estado = {
      skills: dados.skills || [],
      categorias: dados.categorias || [],
      categoria: null,
      termo: "",
      selecionada: null,
    };
  } catch (e) {
    alvo.innerHTML = erroHtml(e, "Não foi possível carregar as skills");
    return;
  }
  montar(alvo);
}

function erroHtml(e, titulo) {
  const dica = e instanceof ApiError && e.naoImplementado
    ? "Esta rota ainda não existe no servidor."
    : "Os logs do container têm o detalhe.";
  return `<div class="k-error" role="alert"><h3>${esc(titulo)}</h3>
    <p>${esc(e.message)}</p><p>${dica}</p>
    <code>${esc(e.path || "")} ${e.status ? "→ HTTP " + e.status : ""}</code></div>`;
}

function visiveis() {
  const t = estado.termo.trim().toLowerCase();
  return estado.skills.filter((s) => {
    if (estado.categoria && s.category !== estado.categoria) return false;
    if (!t) return true;
    // A busca alcança o nome, a descrição e as tags: quem procura "ROI" não
    // sabe que a skill se chama `dcf-model`.
    return `${s.name} ${s.description} ${(s.tags || []).join(" ")}`.toLowerCase().includes(t);
  });
}

function montar(alvo) {
  alvo.innerHTML = `
    <aside class="k-sk__cats">
      <div class="k-sk__cats-topo">
        <h2>Categorias</h2>
        <button class="k-btn k-btn--ghost k-sk__limpar" data-limpar hidden>${icons.close} Limpar</button>
      </div>
      <nav class="k-sk__lista-cats" data-cats aria-label="Filtrar por categoria"></nav>
    </aside>
    <div class="k-sk__painel">
      <div class="k-sk__barra">
        <label class="k-field k-sk__busca">
          <span class="k-sr">Buscar skills</span>
          <input class="k-input" type="search" data-busca placeholder="Buscar por nome, descrição ou tag…">
        </label>
      </div>
      <p class="k-sk__resumo" data-resumo aria-live="polite"></p>
      <div data-grade></div>
    </div>`;

  const cats = alvo.querySelector("[data-cats]");
  const grade = alvo.querySelector("[data-grade]");
  const busca = alvo.querySelector("[data-busca]");
  const limpar = alvo.querySelector("[data-limpar]");

  function pintarCats() {
    const total = estado.skills.length;
    cats.innerHTML =
      `<button class="k-sk__cat" data-cat="" aria-current="${!estado.categoria}">
         <span>Todas</span><span class="k-badge">${total}</span></button>` +
      estado.categorias
        .map((c) => `
          <button class="k-sk__cat" data-cat="${esc(c.id)}" aria-current="${estado.categoria === c.id}">
            <span>${esc(rotulo(c.id))}</span><span class="k-badge">${c.total}</span>
          </button>`)
        .join("");
    limpar.hidden = !estado.categoria && !estado.termo;
  }

  function pintarGrade() {
    const vis = visiveis();
    const escopo = estado.categoria ? rotulo(estado.categoria) : "todas as categorias";
    alvo.querySelector("[data-resumo]").textContent =
      `${vis.length} de ${estado.skills.length} skills · ${escopo}` +
      (estado.termo ? ` · buscando “${estado.termo}”` : "");

    if (!vis.length) {
      grade.innerHTML = `<div class="k-empty k-card">
        <h3>Nada corresponde</h3>
        <p>Nenhuma skill em <strong>${esc(escopo)}</strong>${estado.termo ? ` para “${esc(estado.termo)}”` : ""}.</p>
        <p style="margin-top:var(--k-space-4)"><button class="k-btn" data-limpar-2>Ver todas as skills</button></p>
      </div>`;
      grade.querySelector("[data-limpar-2]").addEventListener("click", zerar);
      return;
    }
    grade.innerHTML = `<div class="k-grid k-grid--cards">${vis.map(cartao).join("")}</div>`;
  }

  function zerar() {
    estado.categoria = null;
    estado.termo = "";
    busca.value = "";
    pintarCats();
    pintarGrade();
  }

  cats.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-cat]");
    if (!b) return;
    estado.categoria = b.dataset.cat || null;
    pintarCats();
    pintarGrade();
  });
  busca.addEventListener("input", () => {
    estado.termo = busca.value;
    limpar.hidden = !estado.categoria && !estado.termo;
    pintarGrade();
  });
  limpar.addEventListener("click", zerar);

  grade.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-detalhe]");
    if (b) abrirDetalhe(b.closest("[data-skill]").dataset.skill);
  });
  grade.addEventListener("change", async (ev) => {
    const input = ev.target.closest('[data-acao="alternar"]');
    if (!input) return;
    const id = input.closest("[data-skill]").dataset.skill;
    const desejado = input.checked;
    input.disabled = true;
    try {
      await api.alternarSkill(id, desejado);
      const s = estado.skills.find((x) => x.id === id);
      if (s) s.enabled = desejado;
      toast(`${s?.name || id} ${desejado ? "ativada" : "desativada"}.`);
      pintarGrade();
    } catch (e) {
      // Reverter é o ponto: sem isso o interruptor mostra um estado que o
      // servidor recusou, e o usuário confia nele.
      input.checked = !desejado;
      toast(`Não foi possível alterar ${id}: ${e.message}`, "erro");
    } finally {
      input.disabled = false;
    }
  });

  pintarCats();
  pintarGrade();
}

function cartao(s) {
  const origem = s.source === "builtin"
    ? '<span class="k-badge" title="Vem com a imagem do Kairos">embarcada</span>'
    : '<span class="k-badge k-badge--accent" title="Instalada no seu KAIROS_HOME">sua</span>';
  const tags = (s.tags || []).slice(0, 3)
    .map((t) => `<span class="k-badge">${esc(t)}</span>`).join("");
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
      ${tags ? `<div class="k-skill__tags">${tags}</div>` : ""}
      <footer class="k-skill__foot">
        <button class="k-btn k-btn--ghost" data-detalhe>${icons.skills} Detalhes</button>
      </footer>
    </article>`;
}

async function abrirDetalhe(id) {
  const s = estado.skills.find((x) => x.id === id);
  if (!s) return;

  const dlg = document.createElement("dialog");
  dlg.className = "k-dialog";
  dlg.innerHTML = `
    <form method="dialog" class="k-dialog__head">
      <div>
        <h3>${esc(s.name)}</h3>
        <span class="k-dialog__cat">${esc(rotulo(s.category))} · <code>${esc(s.id)}</code></span>
      </div>
      <button class="k-btn k-btn--ghost" aria-label="Fechar">${icons.close}</button>
    </form>
    <div class="k-dialog__body">
      <p class="k-det__desc">${esc(s.description)}</p>
      <dl class="k-det__meta">
        ${s.version ? `<div><dt>Versão</dt><dd>${esc(s.version)}</dd></div>` : ""}
        ${s.author ? `<div><dt>Autoria</dt><dd>${esc(s.author)}</dd></div>` : ""}
        ${s.license ? `<div><dt>Licença</dt><dd>${esc(s.license)}</dd></div>` : ""}
        ${s.platforms?.length ? `<div><dt>Plataformas</dt><dd>${esc(s.platforms.join(", "))}</dd></div>` : ""}
        <div><dt>Origem</dt><dd>${s.source === "builtin" ? "embarcada" : "sua"}</dd></div>
      </dl>
      ${s.tags?.length ? `<div class="k-det__bloco"><h4>Tags</h4>
        <div class="k-skill__tags">${s.tags.map((t) => `<span class="k-badge">${esc(t)}</span>`).join("")}</div></div>` : ""}
      ${s.related?.length ? `<div class="k-det__bloco"><h4>Relacionadas</h4>
        <div class="k-skill__tags">${s.related.map((r) => `<span class="k-badge k-badge--accent">${esc(r)}</span>`).join("")}</div></div>` : ""}
      <div class="k-det__bloco">
        <h4>SKILL.md</h4>
        <div data-md><div class="k-skeleton" style="height:200px"></div></div>
      </div>
    </div>`;
  document.body.append(dlg);
  dlg.showModal();
  dlg.addEventListener("close", () => dlg.remove());

  const md = dlg.querySelector("[data-md]");
  try {
    const { content } = await api.skillConteudo(id);
    md.innerHTML = `
      <label class="k-field"><span class="k-sr">Conteúdo de SKILL.md</span>
        <textarea class="k-textarea" spellcheck="false"></textarea></label>
      <div class="k-dialog__foot">
        <span class="k-dialog__aviso">Salvar grava em <code>KAIROS_HOME/skills/${esc(id)}</code>; a embarcada não é tocada.</span>
        <button class="k-btn k-btn--primary" data-salvar>${icons.save} Salvar</button>
      </div>`;
    md.querySelector("textarea").value = content;
    md.querySelector("[data-salvar]").addEventListener("click", async (ev) => {
      const b = ev.currentTarget;
      b.disabled = true;
      try {
        await api.salvarSkill(id, md.querySelector("textarea").value);
        toast(`${s.name} salva.`);
        dlg.close();
      } catch (e) {
        toast(`Falha ao salvar: ${e.message}`, "erro");
      } finally {
        b.disabled = false;
      }
    });
  } catch (e) {
    md.innerHTML = erroHtml(e, "Não foi possível ler o SKILL.md");
  }
}
