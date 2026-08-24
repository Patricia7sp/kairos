"""Cliente MCP — conexão, registro e o teto de resultado em duas camadas.

`_reversa_sdd/mcp/` (Tarefa 12).

**A dependência do pacote `mcp` é opcional.** Sem ele o módulo inteiro é
no-op e loga em debug — não levanta, não avisa em erro. Um agente que não usa
MCP não deve pagar nem em ruído nem em falha por uma integração que não pediu.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "MCP_HARD_RESULT_CAP_CHARS",
    "MCPServerConfig",
    "SchemaCache",
    "Transport",
    "UnsafeServerConfig",
    "mcp_available",
    "namespaced_tool_name",
    "truncate_mcp_text_result",
    "validate_server_config",
]


class Transport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


def mcp_available() -> bool:
    """O pacote `mcp` está instalado?"""
    try:
        import mcp  # noqa: F401
    except ImportError:
        logger.debug("pacote 'mcp' ausente; a integração MCP é no-op")
        return False
    return True


# ---------------------------------------------------------------------------
# T-21 — o teto de resultado, em DUAS camadas
# ---------------------------------------------------------------------------
#
# Teto rígido de alocação para um único payload de texto MCP. Ele fica
# DELIBERADAMENTE muito acima do limiar de spillover (50 KB), e a ORDEM é o
# requisito:
#
#   resultado normal grande  →  chega ÍNTEGRO ao spillover (disco + preview)
#   enxurrada patológica     →  truncada aqui, com perda
#
# Um teto no nível do spillover seria proteção correta no lugar errado:
# truncaria antes que o spillover pudesse preservar o dado. Esta camada existe
# porque, sem ela, o payload inteiro seria alocado, serializado em JSON e
# repassado adiante antes que a camada de orçamento sequer o visse.
MCP_HARD_RESULT_CAP_CHARS = 2_000_000

_HEAD_FRACTION = 0.4


def truncate_mcp_text_result(text: str, max_chars: int = MCP_HARD_RESULT_CAP_CHARS) -> str:
    """Corte 40% cabeça / 60% cauda, com aviso de omissão no meio.

    A cauda pesa mais porque erro e conclusão vivem no fim da saída; um corte
    só de cabeça descartaria justamente a parte que responde à pergunta.
    """
    if len(text) <= max_chars:
        return text
    cabeca = int(max_chars * _HEAD_FRACTION)
    cauda = max_chars - cabeca
    omitidos = len(text) - cabeca - cauda
    return (
        text[:cabeca] + f"\n\n... [RESULTADO MCP TRUNCADO — {omitidos:,} chars omitidos "
        f"de {len(text):,} no total] ...\n\n" + text[-cauda:]
    )


# ---------------------------------------------------------------------------
# Configuração de servidor e checagens de segurança
# ---------------------------------------------------------------------------


class UnsafeServerConfig(ValueError):
    """Forma conhecida de abuso numa entrada de servidor."""


#: Padrões recusados **no salvamento e de novo no spawn**. Duas vezes porque
#: o arquivo de config pode ser editado à mão entre um e outro — validar só na
#: gravação protegeria apenas contra o caminho que já passa pela UI.
_ABUSIVE_COMMANDS = frozenset({"sh", "bash", "zsh", "cmd", "powershell", "pwsh"})
_ABUSIVE_ARG_MARKERS = ("-c", "/c", "-Command", "|", "&&", ";", "$(", "`")


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: Transport = Transport.STDIO
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)


def validate_server_config(cfg: MCPServerConfig) -> None:
    """Recusa formas conhecidas de abuso. Chamada **nas duas pontas**."""
    if not cfg.name or "/" in cfg.name or cfg.name.startswith("."):
        raise UnsafeServerConfig(f"nome de servidor inválido: {cfg.name!r}")

    if cfg.transport is Transport.STDIO:
        if not cfg.command:
            raise UnsafeServerConfig(f"{cfg.name}: transporte stdio exige 'command'")
        base = Path(cfg.command).name.lower()
        if base in _ABUSIVE_COMMANDS:
            raise UnsafeServerConfig(
                f"{cfg.name}: comando {cfg.command!r} é um shell. Um servidor MCP "
                f"deve ser um executável, não uma linha de shell — senão a entrada "
                f"de config vira execução de comando arbitrário."
            )
        for arg in cfg.args:
            if any(m in arg for m in _ABUSIVE_ARG_MARKERS):
                raise UnsafeServerConfig(f"{cfg.name}: argumento {arg!r} contém marcador de shell")
    else:
        if not cfg.url:
            raise UnsafeServerConfig(f"{cfg.name}: transporte {cfg.transport} exige 'url'")
        if not cfg.url.startswith(("http://", "https://")):
            raise UnsafeServerConfig(f"{cfg.name}: url inválida: {cfg.url!r}")


def namespaced_tool_name(server: str, tool: str) -> str:
    """Nome estável e sem colisão no registry.

    Dois servidores podem expor `search`; sem namespace, o segundo
    silenciosamente sobrescreveria o primeiro.
    """
    limpo = "".join(c if (c.isalnum() or c == "_") else "_" for c in f"{server}_{tool}")
    return f"mcp__{limpo}"


# ---------------------------------------------------------------------------
# Cache de schema
# ---------------------------------------------------------------------------


class SchemaCache:
    """Manifestos de ferramenta em disco.

    Existe para **registrar sem acordar processos stdio**: sem o cache, montar
    o prompt exigiria spawnar todo servidor MCP configurado a cada boot, só
    para perguntar quais ferramentas ele tem.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, server: str) -> Path:
        return self._root / f"{server}.json"

    def load(self, server: str) -> list[dict] | None:
        try:
            data = json.loads(self._path(server).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Cache corrompido não é erro: é cache ausente. Levantar aqui
            # impediria o boot por causa de um arquivo descartável.
            return None
        return data if isinstance(data, list) else None

    def store(self, server: str, tools: list[dict]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(server).with_suffix(".tmp")
        tmp.write_text(json.dumps(tools, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(server))

    def invalidate(self, server: str) -> bool:
        try:
            self._path(server).unlink()
        except OSError:
            return False
        return True
