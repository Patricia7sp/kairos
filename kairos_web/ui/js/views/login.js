/* Tela de entrada.
 *
 * A metade esquerda é a marca; a direita, o formulário. O padrão existe porque
 * funciona: um lado carrega o significado e o outro carrega a tarefa, e em tela
 * estreita a marca encolhe para o topo em vez de disputar espaço com o campo.
 */

import { icons } from "../icons.js";

export function loginView({ aoEntrar }) {
  document.body.innerHTML = `
    <main class="k-login">
      <section class="k-login__marca">
        <div class="k-login__marca-conteudo">
          <img src="/ui/kairos-symbol-claro.svg" alt="" class="k-login__simbolo">
          <h1>Kairos</h1>
          <p class="k-login__lema">O instante certo</p>
          <p class="k-login__mito">
            <em>Kairós</em> é o momento oportuno — a diferença entre fazer e
            fazer na hora certa. É disso que trata uma plataforma de agentes:
            não de responder rápido, mas de agir quando a ação ainda vale.
          </p>
          <ul class="k-login__pilares">
            <li>
              <strong>No momento certo.</strong> Os agentes observam gatilhos e
              agendas e disparam quando a janela abre — sem esperar você lembrar.
            </li>
            <li>
              <strong>Com precisão.</strong> Cada agente carrega só as skills que
              a tarefa exige, e pede aprovação antes do que é irreversível.
            </li>
            <li>
              <strong>Com autonomia.</strong> Tarefas longas seguem sozinhas em
              segundo plano e entregam o resultado onde você já está.
            </li>
          </ul>
        </div>
        <div class="k-login__asas" aria-hidden="true"></div>
      </section>

      <section class="k-login__forma">
        <form class="k-login__form" novalidate>
          <h2>Entrar</h2>
          <p class="k-login__ajuda">
            Não tem o token? Gere um com <code>kairos token new</code> — ele é
            gravado em <code>KAIROS_HOME/web-token</code> com permissão 0600 e
            nunca aparece nos logs.
          </p>

          <label class="k-field">
            <span class="k-label">Token de acesso</span>
            <input class="k-input" type="password" name="token" autocomplete="current-password"
                   required autofocus placeholder="••••••••••••••••" aria-describedby="k-login-erro">
          </label>

          <p class="k-login__erro" id="k-login-erro" role="alert" hidden></p>

          <button class="k-btn k-btn--primary k-login__enviar" type="submit">Entrar</button>
        </form>
        <footer class="k-login__rodape">Kairos · painel local</footer>
      </section>
    </main>`;

  const form = document.querySelector(".k-login__form");
  const erro = document.querySelector(".k-login__erro");
  const botao = document.querySelector(".k-login__enviar");

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const token = form.token.value.trim();
    erro.hidden = true;
    if (!token) {
      mostrarErro("Informe o token de acesso.");
      return;
    }
    botao.disabled = true;
    botao.textContent = "Entrando…";
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!res.ok) {
        mostrarErro(res.status === 401 ? "Token inválido." : `Falha no login (HTTP ${res.status}).`);
        return;
      }
      aoEntrar();
    } catch {
      mostrarErro("Sem resposta do servidor.");
    } finally {
      botao.disabled = false;
      botao.textContent = "Entrar";
    }
  });

  function mostrarErro(msg) {
    erro.textContent = msg;
    erro.hidden = false;
    // Devolver o foco ao campo evita que quem usa teclado tenha de caçá-lo de
    // volta depois de cada tentativa.
    form.token.focus();
    form.token.select();
  }

  void icons;
}
