"""Comando `kairos verify` — verificação pós-edição de código.

**Recusa barulhenta, não falsa verificação.** O `verify` do legado detecta a
receita do projeto (manifest), executa as fases bootstrap/build/test e testa a
readiness de porta, registrando evidência. O executor de receitas não foi
portado para o Kairos — conferir dois arquivos não é verificação, e alegar
"Verificação concluída" por causa disso é exatamente o falso sucesso que a
regra do projeto proíbe.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


def _motivo() -> str:
    return (
        ": o executor de receitas (bootstrap/build/test + readiness de porta) "
        "que o verify do legado usa não foi portado. Detectar a receita em "
        ".hermes/environment.json e rodar as fases reais é o efeito; conferir "
        "dois arquivos não é verificação."
    )


async def run_verify(home: Path, args) -> int:
    home = Path(home)
    print(
        f"kairos: verify não implementado{_motivo()}",
        file=sys.stderr,
    )
    return ExitCode.NOT_IMPLEMENTED
