"""Hub de skills: download, quarentena e instalação.

`_reversa_sdd/skills/` (Tarefa 09).

**A quarentena não é gatilho de suspeita — é etapa obrigatória do pipeline.**
Todo bundle baixado passa por ela: download → quarentena → escaneamento →
instalação. Não há caminho alternativo, e é isso que torna a defesa confiável:
uma quarentena condicional só protege contra o que a condição prevê.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "QuarantineError",
    "SkillBundle",
    "UnsafeQuarantinePath",
    "install_from_quarantine",
    "quarantine_bundle",
    "scan_quarantined",
]


class QuarantineError(RuntimeError):
    """Falha no pipeline de quarentena."""


class UnsafeQuarantinePath(QuarantineError):
    """Caminho fora do diretório de quarentena resolvido."""


@dataclass(frozen=True)
class SkillBundle:
    name: str
    files: dict[str, bytes]
    source: str = "hub"
    trust_level: str = "untrusted"


def quarantine_bundle(bundle: SkillBundle, quarantine_dir: Path) -> Path:
    """Grava o bundle na quarentena. Primeiro passo, sempre."""
    if not bundle.name or "/" in bundle.name or bundle.name.startswith("."):
        raise QuarantineError(f"nome de skill inválido: {bundle.name!r}")

    dest = quarantine_dir / bundle.name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    root = dest.resolve()
    for rel, data in bundle.files.items():
        target = (dest / rel).resolve()
        # Um nome de arquivo dentro do bundle é entrada NÃO CONFIÁVEL: um
        # `../../.bashrc` escaparia da quarentena antes de qualquer scan.
        if not _within(target, root):
            raise UnsafeQuarantinePath(f"caminho escapa da quarentena: {rel!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dest


def scan_quarantined(path: Path) -> list[str]:
    """Escaneia o que está em quarentena. Devolve os achados.

    Symlink é rejeitado aqui e de novo na promoção: um link para
    `~/.ssh/id_rsa` dentro de uma skill transforma `skill_view` num leitor de
    arquivo arbitrário.
    """
    achados: list[str] = []
    if not (path / "SKILL.md").is_file():
        achados.append("falta SKILL.md")
    for entry in path.rglob("*"):
        if entry.is_symlink():
            achados.append(f"symlink não permitido: {entry.relative_to(path)}")
    return achados


def install_from_quarantine(quarantine_path: Path, quarantine_dir: Path, skills_dir: Path) -> Path:
    """Promove da quarentena para o diretório de skills.

    Duas defesas, e as duas são necessárias:

    1. **Contenção de caminho** — o caminho resolvido tem de estar sob a
       quarentena resolvida. Sem `resolve()`, um `..` no meio passaria.
    2. **Rejeição de symlink** — reconferida na promoção, não só no scan:
       entre o escaneamento e a promoção o conteúdo pode ter mudado.
    """
    resolved = quarantine_path.resolve()
    root = quarantine_dir.resolve()
    if not _within(resolved, root):
        raise UnsafeQuarantinePath(f"Unsafe quarantine path: {quarantine_path}")

    if not (resolved / "SKILL.md").is_file():
        raise QuarantineError(f"{resolved.name}: falta SKILL.md")

    for entry in resolved.rglob("*"):
        if entry.is_symlink():
            raise UnsafeQuarantinePath(f"symlink em skill promovida: {entry.relative_to(resolved)}")

    dest = skills_dir / resolved.name
    skills_dir.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(resolved), str(dest))
    return dest


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
