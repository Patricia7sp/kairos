"""Configuração em cascata, com precedência declarada.

`_reversa_sdd/hermes-cli/` §2 (Tarefa 15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

__all__ = [
    "ConfigLayer",
    "ConfigResolver",
    "Migration",
    "apply_migrations",
    "resolve_value",
]


class ConfigLayer(IntEnum):
    """Precedência: **menor número vence**.

    A ordem é declarada como enum, e não implícita na ordem de um `if`, para
    que dê para testá-la e para que acrescentar uma camada seja uma mudança
    visível.
    """

    CLI_FLAG = 1
    ENV_VAR = 2
    PROFILE_CONFIG = 3
    GLOBAL_CONFIG = 4
    DEFAULTS = 5


@dataclass
class ConfigResolver:
    cli_flags: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    profile_config: dict[str, Any] = field(default_factory=dict)
    global_config: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)

    def resolve(self, key: str) -> tuple[Any, ConfigLayer | None]:
        """Devolve `(valor, camada)`.

        Devolver a **camada** junto é o que torna a configuração depurável:
        "por que este valor?" tem resposta sem o usuário adivinhar em qual
        dos cinco lugares olhar.
        """
        if key in self.cli_flags:
            return self.cli_flags[key], ConfigLayer.CLI_FLAG

        env_key = f"KAIROS_{key.upper().replace('.', '_')}"
        if env_key in self.env:
            return self.env[env_key], ConfigLayer.ENV_VAR

        for camada, fonte in (
            (ConfigLayer.PROFILE_CONFIG, self.profile_config),
            (ConfigLayer.GLOBAL_CONFIG, self.global_config),
            (ConfigLayer.DEFAULTS, self.defaults),
        ):
            valor = _dig(fonte, key)
            if valor is not None:
                return valor, camada

        return None, None


def resolve_value(resolver: ConfigResolver, key: str) -> Any:
    return resolver.resolve(key)[0]


def _dig(tree: dict[str, Any], dotted: str) -> Any:
    atual: Any = tree
    for parte in dotted.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return None
        atual = atual[parte]
    return atual


@dataclass(frozen=True)
class Migration:
    """Migração de `config.yaml`, **table-driven**.

    Sequencial e versionada. Uma migração escrita como `if` espalhado pelo
    carregador roda em ordem imprevisível e não dá para saber em que versão o
    arquivo está.
    """

    version: int
    description: str
    apply: Any  # Callable[[dict], dict]


def apply_migrations(
    config: dict[str, Any], migrations: tuple[Migration, ...]
) -> tuple[dict[str, Any], int]:
    """Aplica as pendentes em ordem. Idempotente."""
    atual = int(config.get("config_version", 0))
    saida = dict(config)
    for m in sorted(migrations, key=lambda x: x.version):
        if m.version <= atual:
            continue
        saida = m.apply(saida)
        saida["config_version"] = m.version
        atual = m.version
    return saida, atual
