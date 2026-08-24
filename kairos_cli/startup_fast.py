"""Fast path de boot — **stdlib-only, e isso é testado**.

`_reversa_sdd/hermes-cli/` §1 (Tarefa 15).

Este módulo é importado **antes** da parede de imports pesados (config, árvore
de argparse, logging, providers). Ele só faz sondagem de arquivo com `os` e
`sys`. Sem yaml, sem config, sem argparse.

**A classe de bug que ele elimina** está documentada no legado e vale
reproduzir: a impressão de versão era reimplementada como cópias `*_fast()` no
topo do `main`, cada uma duplicando resolução de raiz de projeto, detecção de
container e de perfil. As cópias **divergiram** — um commit mudou a saída
canônica e referenciou dentro da função rápida um símbolo que não existe no
fast path, de modo que `--version` dava `NameError` naquele caminho *"and
nobody noticed"*.

Uma implementação única, importada pelos dois caminhos, torna a divergência
**estruturalmente impossível**.
"""

from __future__ import annotations

import os
import sys

__all__ = [
    "KAIROS_VERSION",
    "container_mode_marker_exists",
    "fast_version_line",
    "profile_name",
    "resolve_kairos_home",
]

KAIROS_VERSION = "0.1.0"


def resolve_kairos_home() -> str:
    """`$KAIROS_HOME`, ou `~/.kairos`. Só `os`."""
    home = os.environ.get("KAIROS_HOME")
    if home:
        return os.path.expanduser(home)
    return os.path.join(os.path.expanduser("~"), ".kairos")


def profile_name() -> str | None:
    """Perfil ativo pelo caminho do home. Sondagem, não parse."""
    home = resolve_kairos_home()
    marcador = os.path.join(home, ".profile-name")
    try:
        with open(marcador, encoding="utf-8") as fh:
            nome = fh.read().strip()
    except OSError:
        return None
    return nome or None


def container_mode_marker_exists() -> bool:
    """**Só sonda a existência.** O parse autoritativo é do caminho lento.

    O fast path erra **em direção ao caminho lento**: se o arquivo existe, o
    caminho lento decide o que ele significa. As suposições de formato entre
    os dois precisam ser mantidas em sincronia, e é por isso que aqui há
    apenas um `stat` — quanto menos este módulo souber do formato, menos há
    para dessincronizar.
    """
    return os.path.isfile(os.path.join(resolve_kairos_home(), ".container-mode"))


def fast_version_line() -> str:
    """A **única** implementação da linha de versão.

    Importada tanto pelo fast path quanto pelo caminho completo. Duplicá-la
    foi exatamente o bug descrito no topo do módulo.
    """
    partes = [f"kairos {KAIROS_VERSION}"]
    perfil = profile_name()
    if perfil:
        partes.append(f"(perfil: {perfil})")
    if container_mode_marker_exists():
        partes.append("[container]")
    return " ".join(partes)


def heavy_modules_loaded() -> set[str]:
    """Módulos pesados presentes em `sys.modules`.

    Alimenta o teste de guarda: importar este módulo num subprocesso limpo
    **não pode** trazer nenhum deles. Sem a guarda, a leveza se perde na
    primeira vez que alguém adiciona um import "só para uma coisinha".
    """
    pesados = {
        "yaml",
        "argparse",
        "logging.config",
        "openai",
        "pydantic",
        "fastapi",
        "rich",
        "httpx",
        "requests",
    }
    return pesados & set(sys.modules)
