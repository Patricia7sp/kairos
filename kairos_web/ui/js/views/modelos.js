/* Catálogo canônico de modelos, filtros e troca sem reiniciar o serviço. */

import { api, ApiError } from "../api.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

export function filterModels(models, filters = {}) {
  const query = String(filters.query || "").trim().toLocaleLowerCase("pt-BR");
  return models.filter((model) => {
    const capabilities = model.capabilities || {};
    if (filters.provider && model.provider !== filters.provider) return false;
    if (filters.freeOnly && !model.is_free) return false;
    if (filters.tools && capabilities.tools !== true) return false;
    if (filters.vision && capabilities.vision !== true) return false;
    if (filters.reasoning && capabilities.reasoning !== true) return false;
    if (filters.minContext && (capabilities.context_length || 0) < filters.minContext) return false;
    if (!filters.includePreview && model.stability === "preview") return false;
    if (model.stability === "deprecated") return false;
    if (query && !`${model.name} ${model.id} ${model.provider}`.toLocaleLowerCase("pt-BR").includes(query)) return false;
    return true;
  });
}

export function modelSelectionMarkup(model) {
  return `<dialog class="k-model-dialog" data-model-dialog>
    <form method="dialog"><button class="k-btn k-btn--ghost k-model-dialog__close" aria-label="Fechar">×</button></form>
    <h2>Usar ${esc(model.name)}</h2>
    <p><code>${esc(model.provider)}/${esc(model.id)}</code></p>
    <div class="k-model-dialog__actions">
      <button class="k-btn k-btn--primary" type="button" data-apply-model>Aplicar ao próximo turno</button>
      <button class="k-btn k-btn--ghost" type="button" data-new-conversation>Iniciar nova conversa</button>
    </div>
    <p class="k-model-dialog__status" aria-live="polite" data-selection-status></p>
  </dialog>`;
}

const context = (model) => {
  const value = model.capabilities?.context_length;
  return typeof value === "number" ? value.toLocaleString("pt-BR") : "—";
};

const price = (model) => {
  if (model.is_free) return "Gratuito";
  const prompt = model.pricing?.prompt;
  const completion = model.pricing?.completion;
  if (prompt == null && completion == null) return "Preço não informado";
  return `entrada ${prompt ?? "—"} · saída ${completion ?? "—"}`;
};

function modelRows(models, defaultProvider, defaultModel) {
  if (!models.length) return `<tr><td colspan="6">Nenhum modelo corresponde aos filtros.</td></tr>`;
  return models.map((model) => {
    const capabilities = model.capabilities || {};
    const selected = model.provider === defaultProvider && model.id === defaultModel;
    return `<tr${selected ? ' class="k-tabela__destaque"' : ""}>
      <td><strong>${esc(model.name)}</strong><div class="k-tabela__sub"><code>${esc(model.id)}</code></div></td>
      <td>${esc(model.provider)}</td><td>${context(model)}</td>
      <td>${capabilities.tools ? '<span class="k-badge">ferramentas</span>' : ""}
          ${capabilities.vision ? '<span class="k-badge">visão</span>' : ""}
          ${model.stability === "preview" ? '<span class="k-badge k-badge--warn">preview</span>' : ""}</td>
      <td><div>${esc(price(model))}</div><small>${esc((model.origins || []).join(" + "))}</small></td>
      <td><button class="k-btn k-btn--ghost" data-choose-model="${esc(model.provider)}/${esc(model.id)}">Escolher</button></td>
    </tr>`;
  }).join("");
}

export async function modelosView(root) {
  root.innerHTML = `<div class="k-page-head"><h1>Modelos</h1>
      <p>Catálogo disponível, capacidades, preço conhecido e seleção ativa.</p></div>
    <div data-content><div class="k-skeleton" style="height:260px"></div></div>`;
  const content = root.querySelector("[data-content]");
  const [modelResult, providerResult] = await Promise.allSettled([api.modelos(), api.provedores()]);
  if (modelResult.status === "rejected") {
    const error = modelResult.reason;
    const hint = error instanceof ApiError && error.naoImplementado
      ? "Esta rota ainda não existe no servidor." : "Verifique o estado do serviço.";
    content.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar os modelos</h2>
      <p>${esc(error.message)}</p><p>${hint}</p></div>`;
    return;
  }

  const payload = modelResult.value;
  const models = payload.models || [];
  const providers = providerResult.status === "fulfilled" ? providerResult.value.providers || [] : [];
  const providerOptions = [...new Set(models.map((model) => model.provider))].sort();
  content.innerHTML = `<section class="k-card k-modelo-padrao"><div><span class="k-stat__label">Padrão</span>
      <strong>${esc(payload.default_provider)}/${esc(payload.default_model)}</strong></div>
      <span class="k-badge">${providers.filter((provider) => provider.configured).length} disponíveis</span></section>
    <form class="k-model-filters" data-model-filters>
      <label>Buscar <input type="search" name="query" placeholder="Nome ou ID"></label>
      <label>Provider <select name="provider"><option value="">Todos</option>
        ${providerOptions.map((provider) => `<option>${esc(provider)}</option>`).join("")}</select></label>
      <label><input type="checkbox" name="freeOnly"> Somente gratuitos</label>
      <label><input type="checkbox" name="tools"> Ferramentas</label>
      <label><input type="checkbox" name="vision"> Visão</label>
      <label><input type="checkbox" name="includePreview"> Incluir previews</label>
    </form>
    <div class="k-card k-tabela-envolve"><table class="k-tabela"><caption class="k-sr">Catálogo de modelos</caption>
      <thead><tr><th>Modelo</th><th>Provider</th><th>Contexto</th><th>Recursos</th><th>Preço/origem</th><th></th></tr></thead>
      <tbody data-model-rows></tbody></table></div><div data-model-dialog-host></div>`;

  const filters = content.querySelector("[data-model-filters]");
  const rows = content.querySelector("[data-model-rows]");
  const render = () => {
    const values = new FormData(filters);
    const active = {
      query: values.get("query"), provider: values.get("provider"),
      freeOnly: values.has("freeOnly"), tools: values.has("tools"),
      vision: values.has("vision"), includePreview: values.has("includePreview"),
    };
    rows.innerHTML = modelRows(filterModels(models, active), payload.default_provider, payload.default_model);
  };
  filters.addEventListener("input", render);
  render();

  content.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-choose-model]");
    if (!button) return;
    const [provider, ...parts] = button.dataset.chooseModel.split("/");
    const id = parts.join("/");
    const model = models.find((item) => item.provider === provider && item.id === id);
    if (!model) return;
    const host = content.querySelector("[data-model-dialog-host]");
    host.innerHTML = modelSelectionMarkup(model);
    const dialog = host.querySelector("dialog");
    dialog.showModal();
    dialog.querySelector("[data-apply-model]").addEventListener("click", async () => {
      const status = dialog.querySelector("[data-selection-status]");
      status.textContent = "Salvando seleção…";
      try {
        await api.selecionarModelo({ provider, model: id, scope: "global" });
        status.textContent = "Seleção aplicada ao próximo turno.";
      } catch (error) {
        status.textContent = error.message || "Falha ao aplicar seleção.";
      }
    });
    dialog.querySelector("[data-new-conversation]").addEventListener("click", () => {
      location.hash = `#/chat?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(id)}&new=1`;
      dialog.close();
    });
  });
}
