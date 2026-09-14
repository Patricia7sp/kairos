"""Comando `kairos update` — atualiza o Kairos."""
import sys
from pathlib import Path


async def run_update(home: Path, args) -> int:
    home = Path(home)

    # Verifica se há atualização disponível (versão atual vs última versão)
    from kairos_cli.startup_fast import fast_version_line

    current = fast_version_line()
    print(f"Kairos versão atual: {current}")

    print("Verificação automática de atualizações indisponível neste modo.")
    print("Atualize via gerenciador de pacotes ou refaça o deploy a partir do repositório.")

    return 1