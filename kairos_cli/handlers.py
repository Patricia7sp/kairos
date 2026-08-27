"""Handlers dos comandos implementados.

Cada um usa as units já reconstruídas. Um comando **declarado sem
implementação** não chega aqui: `main` o intercepta e sai com código próprio,
em vez de sair com 0 em silêncio.
"""

from __future__ import annotations

import json
import os
import sys
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


def cmd_auth(args) -> int:  # noqa: PLR0912 - subcomandos independentes mantidos num handler
    import getpass

    from kairos_cli.auth import AuthStore
    from kairos_security.credentials import (
        LegacyCredentialMigration,
        VaultState,
        build_credential_service,
    )

    home = _home()
    caminho = home / "auth.json"
    vault = build_credential_service(home)
    if args.auth_command == "vault-status":
        _emit({"backend": vault.backend_name, "estado": vault.state}, as_json=args.json)
        return ExitCode.OK
    if args.auth_command == "vault-init":
        if vault.state == VaultState.KEYRING:
            _emit({"backend": "keyring", "estado": vault.state}, as_json=args.json)
            return ExitCode.OK
        if vault.state != VaultState.NOT_CONFIGURED:
            _emit({"erro": "cofre já configurado"}, as_json=args.json)
            return ExitCode.ERROR
        password = getpass.getpass("Senha-mestra: ")
        confirmation = getpass.getpass("Confirme a senha-mestra: ")
        if password != confirmation:
            _emit({"erro": "confirmação não confere"}, as_json=args.json)
            return ExitCode.ERROR
        vault.initialize(password)
        vault.lock()
        _emit({"estado": vault.state}, as_json=args.json)
        return ExitCode.OK
    if args.auth_command == "vault-unlock":
        if vault.state == VaultState.KEYRING:
            _emit({"backend": "keyring", "estado": vault.state}, as_json=args.json)
            return ExitCode.OK
        vault.unlock(getpass.getpass("Senha-mestra: "))
        _emit(
            {"estado": vault.state, "escopo": "senha verificada somente nesta execução"},
            as_json=args.json,
        )
        return ExitCode.OK
    if args.auth_command == "migrate":
        migration = LegacyCredentialMigration(caminho, vault)
        report = migration.scan()
        if not args.confirm_remove_plaintext:
            _emit(
                {
                    "credenciais encontradas": report.credentials_found,
                    "ação": "confirmação necessária: use --confirm-remove-plaintext",
                },
                as_json=args.json,
            )
            return ExitCode.OK
        if vault.state == VaultState.NOT_CONFIGURED:
            _emit({"erro": "inicialize o cofre antes da migração"}, as_json=args.json)
            return ExitCode.ERROR
        if vault.state == VaultState.LOCKED:
            vault.unlock(getpass.getpass("Senha-mestra: "))
        imported = migration.import_and_verify()
        finalized = migration.finalize(confirm=True)
        _emit(
            {
                "verificadas": imported.credentials_verified,
                "finalizadas": finalized.credentials_finalized,
            },
            as_json=args.json,
        )
        return ExitCode.OK
    if args.auth_command != "list":
        return ExitCode.NOT_IMPLEMENTED

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


def cmd_gateway(args) -> int:
    """O serviço longo. É ele que define a vida do container."""
    import logging

    from kairos_gateway.service import GatewayService

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    as_json = getattr(args, "json", False)
    sub = getattr(args, "gateway_command", None)

    if sub == "stop":
        # Não há canal HTTP de controle (design.md): a drenagem é um arquivo.
        from kairos_gateway.service import write_drain_request

        marker = write_drain_request(_home(), getattr(args, "reason", "manual"))
        _emit({"drenagem solicitada": str(marker)}, as_json=as_json)
        return ExitCode.OK

    svc = GatewayService(_home(), poll_interval=getattr(args, "interval", 5.0) or 5.0)

    if sub == "status":
        svc.boot()
        _emit(svc.status(), as_json=as_json)
        return ExitCode.OK

    if sub == "list":
        _emit({"adapters": svc.status()["adapters"] or ["nenhum registrado"]}, as_json=as_json)
        return ExitCode.OK

    if sub == "setup":
        return ExitCode.NOT_IMPLEMENTED

    if getattr(args, "once", False):
        svc.boot()
        entregues = svc.tick()
        svc.shutdown()
        _emit({"entregues": entregues, **svc.status()}, as_json=as_json)
        return ExitCode.OK

    return svc.run()


