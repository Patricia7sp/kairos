"""Hub de skills: download, quarentena e instalação.

`_reversa_sdd/skills/` (Tarefa 09).

**A quarentena não é gatilho de suspeita — é etapa obrigatória do pipeline.**
Todo bundle baixado passa por ela: download → quarentena → escaneamento →
instalação. Não há caminho alternativo, e é isso que torna a defesa confiável:
uma quarentena condicional só protege contra o que a condição prevê.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairos_skills.org_policy_contract import OrgPolicyAction
from kairos_skills.org_policy_guard import publication_guard
from kairos_skills.publication_io import capture_publication, move_publication

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
    files = validated_bundle_files(bundle)
    dest = quarantine_dir / bundle.name
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    try:
        dest.mkdir(mode=0o700)
    except FileExistsError:
        raise QuarantineError("Entrada de quarentena ocupada; aquisição preservada.") from None

    root = dest.resolve()
    for rel, data in files.items():
        target = (dest / rel).resolve()
        # Um nome de arquivo dentro do bundle é entrada NÃO CONFIÁVEL: um
        # `../../.bashrc` escaparia da quarentena antes de qualquer scan.
        if not _within(target, root):
            raise UnsafeQuarantinePath(f"caminho escapa da quarentena: {rel!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dest


def validated_bundle_files(bundle: SkillBundle) -> dict[str, bytes]:
    """Valida todos os componentes antes de criar uma entrada de quarentena."""
    from kairos_skills.mutation_contract import SkillMutationError, require, validate_name
    from kairos_skills.removal_contract import (
        MAX_COMPONENT_BYTES,
        MAX_FILE_BYTES,
        MAX_TREE_BYTES,
        MAX_TREE_DEPTH,
        MAX_TREE_ENTRIES,
    )

    try:
        require(type(bundle) is SkillBundle)
        validate_name(bundle.name)
        require(type(bundle.files) is dict and 0 < len(bundle.files) < MAX_TREE_ENTRIES)
        files = dict(bundle.files)
        directories = {""}
        total = 0
        for path, data in files.items():
            require(type(path) is str and "\x00" not in path)
            parts = path.split("/")
            if not parts or any(part in ("", ".", "..") for part in parts):
                raise UnsafeQuarantinePath("Caminho de bundle inseguro.")
            require(len(parts) <= MAX_TREE_DEPTH)
            require(all(len(part.encode("utf-8")) <= MAX_COMPONENT_BYTES for part in parts))
            require(
                type(data) is bytes
                and len(data) <= (65536 if path == "SKILL.md" else MAX_FILE_BYTES)
            )
            total += len(data)
            require(total <= MAX_TREE_BYTES)
            directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
        require(not set(files) & directories and len(files) + len(directories) <= MAX_TREE_ENTRIES)
        return files
    except (SkillMutationError, UnicodeError):
        raise QuarantineError("Bundle inválido ou fora dos limites de skills.") from None


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


def install_from_quarantine(
    quarantine_path: Path, quarantine_dir: Path, skills_dir: Path, *, policy
) -> Path:
    """Promove da quarentena para o diretório de skills.

    Duas defesas, e as duas são necessárias:

    1. **Contenção de caminho** — o caminho resolvido tem de estar sob a
       quarentena resolvida. Sem `resolve()`, um `..` no meio passaria.
    2. **Rejeição de symlink** — reconferida na promoção, não só no scan:
       entre o escaneamento e a promoção o conteúdo pode ter mudado.

    Destino ocupado recusa com conflito, preservando instalação e quarentena.
    Substituições dependem da coordenação transacional de publicação.
    """
    with publication_guard(policy, skills_dir.parent) as guard:
        return _promote_quarantine(quarantine_path, quarantine_dir, skills_dir, guard=guard)


def _promote_quarantine(quarantine_path, quarantine_dir, skills_dir, *, guard):
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
    guard.check(resolved.name, OrgPolicyAction.PROMOTE)
    if skills_dir.is_symlink():
        raise UnsafeQuarantinePath("Diretório de skills não pode ser um link.")
    source_proof = capture_publication(resolved)
    skills_dir.mkdir(parents=True, exist_ok=True)
    move_publication(
        resolved,
        dest,
        source_proof,
        guard=lambda: guard.check(resolved.name, OrgPolicyAction.PROMOTE),
    )
    return dest


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
