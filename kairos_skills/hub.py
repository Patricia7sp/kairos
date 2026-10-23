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

from kairos_skills.mutation_contract import SkillMutationError, require, validate_id
from kairos_skills.publication_contract import PublicationWriter
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import PublicationContext
from kairos_skills.publications import SkillPublicationService

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
    quarantine_path: Path,
    quarantine_dir: Path,
    skills_dir: Path,
    *,
    policy,
    context=None,
    acquisition_id=None,
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
    require(type(context) is PublicationContext, "Contexto de publicação obrigatório.")
    require(Path(skills_dir).name == "skills", "Promoção exige o diretório skills do home.")
    require(
        context.writer is PublicationWriter.QUARANTINE_PROMOTE,
        "Promoção exige contexto de quarentena.",
    )
    validate_id(acquisition_id)
    require(Path(quarantine_dir).absolute() == context.root, "Quarentena diverge da composição.")
    source = Path(quarantine_path).absolute()
    require(
        source == context.root / acquisition_id / source.name,
        "Caminho não corresponde à aquisição.",
    )
    with publication_session(
        Path(skills_dir).absolute().parent, policy=policy, context=context
    ) as session:
        result = SkillPublicationService(session).publish(
            source.name, acquisition_id=acquisition_id
        )
        if not result.complete:
            raise SkillMutationError(
                result.error_kind or "conflict",
                "Publicação pendente; retome o ID indicado.",
                operation_id=result.operation_id,
            )
    return Path(skills_dir) / source.name


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
