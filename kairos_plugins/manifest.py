"""Manifesto declarativo v2.

`_reversa_sdd/plugins/` §1 (Tarefa 10).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "VALID_PLUGIN_KINDS",
    "Manifest",
    "ManifestError",
    "PluginKind",
    "PluginState",
    "load_manifest",
    "resolve_state",
]


class PluginKind(StrEnum):
    STANDALONE = "standalone"
    BACKEND = "backend"
    EXCLUSIVE = "exclusive"
    PLATFORM = "platform"
    MODEL_PROVIDER = "model-provider"


VALID_PLUGIN_KINDS: frozenset[str] = frozenset(PluginKind)


class PluginState(StrEnum):
    ACTIVE = "active"
    #: Requisito de ambiente ausente. **Falha suave**: some das capacidades
    #: ativas mas continua visível no dashboard, dizendo o que falta.
    DISABLED_MISSING_ENV = "disabled_missing_env"
    DISABLED = "disabled"


class ManifestError(ValueError):
    """Manifesto estruturalmente inválido."""


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    kind: PluginKind = PluginKind.STANDALONE
    author: str | None = None
    license: str | None = None
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ()
    provides_tools: tuple[str, ...] = ()
    provides_hooks: tuple[str, ...] = ()
    python_dependencies: tuple[str, ...] = ()
    config_schema: dict = field(default_factory=dict)
    #: Capacidade declarada (G-21): receber dump de erro do provedor sem
    #: redação. Default falso — quem precisa, pede.
    requires_raw_error: bool = False


def load_manifest(path_or_data: Path | dict, *, name_hint: str | None = None) -> Manifest:
    """Lê e valida um `plugin.yaml` v2.

    **`kind` desconhecido não rejeita o plugin**: gera warning e é coagido
    para `standalone`. Rejeitar quebraria todo plugin escrito contra uma
    versão futura que tenha um `kind` novo — e a coerção degrada para o
    comportamento mais restrito, não para o mais permissivo.
    """
    if isinstance(path_or_data, dict):
        data = path_or_data
    else:
        import yaml

        try:
            data = yaml.safe_load(Path(path_or_data).read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise ManifestError(f"não foi possível ler {path_or_data}: {exc}") from exc
        except Exception as exc:
            raise ManifestError(f"{path_or_data} não é YAML válido: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("o manifesto deve ser um mapa")

    nome = str(data.get("name") or name_hint or "").strip()
    if not nome:
        raise ManifestError("manifesto sem 'name'")
    versao = str(data.get("version") or "").strip()
    if not versao:
        raise ManifestError(f"{nome}: manifesto sem 'version'")

    kind_raw = str(data.get("kind") or PluginKind.STANDALONE)
    if kind_raw in VALID_PLUGIN_KINDS:
        kind = PluginKind(kind_raw)
    else:
        logger.warning(
            "plugin %r declara kind desconhecido %r; coagindo para 'standalone'",
            nome,
            kind_raw,
        )
        kind = PluginKind.STANDALONE

    # Provedor de memória instalado pelo usuário é auto-coagido para
    # `exclusive`, para ir à descoberta de plugins/memory.
    if data.get("memory_provider") and kind is PluginKind.STANDALONE:
        kind = PluginKind.EXCLUSIVE

    hooks = tuple(data.get("provides_hooks") or ())
    from kairos_plugins.hooks import VALID_HOOKS

    desconhecidos = [h for h in hooks if h not in VALID_HOOKS]
    if desconhecidos:
        raise ManifestError(
            f"{nome}: declara hook(s) inexistente(s): {desconhecidos}. "
            f"Um hook que nunca dispara é pior que hook nenhum — o plugin "
            f"parece instalado e não faz nada."
        )

    return Manifest(
        name=nome,
        version=versao,
        kind=kind,
        author=data.get("author"),
        license=data.get("license"),
        requires_env=tuple(data.get("requires_env") or ()),
        optional_env=tuple(data.get("optional_env") or ()),
        provides_tools=tuple(data.get("provides_tools") or ()),
        provides_hooks=hooks,
        python_dependencies=tuple(data.get("python_dependencies") or ()),
        config_schema=data.get("config_schema") or {},
        requires_raw_error=bool(data.get("requires_raw_error")),
    )


def resolve_state(
    manifest: Manifest, env: dict[str, str] | None = None
) -> tuple[PluginState, tuple[str, ...]]:
    """Estado do plugin e o que falta. **Falha suave.**

    Devolve `(estado, variáveis_faltando)`. Um plugin sem a chave de API não
    é erro de instalação — é configuração pendente, e o dashboard precisa
    dizer *o quê* falta em vez de só omiti-lo.
    """
    ambiente = env if env is not None else os.environ
    faltando = tuple(v for v in manifest.requires_env if not (ambiente.get(v) or "").strip())
    if faltando:
        return PluginState.DISABLED_MISSING_ENV, faltando
    return PluginState.ACTIVE, ()
