"""Sincronização de skills bundled — manifesto v2.

RF-08 (parcial). `_reversa_sdd/skills/` (Tarefa 09).

**Esta política não é decisão nova do Kairos — é herança.** A pergunta 8
registrou "a edição local vence e a sincronização apenas avisa" como decisão
de produto; ao implementar, verificou-se que `tools/skills_sync.py` do legado
já faz exatamente isso. A spec foi corrigida.

O manifesto guarda, por skill, o **hash de origem no momento do último sync**.
Esse detalhe é o que faz o mecanismo funcionar.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

__all__ = [
    "MANIFEST_NAME",
    "NO_BUNDLED_SKILLS_MARKER",
    "Outcome",
    "SyncResult",
    "origin_hash",
    "read_manifest",
    "sync_bundled_skills",
    "write_manifest",
]

MANIFEST_NAME = ".bundled_manifest"

#: Marcador de opt-out por perfil. Presente ⇒ zero seeding.
NO_BUNDLED_SKILLS_MARKER = ".no-bundled-skills"


class Outcome(StrEnum):
    COPIED = "copied"  # nova: copiada, hash registrado
    UPDATED = "updated"  # bundled mudou e o usuário nunca tocou
    SKIPPED = "skipped"  # bundled inalterado
    USER_MODIFIED = "user_modified"  # o usuário editou → preservado
    USER_DELETED = "user_deleted"  # o usuário apagou → respeitado
    CLEANED = "cleaned"  # sumiu do bundled → limpa do manifesto


@dataclass
class SyncResult:
    copied: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    user_modified: list[str] = field(default_factory=list)
    user_deleted: list[str] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)
    total_bundled: int = 0
    #: Distingue "optou por sair" de "sincronizou 0 / falhou".
    skipped_opt_out: bool = False


def origin_hash(skill_dir: Path) -> str:
    """Hash do conteúdo de uma skill.

    Sobre **todos** os arquivos, em ordem estável — uma skill é um diretório
    (`SKILL.md` mais `references/`, `scripts/`, `templates/`), e hashear só o
    `SKILL.md` deixaria a edição de um script passar por "inalterada".
    """
    h = hashlib.md5(usedforsecurity=False)
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(skill_dir)).encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    """Lê o manifesto. **Migra v1 automaticamente.**

    v1 era só o nome por linha; v2 é `nome:hash`. Uma entrada v1 vira hash
    vazio, e o hash vazio é o que dispara a migração no sync seguinte —
    trata-se a skill como "origem desconhecida", que é conservador: sem saber
    o hash de origem, não dá para afirmar que o usuário não a editou.
    """
    out: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        name, sep, digest = entry.partition(":")
        out[name.strip()] = digest.strip() if sep else ""
    return out


def write_manifest(path: Path, manifest: dict[str, str]) -> None:
    """Escreve **atomicamente**, em v2.

    Um manifesto meio escrito é pior que nenhum: as skills sem entrada seriam
    tratadas como novas e re-copiadas por cima da edição do usuário.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "".join(f"{name}:{digest}\n" for name, digest in sorted(manifest.items())),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def sync_bundled_skills(bundled_dir: Path, user_dir: Path) -> SyncResult:
    """Semeia e atualiza, preservando o trabalho do usuário.

    **Os três casos, e o do meio é o inteligente:**

    1. Bundled ainda casa com o `origin_hash` → pula **sem ler** a cópia do
       usuário. É o caminho comum, e ler o diretório inteiro de 82 skills a
       cada boot custaria caro por nada.
    2. Bundled mudou **e** a cópia do usuário casa com o `origin_hash` →
       atualiza. A comparação é contra o hash **de origem registrado**, não
       contra o bundled atual: é isso que prova que o usuário nunca a tocou.
    3. Bundled mudou **e** a cópia do usuário difere → o usuário customizou →
       **preserva e avisa**.

    Exclusão pelo usuário também é decisão que sobrevive: skill no manifesto
    e ausente do diretório do usuário **não é re-adicionada**.
    """
    result = SyncResult()

    if (user_dir / NO_BUNDLED_SKILLS_MARKER).exists():
        result.skipped_opt_out = True
        return result

    if not bundled_dir.is_dir():
        return result

    user_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = user_dir / MANIFEST_NAME
    manifest = read_manifest(manifest_path)

    bundled = {p.name: p for p in sorted(bundled_dir.iterdir()) if (p / "SKILL.md").is_file()}
    result.total_bundled = len(bundled)

    for name, src in bundled.items():
        dest = user_dir / name
        recorded = manifest.get(name)
        current = origin_hash(src)

        if name not in manifest:
            _copy_tree(src, dest)
            manifest[name] = current
            result.copied.append(name)
            continue

        if not dest.exists():
            # O usuário apagou. Decisão dele, e ela sobrevive ao sync.
            result.user_deleted.append(name)
            continue

        if recorded and recorded == current:
            # Caso 1: nem lemos a cópia do usuário.
            result.skipped.append(name)
            continue

        if recorded and origin_hash(dest) == recorded:
            # Caso 2: o usuário nunca tocou — seguro atualizar.
            _copy_tree(src, dest)
            manifest[name] = current
            result.updated.append(name)
            continue

        # Caso 3 (inclui `recorded` vazio, de manifesto v1): preserva.
        result.user_modified.append(name)

    for name in list(manifest):
        if name not in bundled:
            del manifest[name]
            result.cleaned.append(name)

    write_manifest(manifest_path, manifest)
    return result


def _copy_tree(src: Path, dest: Path) -> None:
    import shutil

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