def cmd_token(args) -> int:
    """O token de acesso do painel — sem nunca imprimi-lo por acidente.

    Três decisões deliberadas aqui:

    - `show` exige `--reveal`. Sem a flag ele imprime só um prefixo. Comandos
      são executados dentro de sessões gravadas, colados em tíquetes e ficam no
      histórico do shell; revelar por padrão é como um segredo vaza sem que
      ninguém tenha decidido revelá-lo.
    - `new` escreve o arquivo e imprime a linha de `export` — nunca o valor
      solto —, para que o caminho natural de uso seja o que não deixa rastro.
    - O arquivo nasce `0600`. Um token legível por todo o sistema não protege
      de nada.
    """
    import os
    import secrets
    import stat

    home = _home()
    arquivo = home / "web-token"
    as_json = getattr(args, "json", False)
    sub = getattr(args, "token_command", None) or "show"

    if sub == "path":
        _emit({"arquivo": str(arquivo), "existe": arquivo.exists()}, as_json=as_json)
        return ExitCode.OK

    if sub == "new":
        home.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        # Cria com 0600 desde o primeiro byte: escrever e só depois ajustar a
        # permissão deixa uma janela em que o arquivo está legível por todos.
        fd = os.open(arquivo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token + "\n")
        if as_json:
            _emit({"arquivo": str(arquivo), "criado": True}, as_json=True)
        else:
            print(f"token gravado em {arquivo} (0600)")
            print("\npara usar nesta sessão, sem deixá-lo no histórico:")
            print(f'  export KAIROS_WEB_TOKEN="$(cat {arquivo})"')
        return ExitCode.OK

    # show
    do_ambiente = os.environ.get("KAIROS_WEB_TOKEN")
    if do_ambiente:
        token, origem = do_ambiente, "KAIROS_WEB_TOKEN"
    elif arquivo.exists():
        token, origem = arquivo.read_text(encoding="utf-8").strip(), str(arquivo)
    else:
        _emit(
            {
                "erro": "nenhum token configurado",
                "como resolver": "kairos token new",
                "nota": "sem token fixo o servidor gera um por processo, e ele muda a cada restart",
            },
            as_json=as_json,
        )
        return ExitCode.NOT_FOUND if hasattr(ExitCode, "NOT_FOUND") else ExitCode.OK

    if getattr(args, "reveal", False):
        _emit({"origem": origem, "token": token}, as_json=as_json)
    else:
        _emit(
            {
                "origem": origem,
                "token": f"{token[:4]}…{token[-2:]} ({len(token)} caracteres)",
                "para ver inteiro": "kairos token show --reveal",
            },
            as_json=as_json,
        )
    return ExitCode.OK


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

    from kairos_cli.chat import ChatUsageError, run_chat

    prompt_words = getattr(args, "prompt", []) or []
    prompt = " ".join(prompt_words).strip()
    session_id = getattr(args, "session", None)
    if args.command == "run" and session_id is None:
        session_id = "cli-default"
    try:
        return asyncio.run(
            run_chat(
                home=_home(),
                session_id=session_id,
                prompt=prompt,
                provider=getattr(args, "provider", None),
                model=getattr(args, "model", None),
                as_json=getattr(args, "json", False),
                quiet=getattr(args, "quiet", False),
            )
        )
    except ChatUsageError as exc:
        print(f"kairos: {exc}", file=sys.stderr)
        return ExitCode.USAGE


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
    "gateway": cmd_gateway,
    "security": cmd_security,
    "token": cmd_token,
}
