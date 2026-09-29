"""Shell interativo do `kairos` — acolhimento que **delega** ao próprio `main`.

Nenhuma lógica é reimplementada aqui: cada opção do menu se torna o mesmo
argv que o usuário digitaria (`run`, `chat`, `context`), atravessando a mesma
árvore declarativa, os mesmos handlers e o mesmo core que a Web usa (D-PC.1).

Sem TTY não há menu: `kairos` sem comando em pipe/script continua sendo
ajuda + `ExitCode.USAGE` — interatividade nunca muda comportamento de script.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence

__all__ = ["run_shell", "should_open_shell"]

DEFAULT_SESSION = "cli"


def should_open_shell(*, stdin=None, stdout=None) -> bool:
    """Só abre em terminal de verdade. Pipe, redireção e CI seguem o caminho
    de help + erro de uso."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    return bool(stdin.isatty() and stdout.isatty())


def _menu(sessao: str, cwd: str) -> list[tuple[str, list[str]]]:
    return [
        ("analisar projeto", ["run", f"analise o projeto atual em {cwd}", "--session", sessao]),
        ("executar agente (chat interativo)", ["chat", "--session", sessao]),
        ("mostrar o contexto", ["context"]),
        ("sair", []),
    ]


def _resolve(
    linha: str, *, sessao: str, cwd: str, comandos: set[str] | None = None
) -> list[str] | str | None:
    """Linha do usuário → argv. Menu numerado, comando conhecido ou prompt
    rápido (`run`), na ordem; `None` ignora a linha (vazia)."""
    if linha in ("sair", "exit", "quit", "0", "4"):
        return "sair"
    if linha in ("1", "2", "3"):
        return _menu(sessao, cwd)[int(linha) - 1][1]

    try:
        tokens = shlex.split(linha)
    except ValueError:
        return ["run", "--session", sessao, linha]
    if not tokens:
        return None

    primeiro = tokens[0].lower()
    if primeiro == "chat" and "--session" not in tokens:
        return ["chat", "--session", sessao, *tokens[1:]]
    if comandos is not None:
        if primeiro in comandos:
            return tokens
    else:
        from kairos_cli.commands import command_names

        if primeiro in set(command_names()):
            return tokens

    return ["run", "--session", sessao, *tokens]


def run_shell(
    *,
    evaluate: Callable[[list[str]], int] | None = None,
    stdin=None,
    stdout=None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    command_names: Sequence[str] | None = None,
) -> int:
    """Loop do shell. `evaluate` é `kairos_cli.main.main` (injetável para
    testar o menu sem montar service graph)."""
    from kairos_cli.main import main
    from kairos_cli.startup_fast import fast_version_line

    evaluate = evaluate or main
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    env = env if env is not None else os.environ
    cwd = cwd if cwd is not None else os.getcwd()

    sessao = env.get("KAIROS_SHELL_SESSION") or DEFAULT_SESSION
    comandos = set(command_names) if command_names is not None else None

    print(f"Kairos CLI — {fast_version_line()}", file=stdout)
    print(file=stdout)
    for numero, (rotulo, _argv) in enumerate(_menu(sessao, cwd), start=1):
        print(f"  {numero}) {rotulo}", file=stdout)
    print("  ou digite um comando direto; 'sair' termina.", file=stdout)

    while True:
        try:
            linha = stdin.readline()
        except KeyboardInterrupt:
            print("\ninterrompido", file=stdout)
            return 130
        if linha == "":
            print(file=stdout)
            return 0
        linha = linha.strip()
        if not linha:
            continue

        acao = _resolve(linha, sessao=sessao, cwd=cwd, comandos=comandos)
        if acao is None:
            continue
        if acao == "sair":
            return 0
        if not acao:
            continue

        try:
            codigo = evaluate(list(acao))
        except KeyboardInterrupt:
            print("\ninterrompido", file=stdout)
            return 130
        except SystemExit as exc:  # argparse sai assim (ex.: comando mal digitado)
            codigo = exc.code if isinstance(exc.code, int) else 2
        if codigo:
            print(f"(comando saiu com {codigo})", file=stdout)
