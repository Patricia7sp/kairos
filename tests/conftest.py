"""Invariante 11 — um teste nunca toca o `state.db` de produção.

`domain.md` §4 lista isto entre os invariantes cuja violação é "bug por
definição". Ele é diferente dos outros: não é imponível no código de
produção, porque quem o viola é o **teste**. A imposição tem de viver no
harness.

Sem essa guarda, um teste que esqueça de isolar `KAIROS_HOME` escreve — ou
apaga — o banco real do desenvolvedor. O modo de falha é silencioso e
destrutivo: a suíte fica verde e o transcript do usuário some.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _real_home() -> Path:
    return Path.home() / ".kairos"


@pytest.fixture(autouse=True, scope="session")
def _proteger_o_banco_de_producao(tmp_path_factory):
    """Aponta `KAIROS_HOME` para um diretório descartável durante a suíte.

    `autouse` e de escopo de sessão: a proteção não pode depender de cada
    teste lembrar de pedi-la — é justamente o esquecimento que ela cobre.
    """
    anterior = os.environ.get("KAIROS_HOME")
    os.environ["KAIROS_HOME"] = str(tmp_path_factory.mktemp("kairos-home"))
    try:
        yield
    finally:
        if anterior is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = anterior


@pytest.fixture(autouse=True)
def _o_banco_de_producao_nao_foi_tocado():
    """Verifica, a cada teste, que o `state.db` real não mudou.

    A guarda de `KAIROS_HOME` cobre o caminho normal. Esta cobre o resto: um
    caminho absoluto escrito à mão, um `Path.home()` direto, um default que
    escapou. Compara mtime e tamanho antes e depois — barato, e suficiente
    para pegar escrita.
    """
    alvo = _real_home() / "state.db"
    antes = _assinatura(alvo)
    yield
    depois = _assinatura(alvo)
    if antes != depois:
        pytest.fail(
            f"INVARIANTE 11 VIOLADO: o teste tocou o state.db de produção "
            f"({alvo}). antes={antes} depois={depois}"
        )


def _assinatura(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)
