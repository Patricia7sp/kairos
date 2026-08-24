/* Espaço reservado honesto.
 *
 * Diz QUAL rota falta e por quê, em vez de mostrar uma tela vazia — a tela
 * vazia foi o defeito que originou este trabalho.
 */

import { icons } from "../icons.js";

export function emBreveView(raiz, rota) {
  raiz.innerHTML = `
    <div class="k-page-head"><h1>${rota.titulo}</h1></div>
    <div class="k-empty k-card">
      <h3>Ainda não migrada</h3>
      <p>Esta tela existe no novo sistema visual, mas a interface do Kairos para
         ela ainda está sendo escrita. As skills vieram primeiro.</p>
      <p style="margin-top:var(--k-space-4)">
        <a class="k-btn" href="#/skills">${icons.skills} Ir para Skills</a>
      </p>
    </div>`;
}
