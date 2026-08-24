"""O contrato de `.container-mode`, do lado do consumidor.

RF-16. Reconstruído de `_reversa_sdd/container/` (Tarefa 07).

**A forma do contrato é ditada pelo custo do fast path.** O CLI sonda o modo
container no caminho de boot, antes de qualquer import pesado — e por isso a
sondagem barata é um `stat` na EXISTÊNCIA do arquivo, não a leitura dele.
O parse autoritativo do conteúdo fica no caminho lento, para quem precisa dos
valores.

É também por isso que o formato é `chave=valor` e não YAML: o fast path não
pode pagar o import de um parser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CONTAINER_MODE_FILENAME",
    "ContainerMode",
    "container_mode_path",
    "in_container",
    "read_container_mode",
]

CONTAINER_MODE_FILENAME = ".container-mode"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _kairos_home() -> Path:
    home = os.environ.get("KAIROS_HOME")
    return Path(home).expanduser() if home else Path.home() / ".kairos"


def container_mode_path() -> Path:
    return _kairos_home() / CONTAINER_MODE_FILENAME


def in_container() -> bool:
    """**Fast path.** Um `stat`, sem leitura, sem parse, sem import.

    Chamado no boot do CLI. Qualquer coisa mais cara que isto apareceria como
    latência em todo comando, inclusive fora de container.
    """
    try:
        return container_mode_path().is_file()
    except OSError:
        return False


@dataclass(frozen=True)
class ContainerMode:
    runtime: str
    uid: int
    gid: int
    home: Path
    supervised: bool
    unknown: dict[str, str]

    @property
    def degraded(self) -> bool:
        """Rodando em container **sem** árvore de supervisão.

        É o caminho não-PID-1 (Fly, `--init`, alguns Nomad/K8s). Gateway e
        dashboard não sobem ali, e quem pergunta "por que o dashboard não
        responde?" precisa desta resposta, não de um timeout.
        """
        return not self.supervised


def read_container_mode(path: Path | None = None) -> ContainerMode | None:
    """**Caminho lento.** Parse autoritativo. Devolve `None` fora de container.

    Tolerante por desenho: linha malformada, chave desconhecida e valor não
    numérico não levantam. O arquivo é escrito por um script de bootstrap em
    shell, e uma divergência de formato entre as duas pontas não pode impedir
    o CLI de subir — ela degrada para o default.
    """
    target = path if path is not None else container_mode_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return None

    fields: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()

    known = {"runtime", "uid", "gid", "home", "supervised"}
    return ContainerMode(
        runtime=fields.get("runtime", "unknown"),
        uid=_int_or(fields.get("uid"), 10000),
        gid=_int_or(fields.get("gid"), 10000),
        home=Path(fields.get("home") or _kairos_home()),
        # Ausente conta como supervisionado: é o caminho normal, e assumir
        # degradação faria o CLI avisar sobre um problema inexistente.
        supervised=fields.get("supervised", "1").lower() in _TRUTHY,
        unknown={k: v for k, v in fields.items() if k not in known},
    )


def _int_or(value: str | None, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
