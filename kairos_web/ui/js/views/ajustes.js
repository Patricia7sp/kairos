/* Preferências globais usadas pela composição do próximo turno. */
import { api } from "../api.js";

export async function ajustesView(raiz, _rota, { signal } = {}) {
  const listeners = new AbortController();
  let disposed = Boolean(signal?.aborted);
  let busy = false;
  const dispose = () => {
    disposed = true;
    listeners.abort();
    signal?.removeEventListener("abort", dispose);
  };
  if (disposed) return dispose;
  signal?.addEventListener("abort", dispose, { once: true });
  raiz.innerHTML = `
    <div class="k-page-head"><h1>Ajustes</h1>
      <p>Preferências globais para as próximas respostas. Perfis e conversas podem definir suas próprias opções.</p></div>
    <section class="k-card">
      <h2>Geração de respostas</h2>
      <p data-default-selection></p>
      <form class="k-form">
        <label class="k-field">Temperatura
          <input class="k-input" name="temperature" type="number" min="0" max="2" step="any" placeholder="Padrão do provedor">
        </label>
        <p>Valores menores favorecem respostas mais previsíveis. O suporte depende do modelo.</p>
        <label class="k-field">Limite de tokens da resposta
          <input class="k-input" name="max_tokens" type="number" min="1" max="2147483647" step="1" placeholder="Padrão do provedor">
        </label>
        <p>Deixe um campo vazio para remover a preferência global.</p>
        <button class="k-btn k-btn--primary" type="submit" disabled>Salvar preferências</button>
        <button class="k-btn k-btn--ghost" type="button" data-settings-reload>Recarregar</button>
      </form>
      <p role="status" aria-live="polite" data-settings-status>Carregando preferências…</p>
    </section>`;
  const form = raiz.querySelector("form");
  const status = raiz.querySelector("[data-settings-status]");
  const save = form.querySelector('[type="submit"]');
  const reload = form.querySelector("[data-settings-reload]");
  const fields = ["temperature", "max_tokens"];
  const setBusy = (value) => {
    busy = value;
    save.disabled = reload.disabled = value;
    fields.forEach((name) => { form.elements.namedItem(name).disabled = value; });
  };
  const render = (data) => {
    fields.forEach((name) => { form.elements.namedItem(name).value = data.generation?.[name] ?? ""; });
    raiz.querySelector("[data-default-selection]").textContent =
      data.default_model ? `Modelo padrão: ${data.default_model} (${data.default_provider || "provedor não definido"})` : "Nenhum modelo padrão definido.";
  };
  const load = async () => {
    if (disposed || busy) return;
    setBusy(true);
    try {
      const data = await api.ajustes();
      if (disposed) return;
      render(data);
      status.textContent = "";
      setBusy(false);
    } catch {
      if (disposed) return;
      setBusy(false);
      save.disabled = true;
      status.textContent = "Não foi possível carregar as preferências. Use Recarregar para tentar novamente.";
    }
  };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (disposed || busy || save.disabled || !form.reportValidity()) return;
    const generation = Object.fromEntries(fields.map((name) => {
      const value = form.elements.namedItem(name).value.trim();
      return [name, value === "" ? null : Number(value)];
    }));
    setBusy(true);
    status.textContent = "Salvando…";
    try {
      const data = await api.salvarAjustes(generation);
      if (disposed) return;
      render(data);
      status.textContent = "Preferências salvas. Serão usadas nas próximas respostas.";
    } catch {
      if (!disposed) status.textContent = "Não foi possível salvar as preferências. Tente novamente.";
    } finally {
      if (!disposed) setBusy(false);
    }
  }, { signal: listeners.signal });
  reload.addEventListener("click", () => void load(), { signal: listeners.signal });
  await load();
  return dispose;
}
