"""Armazenamento isolado por plugin.

`_reversa_sdd/plugins/` §3 (Tarefa 10).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

__all__ = ["plugin_data_dir", "plugin_db", "plugin_root"]


def _kairos_home() -> Path:
    home = os.environ.get("KAIROS_HOME")
    return Path(home).expanduser() if home else Path.home() / ".kairos"


def plugin_root() -> Path:
    """`<kairos home>/plugin-data/`.

    **Deliberadamente fora do diretório de instalação do plugin.** Aquela
    árvore é gerenciada pelo gerenciador de plugins: `plugins remove` a apaga
    e `plugins update` faz git-pull dentro dela. Dado de usuário estacionado
    ali **morre junto com o código que o escreveu** — e o usuário não tem
    como saber disso antes de acontecer.
    """
    return _kairos_home() / "plugin-data"


def plugin_data_dir(name: str) -> Path:
    """Diretório do plugin, criado se preciso.

    `KAIROS_HOME` é resolvido **a cada chamada**, não cacheado: o perfil
    ativo pode mudar no meio da vida do processo, e um caminho cacheado
    escreveria os dados do perfil novo dentro do antigo.
    """
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"nome de plugin inválido: {name!r}")
    d = plugin_root() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def plugin_db(name: str) -> sqlite3.Connection:
    """SQLite isolado por plugin, com WAL e FKs.

    Um banco por plugin, e não uma tabela no `state.db`: assim o dado do
    plugin sobrevive a install/update/remove **e** um plugin não consegue ler
    o que outro guardou.
    """
    conn = sqlite3.connect(plugin_data_dir(name) / "data.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        conn.execute("PRAGMA journal_mode=DELETE")
    return conn
