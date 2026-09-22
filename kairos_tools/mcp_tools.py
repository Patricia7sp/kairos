"""Ferramentas de servidores MCP de terceiros — toolset `mcp`, gated por config.

Passo 4 do plano de ferramentas (P3). Servidores externos configurados em
`mcp_servers` no `<home>/config.yaml` têm suas ferramentas registradas com
namespace `mcp__<servidor>__<ferramenta>` (delimitador duplo da spec —
`_reversa_sdd/mcp/`) e as chamadas quebram o processo de verdade via
`kairos_mcp.runtime`, o cliente `stdio` JSON-RPC em stdlib.

Cinto estreito:
- nada é registrado sem servidor **configurado e validado** (`UnsafeServerConfig`
  ⇒ log alto, zero ferramentas);
- o boot **não spawna** quando o manifest já está no cache (`SchemaCache` em
  `<home>/mcp/`) — sem cache, sincroniza uma vez no registro (a via de entrada);
- `KAIROS_MCP_OFFLINE=1` força cache-only (sem spawn) no registro;
- sob pytest, o registro **nunca spawna** (o cache é o único caminho; os testes
  de comportamento chamam `sync_mcp_servers` explicitamente para acordar o
  servidor real pelo pipe);
- servidor ausente/erro de protocolo ⇒ zero ferramentas daquele server e
  chamada com erro nomeado — nunca "sucesso sem efeito".

Ferramenta registrada com um schema e um handler por server — o despacho local
só carrega o nome, o argumento é repassado ao processo remoto.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kairos_mcp.client import (
    MCP_HARD_RESULT_CAP_CHARS,
    MCPServerConfig,
    SchemaCache,
    Transport,
    UnsafeServerConfig,
    namespaced_tool_name,
    truncate_mcp_text_result,
    validate_server_config,
)
from kairos_mcp.runtime import McpRuntimeError, fetch_tool_manifests, invoke_tool
from kairos_tools.registry import ToolRegistry, registry, tool_error

logger = logging.getLogger(__name__)

#: Teto de descrição no schema que o modelo vê — descrição de terceiro é dado
#: não confiável e mora no contexto de todo turno: um teto é proteção honesta.
_MAX_DESCRIPTION_CHARS = 1000


def _home() -> Path:
    return Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))


def _load_config(home: Path) -> Mapping[str, Any]:
    path = home / "config.yaml"
    if not path.exists():
        return {}
    import yaml

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, ValueError, RecursionError):
        # Ler a config é melhor-esforço e NUNCA derruba o boot — o registro
        # autoexecutado no import não pode cair por um YAML ilegível ou
        # venenoso (ex.: timestamp inválido como 2026-99-99, que o SafeLoader
        # rejeita com ValueError, não YAMLError). Sem config legível nada é
        # exposto (estado fechado); log em debug para não sujar o stderr de
        # subprocessos que só importam o pacote.
        logger.debug("%s: config ilegível; tratando como ausente", path)
        return {}
    return config if isinstance(config, Mapping) else {}


def _under_test() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def _mcp_offline() -> bool:
    return os.environ.get("KAIROS_MCP_OFFLINE", "").lower() in {"1", "true", "yes"}


def _schema_cache(home: Path) -> SchemaCache:
    return SchemaCache(home / "mcp")


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


def _server_entries(
    home: Path,
) -> tuple[list[tuple[str, MCPServerConfig]], list[str]]:
    """Servidores `mcp_servers` válidos e os erros de entrada, separados.

    Entrada inválida é **barulhenta** (lista de erros colecionada e logada) e
    não expõe nada — a exceção é a ferramenta ausente, nunca a ferramenta que
    erra sempre. Transporte remoto (http/sse) é recusado neste slice: o
    cliente desta entrega é `stdio`; aceitar o servidor e nunca executar seria
    o bug "reporta sucesso sem efeito".
    """
    config = _load_config(home)
    raw = config.get("mcp_servers", {})
    if not isinstance(raw, Mapping):
        return [], ["mcp_servers: esperava mapeamento nome → config"]
    validos: list[tuple[str, MCPServerConfig]] = []
    erros: list[str] = []
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.strip():
            erros.append("mcp_servers: chave de servidor deve ser texto não vazio")
            continue
        if not isinstance(spec, Mapping):
            erros.append(f"{name}: entrada deve ser um mapeamento")
            continue
        transport = str(spec.get("transport", Transport.STDIO.value)).lower()
        if transport != Transport.STDIO.value:
            erros.append(
                f"{name}: transporte '{transport}' não suportado por este slice "
                "(o cliente da entrega é stdio puro)"
            )
            continue
        command = spec.get("command")
        if not isinstance(command, str) or not command.strip():
            erros.append(f"{name}: 'command' é obrigatório para descrever o stdio")
            continue
        raw_args = spec.get("args", [])
        if not isinstance(raw_args, list) or not all(isinstance(arg, str) for arg in raw_args):
            erros.append(f"{name}: 'args' deve ser lista de textos")
            continue
        try:
            cfg = MCPServerConfig(
                name=name,
                command=command,
                args=tuple(raw_args),
                env=_env_of(name, spec.get("env")),
            )
            validate_server_config(cfg)
        except UnsafeServerConfig as exc:
            erros.append(str(exc))
        else:
            validos.append((name, cfg))
    return validos, erros


def _env_of(server: str, raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise UnsafeServerConfig(f"{server}: 'env' deve ser um mapeamento")
    env: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise UnsafeServerConfig(f"{server}: chave de 'env' deve ser texto")
        if isinstance(value, str):
            env[key] = value
        elif isinstance(value, (int, float)):
            env[key] = str(value)
        else:
            raise UnsafeServerConfig(f"{server}: valor de 'env' para {key!r} inválido")
    return env


def server_configs(home: Path) -> list[tuple[str, MCPServerConfig]]:
    """Servidores válidos — a origem da verdade para registro e sync."""
    validos, _ = _server_entries(home)
    return validos


def _mcp_available() -> bool:
    """Requisito do toolset: ao menos um servidor configurado e validado."""
    return bool(server_configs(_home()))


# ---------------------------------------------------------------------------
# Sincronização e manifestos
# ---------------------------------------------------------------------------


def sync_mcp_servers(home: Path | None = None) -> dict[str, Any]:
    """Spawna cada servidor configurado, colhe `tools/list` e grava o cache.

    Caminho **explícito** do on-ramp: é o que acorda o processo real. Em produção
    roda no primeiro registro sem cache; os testes comportamentais chamam aqui
    para exercitar o spawn de verdade pelo pipe. Retorna o relatório honesto de
    quem sincronizou e quem falhou (fail-closed: erro ⇒ zero ferramentas).
    """
    home = home or _home()
    cache = _schema_cache(home)
    relatorio: dict[str, Any] = {"sincronizados": [], "erros": []}
    for name, cfg in server_configs(home):
        try:
            tools = fetch_tool_manifests(cfg)
        except (McpRuntimeError, UnsafeServerConfig, OSError) as exc:
            msg = f"{name}: sync falhou: {exc}"
            logger.warning(msg)
            relatorio["erros"].append(msg)
            continue
        cache.store(name, tools)
        relatorio["sincronizados"].append(name)
    return relatorio


def _manifests(home: Path, name: str, cfg: MCPServerConfig) -> list[dict[str, Any]] | None:
    """Manifesto do cache; sem cache, sincroniza (uma vez) fora de pytest.

    `None` ⇒ o servidor ficou sem ferramentas registradas (fail-closed), nunca
    um lista vazia de "sucesso" — o erro já foi logado no sync.
    """
    cached = _schema_cache(home).load(name)
    if cached is not None:
        return cached
    if not (_under_test() or _mcp_offline()):
        try:
            tools = fetch_tool_manifests(cfg)
        except (McpRuntimeError, OSError) as exc:
            logger.warning("%s: sem cache e sync falhou: %s", name, exc)
            return None
        _schema_cache(home).store(name, tools)
        return tools
    if _mcp_offline():
        logger.warning("%s: cache ausente e KAIROS_MCP_OFFLINE=1 — nada registrado", name)
    return None


# ---------------------------------------------------------------------------
# Schema e handler
# ---------------------------------------------------------------------------


def _clean_description(entry: dict[str, Any]) -> str:
    desc = str(entry.get("description") or "").strip()
    if len(desc) > _MAX_DESCRIPTION_CHARS:
        desc = desc[:_MAX_DESCRIPTION_CHARS].rstrip() + "…"
    return desc


def _schema_for(server: str, entry: dict[str, Any]) -> dict[str, Any]:
    input_schema = entry.get("inputSchema")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "type": "function",
        "function": {
            "name": namespaced_tool_name(server, entry["name"]),
            "description": _clean_description(entry),
            "parameters": input_schema,
        },
    }


def _make_handler(cfg: MCPServerConfig, tool_name: str):
    def handler(**arguments: Any) -> Any:
        try:
            text = invoke_tool(cfg, tool_name, arguments)
        except (McpRuntimeError, OSError) as exc:
            return tool_error(f"{cfg.name}/{tool_name}: {exc}")
        return truncate_mcp_text_result(text, max_chars=MCP_HARD_RESULT_CAP_CHARS)

    return handler


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------


def register_mcp_tools(reg: ToolRegistry | None = None) -> None:
    """Registra o toolset `mcp` e as ferramentas dos servidores configurados.

    Chamado por padrão no import (mesmo padrão de `calendar`/`git`): o boot
    espelha o `mcp_startup` da spec usando o cache, sem acordar processo.
    """
    r = reg or registry
    home = _home()
    validos, erros = _server_entries(home)
    r.register_toolset("mcp", requirement=_mcp_available)
    for erro in erros:
        logger.warning("mcp_servers: %s", erro)
    for name, cfg in validos:
        manifests = _manifests(home, name, cfg)
        if manifests is None:
            continue
        for entry in manifests:
            tool_name = namespaced_tool_name(name, entry["name"])
            r.register(
                name=tool_name,
                handler=_make_handler(cfg, entry["name"]),
                schema=_schema_for(name, entry),
                toolset="mcp",
                max_result_size_chars=MCP_HARD_RESULT_CAP_CHARS,
            )


#: Registra por padrão — mesmo padrão do `calendar`, `git` e `builtin`. Nunca
#: spawna sob pytest (guarda interna em `_manifests`).
register_mcp_tools()
