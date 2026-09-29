"""`kairos context`: contorno do ambiente e da sessão, lendo estado real.

Não monta service graph nem toca providers: o contexto é leitura de
`state.db` + sondagem de home/perfil/container — o mesmo material de
`doctor`/`insights`, sem inventar valor que não esteja persistido.

Sem `state.db`, responde **"nenhum turno persistido"** — e não cria o banco
para soar completo (mesma regra do `insights`: ausência não vira zero bonito).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from kairos_cli.startup_fast import (
    KAIROS_VERSION,
    container_mode_marker_exists,
    profile_name,
    resolve_kairos_home,
)

__all__ = ["build_context", "render_context"]


def _sessao_mais_recente(conn) -> dict | None:
    row = conn.execute(
        "SELECT id, source, execution_kind, message_count, tool_call_count, "
        "       input_tokens, output_tokens, last_activity_at, title "
        "FROM sessions "
        "ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def build_context(*, home: Path | None = None, env: Mapping[str, str] | None = None) -> dict:
    """Dados honestos do ambiente e da sessão mais recente, se houver."""
    home = Path(home or resolve_kairos_home())
    env = env if env is not None else os.environ

    contexto: dict = {
        "home": str(home),
        "versao": KAIROS_VERSION,
        "perfil": profile_name(),
        "container": container_mode_marker_exists(),
        "sessao_ativa": env.get("KAIROS_SHELL_SESSION") or "cli-default",
        "config.yaml": (home / "config.yaml").exists(),
        "state.db": None,
    }

    db_path = home / "state.db"
    if not db_path.exists():
        contexto["state.db"] = {"presente": False}
        return contexto

    from kairos_state import connect, read_schema_version

    try:
        conn = connect(db_path)
    except Exception as exc:  # noqa: BLE001 - banco legado é estado real a reportar
        contexto["state.db"] = {"presente": True, "erro": str(exc)}
        return contexto

    try:
        try:
            versao = read_schema_version(conn)
            total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            recente = _sessao_mais_recente(conn)
        except Exception as exc:  # noqa: BLE001 - banco anterior à migração
            contexto["state.db"] = {"presente": True, "legado": str(exc)}
            return contexto
        contexto["state.db"] = {
            "presente": True,
            "schema_version": versao,
            "total_sessions": total,
            "sessao_mais_recente": recente,
        }
    finally:
        conn.close()
    return contexto


def render_context(contexto: dict) -> list[str]:
    """Linhas textuais do contexto (o JSON é responsabilidade do handler)."""
    perfil = f"{contexto['perfil']}" if contexto["perfil"] else "— (padrão)"
    linhas: list[str] = [
        f"home          {contexto['home']}",
        f"versão        {contexto['versao']}",
        f"perfil        {perfil}",
        f"container     {'sim' if contexto['container'] else 'não'}",
        f"sessão ativa  {contexto['sessao_ativa']}",
        f"config.yaml   {'existe' if contexto['config.yaml'] else 'ausente'}",
    ]

    estado = contexto["state.db"]
    if estado is None or not estado.get("presente"):
        linhas.append("state.db       ausente — nenhum turno persistido ainda")
        return linhas
    if "erro" in estado:
        linhas.append(f"state.db       erro ao abrir: {estado['erro']}")
        return linhas
    if "legado" in estado:
        linhas.append(f"state.db       banco anterior ao schema: {estado['legado']}")
        return linhas

    linhas.append(
        f"state.db       presente (schema {estado['schema_version']}; "
        f"{_n(estado['total_sessions'])} de {_n(estado['total_sessions'], 'sessão', 'sessões')})"
    )
    recente = estado["sessao_mais_recente"]
    if recente is not None:
        linhas.append(
            f"  recente      {recente['id']} ({recente['source']}/"
            f"{recente['execution_kind']}; {_n(recente['message_count'], 'mensagem', 'mensagens')}; "
            f"{recente['input_tokens'] + recente['output_tokens']} tokens)"
        )
    return linhas


def _n(valor: int, singular: str = "sessão", plural: str = "sessões") -> str:
    return f"{valor} {singular if valor == 1 else plural}"
