"""Ponto de entrada do `kairos`.

`_reversa_sdd/hermes-cli/` (superfície CLI).

**O fast path vem antes de tudo.** `--version` e a sondagem de modo container
não podem pagar o custo de montar a árvore de argparse inteira — e é
exatamente isso que `startup_fast` existe para evitar. A árvore só é montada
quando o comando pedido precisa dela.
"""

from __future__ import annotations

import sys

__all__ = ["build_parser", "main"]


def _fast_path(argv: list[str]) -> int | None:
    """Responde sem montar a árvore. `None` significa "siga o caminho lento".

    A guarda de leveza (`test_startup_fast_import_weight`) só tem sentido se
    alguém de fato usar o módulo antes dos imports pesados. Este é o uso.
    """
    if not argv:
        return None
    if argv[0] in ("--version", "-V"):
        from kairos_cli.startup_fast import fast_version_line

        print(fast_version_line())
        return 0
    return None


def build_parser():
    """Monta a árvore a partir do registro declarativo."""
    import argparse

    from kairos_cli.commands import COMMANDS

    parser = argparse.ArgumentParser(
        prog="kairos",
        description="Kairos — agente pessoal de IA.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="mostra a versão e sai")
    parser.add_argument("--json", action="store_true", help="saída em JSON, para script")

    sub = parser.add_subparsers(dest="command", metavar="<comando>")

    for cmd in COMMANDS:
        p = sub.add_parser(cmd.name, help=cmd.help)
        p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

        if cmd.subcommands:
            # `dest` nomeado por comando: é o que permite ao handler saber
            # qual subcomando veio sem inspecionar o parser.
            dest = f"{cmd.name.replace('-', '_')}_command"
            s = p.add_subparsers(dest=dest, metavar="<subcomando>")
            for scmd in cmd.subcommands:
                sp = s.add_parser(scmd.name, help=scmd.help)
                sp.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
                _extra_args(cmd.name, scmd.name, sp)
        else:
            _extra_args(cmd.name, None, p)

    return parser


def _extra_args(command: str, subcommand: str | None, parser) -> None:
    """Argumentos específicos, onde o comando os exige."""
    if command == "config" and subcommand == "show":
        parser.add_argument("key", nargs="?", help="chave pontilhada")
    elif command == "config" and subcommand == "set":
        parser.add_argument("key")
        parser.add_argument("value")
    elif command == "approvals" and subcommand == "test":
        # `cmdline`, não `command`: o parser de topo já usa `dest="command"`,
        # e um positional homônimo o SOBRESCREVE — o despacho passaria a ver
        # a linha de comando avaliada como se fosse o comando pedido.
        parser.add_argument("cmdline", help="o comando a avaliar")
        parser.add_argument("--yolo", action="store_true")
        parser.add_argument("--deny", action="append", metavar="GLOB")
        parser.add_argument("--allow", action="append", metavar="GLOB")
    elif command in ("run", "chat"):
        parser.add_argument("prompt", nargs="*", help="a mensagem")
        parser.add_argument("-q", "--quiet", action="store_true")
    elif command == "dashboard":
        parser.add_argument("--port", type=int, default=9119, help="Porta HTTP (padrão: 9119)")
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host de escuta (padrão: 127.0.0.1; use 0.0.0.0 para expor na rede)",
        )
        parser.add_argument(
            "--no-browser", action="store_true", help="Não abre o navegador automaticamente"
        )


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)

    rapido = _fast_path(args_list)
    if rapido is not None:
        return rapido

    from kairos_cli.commands import find_command
    from kairos_cli.handlers import HANDLERS, ExitCode

    parser = build_parser()
    args = parser.parse_args(args_list)

    if getattr(args, "version", False):
        from kairos_cli.startup_fast import fast_version_line

        print(fast_version_line())
        return ExitCode.OK

    if not args.command:
        parser.print_help()
        return ExitCode.USAGE

    handler = HANDLERS.get(args.command)
    if handler is None:
        cmd = find_command(args.command)
        # A regra do projeto: um comando que reporta sucesso sem efeito é
        # pior que um comando ausente. Sair com 0 aqui faria script acreditar
        # que a operação aconteceu.
        print(
            f"kairos: '{args.command}' está declarado na superfície mas ainda não "
            f"foi implementado.",
            file=sys.stderr,
        )
        if cmd and cmd.unit:
            print(
                f"kairos: a unit '{cmd.unit}' já existe; falta a ligação do comando.",
                file=sys.stderr,
            )
        return ExitCode.NOT_IMPLEMENTED

    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\ninterrompido", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        # DELIBERADO: o CLI é a superfície do usuário final. Traceback aqui é
        # ruído; o diagnóstico completo é trabalho do `doctor`.
        print(f"kairos: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ExitCode.ERROR


if __name__ == "__main__":
    raise SystemExit(main())
