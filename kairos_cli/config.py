"""Configuração em cascata, com precedência declarada.

`_reversa_sdd/hermes-cli/` §2 (Tarefa 15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from kairos_cli.startup_fast import resolve_kairos_home

__all__ = [
    "ConfigLayer",
    "ConfigResolver",
    "Migration",
    "apply_migrations",
    "get_config_path",
    "load_config",
    "open_in_editor",
    "parse_cli_value",
    "resolve_value",
    "save_config",
    "set_config_value",
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


def get_config_path() -> Path:
    # Fonte única de resolução do home: `startup_fast.resolve_kairos_home`
    # (que também faz expanduser). Antes havia um segundo cálculo aqui, à
    # revelia do fast path — duas respostas para "onde mora o Kairos?" divergem
    # na primeira vez que alguém usa `~` em KAIROS_HOME.
    return Path(resolve_kairos_home()) / "config.yaml"


def load_config() -> dict[str, Any]:
    import yaml

    path = get_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_config(config: dict[str, Any]) -> None:
    import os
    import tempfile

    import yaml

    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=str(path.parent), delete=False, encoding="utf-8"
    ) as tf:
        yaml.safe_dump(config, tf, sort_keys=False)
        tmp_name = tf.name
    os.replace(tmp_name, path)


def parse_cli_value(raw: str) -> Any:
    """Valor de `config set` como scalar YAML quando dá, senão string crua.

    `true`, `42`, `null`, `[a, b]` viram os tipos reais; um texto comum
    continua texto. O usuário digita o valor que veria no YAML.
    """
    import yaml

    if not raw.strip():
        return None
    try:
        valor = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    if isinstance(valor, str):
        return raw
    return valor


def set_config_value(config: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    """Grava `value` na chave pontilhada, criando os nós intermediários."""
    partes = dotted.split(".")
    alvo: Any = config
    for parte in partes[:-1]:
        atual = alvo.get(parte)
        if not isinstance(atual, dict):
            atual = {}
            alvo[parte] = atual
        alvo = atual
    alvo[partes[-1]] = value
    return config


def open_in_editor(path: Path | None = None, *, spawn=None, editor: str | None = None) -> None:
    """Abre o config.yaml no `$EDITOR` (fallback `vi`).

    `spawn` é injetável para teste: recebe a lista de argv e devolve o exit
    code do editor. Falha do editor é erro — aceitar silêncio aqui esconderia
    que o usuário não salvou nada.
    """
    import os
    import shlex
    import subprocess

    comando = editor or os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    argv = [*shlex.split(comando), str(path or get_config_path())]
    spawn = spawn or subprocess.call
    if spawn(argv) != 0:
        raise OSError(f"editor '{comando}' encerrado com erro")
