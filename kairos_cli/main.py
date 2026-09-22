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
        p.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )

        if cmd.name == "runtime":
            _runtime_branch(p, cmd, argparse)
        elif cmd.name == "logs":
            _logs_args(p)
        elif cmd.subcommands:
            # `dest` nomeado por comando: é o que permite ao handler saber
            # qual subcomando veio sem inspecionar o parser.
            dest = f"{cmd.name.replace('-', '_')}_command"
            s = p.add_subparsers(dest=dest, metavar="<subcomando>")
            for scmd in cmd.subcommands:
                sp = s.add_parser(scmd.name, help=scmd.help)
                sp.add_argument(
                    "--json",
                    action="store_true",
                    default=argparse.SUPPRESS,
                    help=argparse.SUPPRESS,
                )
                if cmd.name == "cron":
                    _cron_args(scmd.name, sp)
                elif cmd.name == "model":
                    _model_args(scmd.name, sp)
                else:
                    _extra_args(cmd.name, scmd.name, sp)
        else:
            _extra_args(cmd.name, None, p)

    return parser


def _cron_args(subcommand: str, parser) -> None:
    import argparse

    if subcommand == "create":
        parser.add_argument("--name", required=True)
        parser.add_argument("--prompt", required=True)
        timing = parser.add_mutually_exclusive_group(required=True)
        timing.add_argument("--at", help="Data ISO com fuso horário")
        timing.add_argument("--every", type=int, help="Intervalo em minutos")
        timing.add_argument("--expr", help="Expressão cron de cinco campos, em UTC")
        parser.add_argument(
            "--times",
            type=int,
            help="Limite de ocorrências (1–1000000); falhas também contam. Sem opção: ilimitado em recorrentes",
        )
        monitor_group = parser.add_mutually_exclusive_group()
        monitor_group.add_argument(
            "--monitor",
            help="Comando da fonte que decide se o agente roda (exige agendamento recorrente)",
        )
        monitor_group.add_argument(
            "--monitor-calendar",
            action="store_true",
            help="Monitor de calendário: lembra os eventos que entram na janela (exige agendamento recorrente)",
        )
        parser.add_argument(
            "--window-minutes",
            type=int,
            help="Janela de lembretes em minutos (1–1440; padrão 120), para --monitor-calendar",
        )
        parser.add_argument(
            "--deliver",
            help="Entrega a saída ao destino 'plataforma:destino' (adapter registrado no gateway)",
        )
    elif subcommand == "monitor-set":
        parser.add_argument("job_id")
        parser.add_argument("script", help="Comando da fonte, sem shell")
    elif subcommand == "monitor-calendar-set":
        parser.add_argument("job_id")
        parser.add_argument(
            "--window-minutes",
            type=int,
            help="Janela de lembretes em minutos (1–1440; padrão 120)",
        )
    elif subcommand in (
        "pause",
        "resume",
        "remove",
        "monitor-clear",
        "monitor-show",
        "monitor-run",
    ):
        parser.add_argument("job_id")
    elif subcommand == "notepad":
        parser.add_argument("job_id")
        parser.add_argument(
            "notepad_action",
            nargs="?",
            default="list",
            choices=["get", "set", "delete", "list"],
            help="Ação (padrão: list)",
        )
        parser.add_argument("key", nargs="?", help="Chave (get/set/delete)")
        parser.add_argument("value", nargs="?", help="Valor a guardar (set)")
    elif subcommand == "history":
        parser.add_argument("job_id", nargs="?")
    elif subcommand == "blueprint":
        boo = parser.add_subparsers(dest="blueprint_command", metavar="<subcomando>")
        bp_list = boo.add_parser("list", help="Lista o catálogo de blueprints")
        bp_list.add_argument(
            "--category", help="Filtra por categoria (daily, weekly, email, general)"
        )
        bp_list.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )
        bp_show = boo.add_parser("show", help="Mostra as quatro superfícies de um blueprint")
        bp_show.add_argument("key")
        bp_show.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )
        bp_create = boo.add_parser(
            "create", help="Cria um job a partir de um blueprint (slots slot=valor)"
        )
        bp_create.add_argument("key", help="Chave do blueprint; ou um slash command inteiro")
        bp_create.add_argument(
            "sets",
            nargs="*",
            metavar="slot=valor",
            help="Valores dos slots; defaults são usados onde omitidos",
        )
        bp_create.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )


def _model_args(subcommand: str, parser) -> None:
    if subcommand == "list":
        parser.add_argument("--provider", help="Filtra pelo provedor")
        parser.add_argument("--free", action="store_true", help="Somente modelos gratuitos")
    elif subcommand in ("refresh", "test", "set"):
        parser.add_argument("provider", help="Identificador do provedor")
        if subcommand == "set":
            parser.add_argument("model", help="Identificador exato do modelo")


