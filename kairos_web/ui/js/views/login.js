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
            Na mitologia grega, <em>kairós</em> é o tempo oportuno — o momento
            que se abre e se fecha. Era representado com asas nos pés, pela
            velocidade, e uma balança em fio de navalha, pela precisão de
            julgá-lo. O deus tinha uma mecha na testa e a nuca raspada: o
            instante só se agarra quando vem chegando.
          </p>
        </div>
        <div class="k-login__asas" aria-hidden="true"></div>
      </section>

      <section class="k-login__forma">
        <form class="k-login__form" novalidate>
          <h2>Entrar</h2>
          <p class="k-login__ajuda">
            Use o token de acesso do painel. Ele é definido em
            <code>KAIROS_WEB_TOKEN</code>; sem essa variável o servidor gera um
            a cada arranque e o imprime no log do container.
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
