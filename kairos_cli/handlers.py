"""Handlers dos comandos implementados.

Cada um usa as units já reconstruídas. Um comando **declarado sem
implementação** não chega aqui: `main` o intercepta e sai com código próprio,
em vez de sair com 0 em silêncio.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["HANDLERS", "ExitCode"]


class ExitCode:
    OK = 0
    ERROR = 1
    USAGE = 2
    #: Comando existe na superfície mas ainda não faz nada. Código próprio,
    #: para script conseguir distinguir "falhou" de "não existe ainda".
    NOT_IMPLEMENTED = 69


def _home() -> Path:
    from kairos_cli.startup_fast import resolve_kairos_home

    return Path(resolve_kairos_home())


def _emit(data: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif isinstance(data, list):
        for linha in data:
            print(linha)
    elif isinstance(data, dict):
        largura = max((len(k) for k in data), default=0)
        for k, v in data.items():
            print(f"{k:<{largura}}  {v}")
    else:
        print(data)


# ---------------------------------------------------------------------------


def cmd_version(args) -> int:
    from kairos_cli.startup_fast import fast_version_line

    print(fast_version_line())
    return ExitCode.OK


def cmd_status(args) -> int:
    from kairos_container import in_container, read_container_mode
    from kairos_i18n import get_language

    modo = read_container_mode() if in_container() else None
    _emit(
        {
            "home": str(_home()),
            "idioma": get_language(),
            "container": "sim" if modo else "não",
            "supervisionado": ("não" if modo and modo.degraded else "sim") if modo else "—",
        },
        as_json=args.json,
    )
    return ExitCode.OK


def cmd_doctor(args) -> int:
    """Diagnóstico. **Sai com erro quando encontra problema** — um doctor que
    sempre sai 0 não serve para script nem para CI."""
    from kairos_state import connect, read_schema_version
    from kairos_state.contention import get_write_contention_stats
    from kairos_state.migrations import migrate

    achados: list[str] = []
    db_path = _home() / "state.db"

    try:
        conn = connect(db_path)
        try:
            versao = read_schema_version(conn)
            if versao is None:
                migrate(conn)
                versao = read_schema_version(conn)
            modo = conn.execute("PRAGMA journal_mode").fetchone()[0]
            fks = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            integridade = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        # DELIBERADO: o doctor existe para relatar o que está quebrado. Uma
        # exceção aqui É o achado — propagá-la daria traceback em vez de
        # diagnóstico.
        _emit({"state.db": f"ERRO: {exc}"}, as_json=args.json)
        return ExitCode.ERROR

    if fks != 1:
        achados.append("PRAGMA foreign_keys desligado: as FKs são decorativas")
    if integridade != "ok":
        achados.append(f"integrity_check: {integridade}")
    if modo.lower() != "wal":
        achados.append(f"journal_mode={modo} (WAL indisponível neste sistema de arquivos)")

    contencao = get_write_contention_stats()
    desistencias = contencao["by_budget"]["transcript"]["gaveup_total"]
    if desistencias:
        achados.append(
            f"{desistencias} escrita(s) de transcript desistiram por banco ocupado — "
            "turno destruído"
        )

    relatorio = {
        "state.db": str(db_path),
        "schema_version": versao,
        "journal_mode": modo,
        "foreign_keys": "on" if fks == 1 else "OFF",
        "integrity_check": integridade,
        "contenção (esperas)": contencao["waits_total"],
        "achados": achados or ["nenhum"],
    }
    _emit(relatorio, as_json=args.json)
    return ExitCode.ERROR if achados else ExitCode.OK


def cmd_config(args) -> int:
    from kairos_cli.config import ConfigResolver

    sub = args.config_command
    caminho = _home() / "config.yaml"

    if sub == "path":
        print(caminho)
        return ExitCode.OK
    if sub == "env-path":
        print(_home() / ".env")
        return ExitCode.OK
    if sub == "show":
        import yaml

        try:
            cfg = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
        except OSError:
            cfg = {}
        resolver = ConfigResolver(env=dict(os.environ), profile_config=cfg)
        if args.key:
            valor, camada = resolver.resolve(args.key)
            # A camada vai junto: "por que este valor?" tem resposta sem o
            # usuário adivinhar em qual dos cinco lugares olhar.
            _emit({args.key: valor, "camada": camada.name if camada else "—"}, as_json=args.json)
        else:
            _emit(cfg, as_json=args.json)
        return ExitCode.OK
    if sub == "check":
        import yaml

        try:
            yaml.safe_load(caminho.read_text(encoding="utf-8"))
        except OSError:
            print(f"config.yaml ausente em {caminho} (os defaults valem)")
            return ExitCode.OK
        except yaml.YAMLError as exc:
            print(f"config.yaml inválido: {exc}")
            return ExitCode.ERROR
        print("config.yaml válido")
        return ExitCode.OK

    return ExitCode.NOT_IMPLEMENTED


def cmd_tools(args) -> int:
    from kairos_tools import registry
    from kairos_tools.policy import DELEGATE_BLOCK_REASONS, SANDBOX_ALLOWED_TOOLS

    nomes = registry.get_all_tool_names()
    if args.json:
        _emit(
            {
                "registradas": nomes,
                "sandbox_allowlist": sorted(SANDBOX_ALLOWED_TOOLS),
                "delegate_blocklist": DELEGATE_BLOCK_REASONS,
            },
            as_json=True,
        )
        return ExitCode.OK

    print(f"registradas: {len(nomes)}")
    for n in nomes:
        print(f"  {n}")
    print(f"\nallowlist de sandbox ({len(SANDBOX_ALLOWED_TOOLS)}):")
    for n in sorted(SANDBOX_ALLOWED_TOOLS):
        print(f"  {n}")
    print(f"\nbloqueadas na delegação ({len(DELEGATE_BLOCK_REASONS)}):")
    for n, motivo in sorted(DELEGATE_BLOCK_REASONS.items()):
        print(f"  {n}: {motivo}")
    return ExitCode.OK


def cmd_approvals(args) -> int:
    from kairos_tools.approval import ApprovalContext, resolve

    if args.approvals_command != "test":
        return ExitCode.NOT_IMPLEMENTED

    d = resolve(
        ApprovalContext(
            command=args.cmdline,
            yolo=args.yolo,
            user_deny=tuple(args.deny or ()),
            allowlist=tuple(args.allow or ()),
        )
    )
    _emit(
        {
            "veredito": d.verdict.value,
            "camada": f"{int(d.layer)} — {d.layer.name}",
            "contornável por --yolo": "sim" if d.bypassable else "NÃO",
            "casou": d.matched or "—",
            "motivo": d.reason,
        },
        as_json=args.json,
    )
    # Códigos amigáveis a script, como no legado.
    return {"allow": 0, "ask": 2, "deny": 3}[d.verdict.value]


def cmd_skills(args) -> int:
    from kairos_skills.sync import sync_bundled_skills

    sub = args.skills_command
    user_dir = _home() / "skills"

    if sub == "list":
        if not user_dir.is_dir():
            print("nenhuma skill instalada")
            return ExitCode.OK
        nomes = sorted(p.name for p in user_dir.iterdir() if (p / "SKILL.md").is_file())
        _emit(nomes or ["nenhuma skill instalada"], as_json=args.json)
        return ExitCode.OK

    if sub == "sync":
        bundled = Path(__file__).resolve().parent.parent / "skills"
        r = sync_bundled_skills(bundled, user_dir)
        _emit(
            {
                "copiadas": r.copied,
                "atualizadas": r.updated,
                "preservadas (editadas pelo usuário)": r.user_modified,
                "respeitadas (apagadas pelo usuário)": r.user_deleted,
            },
            as_json=args.json,
        )
        return ExitCode.OK

    return ExitCode.NOT_IMPLEMENTED


def cmd_mcp(args) -> int:
    from kairos_mcp import SERVER_TOOLS, UNPUBLISHED_TOOLS, mcp_available

    if args.mcp_command == "list":
        _emit(
            {
                "pacote mcp instalado": "sim" if mcp_available() else "não (integração é no-op)",
                "ferramentas do servidor": list(SERVER_TOOLS),
                "não publicadas": UNPUBLISHED_TOOLS,
            },
            as_json=args.json,
        )
        return ExitCode.OK
    return ExitCode.NOT_IMPLEMENTED


def cmd_plugins(args) -> int:
    from kairos_plugins import VALID_HOOKS, VALID_PLUGIN_KINDS

    if args.plugins_command == "list":
        raiz = _home() / "plugins"
        nomes = sorted(p.name for p in raiz.iterdir()) if raiz.is_dir() else []
        _emit(
            {
                "instalados": nomes or ["nenhum"],
                "kinds válidos": sorted(VALID_PLUGIN_KINDS),
                "hooks disponíveis": len(VALID_HOOKS),
            },
            as_json=args.json,
        )
        return ExitCode.OK
    return ExitCode.NOT_IMPLEMENTED


def cmd_profile(args) -> int:
    from kairos_cli.startup_fast import profile_name

    if args.profile_command == "show":
        _emit({"perfil": profile_name() or "default", "home": str(_home())}, as_json=args.json)
        return ExitCode.OK
    if args.profile_command == "list":
        raiz = _home().parent / "profiles"
        nomes = sorted(p.name for p in raiz.iterdir() if p.is_dir()) if raiz.is_dir() else []
        _emit(["default", *nomes], as_json=args.json)
        return ExitCode.OK
    return ExitCode.NOT_IMPLEMENTED


def cmd_auth(args) -> int:
    from kairos_cli.auth import AuthStore

    if args.auth_command != "list":
        return ExitCode.NOT_IMPLEMENTED

    caminho = _home() / "auth.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        dados = {}
    store = AuthStore(profile=dados.get("credential_pool", {}))
    # Nunca imprime o segredo: `auth list` é o comando que mais aparece em
    # captura de tela e em log de suporte.
    _emit(
        {p: f"{len(c)} credencial(is)" for p, c in store.profile.items()}
        or {"—": "nenhuma credencial"},
        as_json=args.json,
    )
    return ExitCode.OK


def cmd_cron(args) -> int:
    from kairos_cron.schedule import croniter_available
    from kairos_domain.scheduling import ExecutionStatus

    if args.cron_command in ("status", None):
        _emit(
            {
                "croniter": "disponível"
                if croniter_available()
                else "ausente (jobs 'cron' ficam inertes)",
                "estados de execução": [s.value for s in ExecutionStatus],
            },
            as_json=args.json,
        )
        return ExitCode.OK
    if args.cron_command == "list":
        _emit(["nenhum job agendado"], as_json=args.json)
        return ExitCode.OK
    return ExitCode.NOT_IMPLEMENTED


def cmd_tick(args) -> int:
    from kairos_cron.dispatch import DispatchClaimer

    DispatchClaimer()
    print("tick executado: nenhum job devido")
    return ExitCode.OK


def cmd_sync(args) -> int:
    if args.sync_command == "status":
        manifesto = _home() / "skills" / ".bundled_manifest"
        from kairos_skills.sync import read_manifest

        m = read_manifest(manifesto)
        _emit({"manifesto": str(manifesto), "skills rastreadas": len(m)}, as_json=args.json)
        return ExitCode.OK
    return ExitCode.NOT_IMPLEMENTED


def cmd_web(args) -> int:
    import webbrowser

    import uvicorn

    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    port = getattr(args, "port", 9119) or 9119
    no_browser = getattr(args, "no_browser", False)

    print(f"🚀 Servidor Web do Kairos ouvindo em http://{host}:{port}")
    print(f"   • Local: http://127.0.0.1:{port}")
    print(f"   • Localhost: http://localhost:{port}")
    if not no_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:  # noqa: BLE001, S110
            pass

    try:
        from kairos_web.server import app

        uvicorn.run(app, host=host, port=port, log_level="info")
        return ExitCode.OK
    except KeyboardInterrupt:
        print("\nServidor web encerrado.")
        return ExitCode.OK
    except Exception as exc:  # noqa: BLE001
        print(f"Erro ao iniciar servidor web: {exc}")
        return ExitCode.ERROR


def cmd_run(args) -> int:
    import asyncio

    from kairos_cli.config import load_config
    from kairos_providers.manager import ProviderManager
    from kairos_tools.registry import registry

    prompt_words = getattr(args, "prompt", []) or []
    prompt = " ".join(prompt_words).strip()

    config = load_config()
    provider_name = config.get("provider", "anthropic")
    model_name = config.get("model")

    # Obtem credenciais
    auth_path = _home() / "auth.json"
    auth_pool = {}
    if auth_path.exists():
        try:
            auth_pool = json.loads(auth_path.read_text(encoding="utf-8")).get("credential_pool", {})
        except Exception:  # noqa: BLE001, S110
            pass

    manager = ProviderManager(auth_store=auth_pool)
    provider = manager.get_provider(provider_name, model=model_name)

    async def _execute_turn(user_msg: str) -> None:
        messages = [{"role": "user", "content": user_msg}]
        tools = registry.get_definitions()
        print(f"\n[Kairos - {provider.name}] > ", end="", flush=True)
        try:
            async for chunk in provider.stream_chat(messages, tools=tools, model=model_name):
                if chunk.delta_text:
                    print(chunk.delta_text, end="", flush=True)
                if chunk.delta_tool_calls:
                    print(f"\n[Ferramenta: {chunk.delta_tool_calls}]", flush=True)
            print()
        except Exception as exc:  # noqa: BLE001
            print(f"\nErro no provedor {provider_name}: {exc}")

    if prompt:
        asyncio.run(_execute_turn(prompt))
        return ExitCode.OK

    # Modo interativo REPL
    print("Kairos Agent CLI (digite 'sair' ou Ctrl+C para encerrar)")
    try:
        while True:
            try:
                line = input("\nvocê > ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in ("exit", "quit", "sair"):
                break
            asyncio.run(_execute_turn(line))
        return ExitCode.OK
    except KeyboardInterrupt:
        print("\nSaindo do Kairos.")
        return ExitCode.OK


def cmd_security(args) -> int:
    from kairos_security import Severity, format_cli_summary, format_json_report, run_full_audit

    as_json = getattr(args, "json", False)
    target = getattr(args, "target", None)
    target_path = Path(target) if target else _home()

    source = getattr(args, "source", None)
    scan_source = not getattr(args, "no_source", False)
    report = run_full_audit(
        target_path,
        source_root=Path(source) if source else None,
        scan_source=scan_source,
    )

    if as_json:
        print(format_json_report(report))
    else:
        print(format_cli_summary(report))
        if scan_source and report.source_root is None:
            print("aviso: código-fonte não encontrado — auditado só o runtime.")

    # Ordem decrescente: reprovar em "medium" também reprova em high/critical.
    ladder = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    threshold = getattr(args, "fail_on", "high") or "high"
    reproving = set(ladder[: ladder.index(Severity(threshold)) + 1])
    if any(f.severity in reproving for f in report.findings):
        return ExitCode.ERROR
    return ExitCode.OK


HANDLERS = {
    "run": cmd_run,
    "chat": cmd_run,
    "version": cmd_version,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "config": cmd_config,
    "tools": cmd_tools,
    "approvals": cmd_approvals,
    "skills": cmd_skills,
    "mcp": cmd_mcp,
    "plugins": cmd_plugins,
    "profile": cmd_profile,
    "auth": cmd_auth,
    "cron": cmd_cron,
    "tick": cmd_tick,
    "sync": cmd_sync,
    "dashboard": cmd_web,
    "security": cmd_security,
}