def _extra_args(  # noqa: PLR0912, PLR0915 - dispatcher de argumentos por comando; cascata objetiva, não (de)codificável em tabela
    command: str, subcommand: str | None, parser
) -> None:
    """Argumentos específicos, onde o comando os exige."""
    import argparse

    if command == "config" and subcommand == "show":
        parser.add_argument("key", nargs="?", help="chave pontilhada")
    elif command == "config" and subcommand == "set":
        parser.add_argument("key")
        parser.add_argument("value")
    elif command == "monitoring" and subcommand == "test":
        parser.add_argument("job_id", help="ID do agendamento monitorado")
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
        if command == "chat":
            parser.add_argument(
                "--session",
                required=True,
                type=_nonblank_session,
                metavar="ID",
                help="ID não vazio da conversa persistida",
            )
        else:
            parser.add_argument(
                "--session",
                default="cli-default",
                type=_nonblank_session,
                metavar="ID",
                help="ID da conversa persistida (padrão: cli-default)",
            )
        parser.add_argument("--provider", help="provider do override deste turno")
        parser.add_argument("--model", help="modelo do override deste turno")
        parser.add_argument(
            "--web-search",
            action="store_true",
            help="habilita busca web no Chat; consultas são enviadas ao DuckDuckGo",
        )
        parser.add_argument(
            "--experiences",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="injeta experiências ativas como referência no turno atual (padrão: ligado)",
        )
        parser.add_argument(
            "--idempotency-key",
            help="chave durável para repetir manualmente o mesmo turno de runtime",
        )
    elif command == "memory" and subcommand == "experiences":
        parser.add_argument(
            "experience_action",
            choices=("list", "add", "confirm", "reject", "invalidate", "record"),
            help="Ação sobre as experiências aprendidas",
        )
        parser.add_argument(
            "experience_id", nargs="?", help="ID da experiência (exceto em list/add)"
        )
        parser.add_argument("--trigger", help="Gatilho textual (add)")
        parser.add_argument("--observation", default="", help="Observação/erro observado (add)")
        parser.add_argument("--correction", help="Correção a aplicar (add)")
        parser.add_argument("--scope", default="global", help="Escopo da experiência (add)")
        parser.add_argument("--confidence", type=float, default=0.9, help="Confiança inicial (add)")
        parser.add_argument(
            "--status",
            choices=("candidata", "ativa", "invalida"),
            default="ativa",
            help="Estado inicial da experiência (add)",
        )
        parser.add_argument(
            "--all", action="store_true", help="Inclui candidatas e inválidas na listagem (list)"
        )
        parser.add_argument(
            "--success", action="store_true", help="Registra sucesso no record (senão, falha)"
        )
    elif command == "gateway" and subcommand in ("run", None):
        parser.add_argument("--once", action="store_true", help="Roda um único tick e sai")
        parser.add_argument(
            "--interval", type=float, default=5.0, help="Segundos entre ticks (padrão: 5)"
        )
    elif command == "gateway" and subcommand == "stop":
        parser.add_argument("--reason", default="manual", help="Motivo registrado no marcador")
    elif command == "telegram" and subcommand == "config":
        parser.add_argument(
            "--enabled", choices=("true", "false"), help="ativa/desativa envio (outbound)"
        )
        parser.add_argument(
            "--inbound-enabled", choices=("true", "false"), help="ativa/desativa canal de entrada"
        )
        parser.add_argument("--allowed-ids", help="IDs Telegram autorizados, separados por vírgula")
        parser.add_argument("--poll-interval", type=float, help="segundos entre polls (0.5–300)")
        parser.add_argument(
            "--mode",
            choices=("poll", "webhook"),
            help="transporte do canal: poll (long-poll) ou webhook (POST público)",
        )
        parser.add_argument("--chat-default", help="chat_id padrão para envio")
    elif command == "telegram" and subcommand == "webhook":
        parser.add_argument(
            "url", help="URL HTTPS pública do webhook (ex.: https://host/api/inbound/telegram)"
        )
    elif command == "telegram" and subcommand == "webhook-off":
        parser.add_argument(
            "--drop-pending", action="store_true", help="descarta updates pendentes"
        )
    elif command == "slack" and subcommand == "config":
        parser.add_argument(
            "--enabled", choices=("true", "false"), help="ativa/desativa envio (outbound)"
        )
        parser.add_argument("--channel-default", help="canal padrão de envio (#canal)")
    elif command == "whatsapp" and subcommand == "config":
        parser.add_argument(
            "--enabled", choices=("true", "false"), help="ativa/desativa envio (outbound)"
        )
        parser.add_argument("--phone-number-id", help="Phone Number ID (Meta Cloud API)")
        parser.add_argument("--number-default", help="número padrão de envio (E.164)")
    elif command == "telegram" and subcommand == "stop":
        parser.add_argument("--reason", default="manual", help="Motivo registrado no marcador")
    elif command == "import" and subcommand is None:
        parser.add_argument("--data", help="Dados JSON a importar")
    elif command == "import-agent" and subcommand is None:
        parser.add_argument("--data", help="Dados JSON do agente a importar")
    elif command == "login" and subcommand is None:
        parser.add_argument("--provider", required=True, help="Identificador do provedor")
        parser.add_argument("--api-key", required=True, help="Chave de API do provedor")
    elif command == "logout" and subcommand is None:
        parser.add_argument("--provider", help="Provedor a desconectar (padrão: todos)")
    elif command == "prompt-size" and subcommand == "set":
        parser.add_argument("--size", type=int, required=True, help="Tamanho do prompt")
    elif command == "console" and subcommand == "eval":
        parser.add_argument("--expression", required=True, help="Expressão a avaliar")
    elif command == "skin" and subcommand == "use":
        parser.add_argument("--theme", required=True, help="Tema a aplicar")
    elif command == "hooks" and subcommand == "use":
        parser.add_argument("--hook", required=True, help="Hook a ativar")
    elif command in ("pairing", "peer") and subcommand in ("revoke", "remove", "add"):
        parser.add_argument("--target", required=True, help="Identificador do alvo")
    elif command == "token" and subcommand in ("show", None):
        parser.add_argument(
            "--reveal", action="store_true", help="Imprime o token inteiro (pense antes)"
        )
    elif command == "auth" and subcommand == "migrate":
        parser.add_argument(
            "--confirm-remove-plaintext",
            action="store_true",
            help="Confirma a remoção dos segredos legados após verificação",
        )
    elif command == "security":
        parser.add_argument(
            "--target", help="Diretório de runtime a auditar (padrão: $KAIROS_HOME)"
        )
        parser.add_argument(
            "--source", help="Raiz do código-fonte a auditar (padrão: detecta o repositório)"
        )
        parser.add_argument(
            "--no-source", action="store_true", help="Audita só o runtime, sem ler o código-fonte"
        )
        parser.add_argument(
            "--fail-on",
            choices=["critical", "high", "medium", "low"],
            default="high",
            help="Severidade mínima que reprova a auditoria (padrão: high)",
        )
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
    elif command == "uninstall" and subcommand is None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirma a remoção do home (sem isto, o uninstall é recusa)",
        )


