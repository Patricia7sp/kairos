/* Utilidades de interface partilhadas pelas views. */

export function toast(mensagem, tipo = "ok") {
  let pilha = document.querySelector(".k-toast");
  if (!pilha) {
    pilha = document.createElement("div");
    pilha.className = "k-toast";
    // `status` e não `alert`: alert interrompe o leitor de tela no meio da
    // frase, e uma confirmação de rotina não merece isso.
    pilha.setAttribute("role", "status");
    pilha.setAttribute("aria-live", "polite");
    document.body.append(pilha);
  }
  const item = document.createElement("div");
  item.className = "k-toast__item" + (tipo === "erro" ? " k-toast__item--error" : "");
  item.textContent = mensagem;
  pilha.append(item);
  setTimeout(() => item.remove(), 4200);
}

const CHAVE_TEMA = "kairos.tema";

export function temaAtual() {
  try {
    return localStorage.getItem(CHAVE_TEMA) || "auto";
  } catch {
    return "auto"; // janela privada, storage bloqueado: seguir com o padrão
  }
}

export function aplicarTema(tema) {
  if (tema === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", tema);
  try {
    localStorage.setItem(CHAVE_TEMA, tema);
  } catch {
    /* preferência não persiste; a sessão atual continua correta */
  }
}
