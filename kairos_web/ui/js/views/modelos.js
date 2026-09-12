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

export function modelSelectionMarkup(model, { scope = "conversation" } = {}) {
  const applyLabel = scope === "global"
    ? "Definir como padrão global"
    : scope === "draft" ? "Aplicar ao próximo turno do rascunho" : "Aplicar ao próximo turno";
  return `<dialog class="k-model-dialog" data-model-dialog>
    <form method="dialog"><button class="k-btn k-btn--ghost k-model-dialog__close" aria-label="Fechar">×</button></form>
    <h2>Usar ${esc(model.name)}</h2>
    <p><code>${esc(model.provider)}/${esc(model.id)}</code></p>
    <div class="k-model-dialog__actions">
      <button class="k-btn k-btn--primary" type="button" data-apply-model>${applyLabel}</button>
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

const modelRouteContext = () => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  return {
    sessionId: params.get("session") || "",
    provider: params.get("provider") || "",
    model: params.get("model") || "",
    draft: params.get("new") === "1",
  };
};

export async function modelosView(root, _route, { signal } = {}) {
  let disposed = false;
  const dispose = () => { disposed = true; };
  signal?.addEventListener("abort", dispose, { once: true });
  if (signal?.aborted) return dispose;
  const routeContext = modelRouteContext();
  root.innerHTML = `<div class="k-page-head"><h1>Modelos</h1>
      <p>Catálogo disponível, capacidades, preço conhecido e seleção ativa.</p></div>
    <div data-content><div class="k-skeleton" style="height:260px"></div></div>`;
  const content = root.querySelector("[data-content]");
  const [modelResult, providerResult] = await Promise.allSettled([api.modelos(), api.provedores()]);
  if (disposed || signal?.aborted) return dispose;
  if (modelResult.status === "rejected") {
    const error = modelResult.reason;
    const hint = error instanceof ApiError && error.naoImplementado
      ? "Esta rota ainda não existe no servidor." : "Verifique o estado do serviço.";
    content.innerHTML = `<div class="k-error" role="alert"><h2>Não foi possível carregar os modelos</h2>
      <p>${esc(error.message)}</p><p>${hint}</p></div>`;
    return dispose;
  }

  const payload = modelResult.value;
  let models = payload.models || [];
  const providers = providerResult.status === "fulfilled" ? providerResult.value.providers || [] : [];
  const providerOptions = [...new Set(models.map((model) => model.provider))].sort();
  content.innerHTML = `<section class="k-card k-modelo-padrao"><div><span class="k-stat__label">Padrão</span>
      <strong data-default-selection>${esc(payload.default_provider)}/${esc(payload.default_model)}</strong></div>
      <span class="k-badge">${providers.filter((provider) => provider.configured).length} configurados</span></section>
    <form class="k-model-filters" data-model-filters>
      <label>Buscar <input type="search" name="query" placeholder="Nome ou ID"></label>
      <label>Provider <select name="provider"><option value="">Todos</option>
        ${providerOptions.map((provider) => `<option>${esc(provider)}</option>`).join("")}</select></label>
      <label><input type="checkbox" name="freeOnly"> Somente gratuitos</label>
      <label><input type="checkbox" name="tools"> Ferramentas</label>
      <label><input type="checkbox" name="vision"> Visão</label>
      <label><input type="checkbox" name="reasoning"> Raciocínio</label>
      <label>Contexto mínimo <input type="number" name="minContext" min="0" step="1"></label>
      <label><input type="checkbox" name="includePreview"> Incluir previews</label>
    </form>
    <p class="k-model-filter-status" aria-live="polite" data-model-filter-status></p>
    <div class="k-card k-tabela-envolve"><table class="k-tabela"><caption class="k-sr">Catálogo de modelos</caption>
      <thead><tr><th>Modelo</th><th>Provider</th><th>Contexto</th><th>Recursos</th><th>Preço/origem</th><th></th></tr></thead>
      <tbody data-model-rows></tbody></table></div><div data-model-dialog-host></div>`;

  const filters = content.querySelector("[data-model-filters]");
  const rows = content.querySelector("[data-model-rows]");
  const filterStatus = content.querySelector("[data-model-filter-status]");
  const defaultSelection = content.querySelector("[data-default-selection]");
  const render = () => {
    const values = new FormData(filters);
    const active = {
      query: values.get("query"), provider: values.get("provider"),
      freeOnly: values.has("freeOnly"), tools: values.has("tools"),
      vision: values.has("vision"), reasoning: values.has("reasoning"),
      minContext: Math.max(0, Number(values.get("minContext")) || 0),
      includePreview: values.has("includePreview"),
    };
    rows.innerHTML = modelRows(filterModels(models, active), payload.default_provider, payload.default_model);
  };
  let previewLoaded = models.some((model) => model.stability === "preview");
  let previewRequest = null;
  const loadPreviews = async () => {
    if (previewLoaded || previewRequest) return previewRequest;
    filterStatus.textContent = "Carregando previews…";
    const request = api.modelos({ includePreview: true });
    previewRequest = request;
    try {
      const previewPayload = await request;
      if (disposed || signal?.aborted || previewRequest !== request) return;
      models = previewPayload.models || [];
      previewLoaded = true;
      filterStatus.textContent = "";
      render();
    } catch (error) {
      if (disposed || signal?.aborted || previewRequest !== request) return;
      filterStatus.textContent = error.message || "Não foi possível carregar os previews.";
    } finally {
      if (previewRequest === request) previewRequest = null;
    }
  };
  filters.addEventListener("input", () => {
    render();
    const values = new FormData(filters);
    if (values.has("includePreview")) void loadPreviews();
  });
  render();

  content.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-choose-model]");
    if (!button) return;
    const [provider, ...parts] = button.dataset.chooseModel.split("/");
    const id = parts.join("/");
    const model = models.find((item) => item.provider === provider && item.id === id);
    if (!model) return;
    const host = content.querySelector("[data-model-dialog-host]");
    const scope = routeContext.draft ? "draft" : routeContext.sessionId ? "conversation" : "global";
    host.innerHTML = modelSelectionMarkup(model, { scope });
    const dialog = host.querySelector("dialog");
    dialog.showModal();
    dialog.querySelector("[data-apply-model]").addEventListener("click", async (applyEvent) => {
      if (disposed) return;
      const applyButton = applyEvent.currentTarget;
      const status = dialog.querySelector("[data-selection-status]");
      if (routeContext.draft) {
        const params = new URLSearchParams({
          provider,
          model: id,
          new: "1",
          session: routeContext.sessionId,
        });
        location.hash = `#/chat?${params}`;
        dialog.close();
        return;
      }
      applyButton.disabled = true;
      status.textContent = "Salvando seleção…";
      try {
        const selection = routeContext.sessionId
          ? { provider, model: id, scope: "conversation", session_id: routeContext.sessionId }
          : { provider, model: id, scope: "global" };
        await api.selecionarModelo(selection);
        if (disposed || signal?.aborted) return;
        if (!routeContext.sessionId) {
          payload.default_provider = provider;
          payload.default_model = id;
          defaultSelection.textContent = `${provider}/${id}`;
          render();
        }
        status.textContent = routeContext.sessionId
          ? "Seleção aplicada ao próximo turno desta conversa."
          : "Novo padrão global salvo.";
      } catch (error) {
        if (disposed || signal?.aborted) return;
        status.textContent = error.message || "Falha ao aplicar seleção.";
      } finally {
        if (!disposed && !signal?.aborted) applyButton.disabled = false;
      }
    });
    dialog.querySelector("[data-new-conversation]").addEventListener("click", () => {
      location.hash = `#/chat?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(id)}&new=1`;
      dialog.close();
    });
  });
  return dispose;
}