def _runtime_branch(parser, command, argparse) -> None:
    """Build the sole three-level CLI branch without changing legacy dispatch."""
    subparsers = parser.add_subparsers(dest="runtime_command", metavar="<subcomando>")
    for child in command.subcommands:
        child_parser = subparsers.add_parser(child.name, help=child.help)
        child_parser.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        if child.name == "session":
            session_parsers = child_parser.add_subparsers(
                dest="runtime_session_command", metavar="<subcomando>"
            )
            for leaf in child.subcommands:
                leaf_parser = session_parsers.add_parser(leaf.name, help=leaf.help)
                leaf_parser.add_argument(
                    "--json",
                    action="store_true",
                    default=argparse.SUPPRESS,
                    help=argparse.SUPPRESS,
                )
                _runtime_args(leaf.name, leaf_parser, session=True)
        else:
            _runtime_args(child.name, child_parser, session=False)


def _runtime_args(command: str, parser, *, session: bool) -> None:
    if command in {"project-export", "changes", "apply", "publish"}:
        required = {
            "project-export": ("repo", "revision", "catalog"),
            "changes": ("session", "output"),
            "apply": ("repo", "bundle", "approve", "branch", "worktree", "receipt"),
            "publish": ("receipt", "title", "base"),
        }
        for name in required[command]:
            parser.add_argument("--" + name, required=True)
        if command == "apply":
            parser.add_argument("--test", action="append", required=True)
    elif session and command == "create":
        parser.add_argument("--cwd", required=True)
        parser.add_argument(
            "--sandbox",
            required=True,
            choices=["read_only", "workspace_write", "broad_access"],
        )
        parser.add_argument("--consent", action="store_true")
        parser.add_argument("--session", dest="session_id")
        parser.add_argument("--parent-session")
    elif session and command == "end":
        parser.add_argument("--session", required=True, type=_nonblank_session)
    elif command == "login":
        parser.add_argument(
            "--method", required=True, choices=["apiKey", "chatgpt", "chatgptDeviceCode"]
        )
    elif command == "approve":
        parser.add_argument("--session", required=True, type=_nonblank_session)
        parser.add_argument("--approval", required=True)
        parser.add_argument("--decision", required=True, choices=["accept", "decline"])
    elif command == "cancel":
        parser.add_argument("--session", required=True, type=_nonblank_session)
        parser.add_argument("--turn", required=True)
    elif command == "watch":
        parser.add_argument("--session", required=True, type=_nonblank_session)
        parser.add_argument("--cursor")


def _logs_args(parser) -> None:
    parser.add_argument("--limit", type=_logs_limit, default=50, help="Máximo de eventos (1–200)")
    parser.add_argument("--service", choices=("web", "chat", "search", "cron"))
    parser.add_argument("--level", choices=("info", "warning", "error"))


def _logs_limit(value: str) -> int:
    import argparse

    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--limit deve ser um inteiro entre 1 e 200") from exc
    if not 1 <= limit <= 200:
        raise argparse.ArgumentTypeError("--limit deve estar entre 1 e 200")
    return limit


def _nonblank_session(value: str) -> str:
    session_id = value.strip()
    if not session_id:
        from argparse import ArgumentTypeError

        raise ArgumentTypeError("--session exige um ID não vazio")
    return session_id


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
