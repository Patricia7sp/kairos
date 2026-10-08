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
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

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
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".bundled-manifest-", delete=False
    ) as output:
        tmp = Path(output.name)
        try:
            output.write("".join(f"{name}:{digest}\n" for name, digest in sorted(manifest.items())))
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            tmp.unlink()
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


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

    with _snapshot_bundle(bundled_dir) as bundled:
        return _sync_discovered(bundled, user_dir)


def _sync_discovered(bundled: dict[str, Path], user_dir: Path) -> SyncResult:
    if user_dir.is_symlink():
        raise ValueError("Diretório de skills não pode ser um link.")
    user_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = user_dir / MANIFEST_NAME
    if manifest_path.is_symlink():
        raise ValueError("Manifesto de skills não pode ser um link.")
    manifest = read_manifest(manifest_path)
    with (
        TemporaryDirectory(prefix=".skill-sync-", dir=user_dir) as temporary,
        ExitStack() as rollback,
    ):
        result = _sync_items(bundled, user_dir, manifest, Path(temporary), rollback)
        write_manifest(manifest_path, manifest)
        rollback.pop_all()
        return result


def _sync_items(bundled, user_dir, manifest, staging, rollback) -> SyncResult:
    result = SyncResult(total_bundled=len(bundled))

    for name, src in bundled.items():
        dest = user_dir / name
        recorded = manifest.get(name)
        current = origin_hash(src)

        if name not in manifest:
            if dest.exists() or dest.is_symlink():
                result.user_modified.append(name)
                continue
            _copy_tree(src, dest, staging, rollback)
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
            _copy_tree(src, dest, staging, rollback)
            manifest[name] = current
            result.updated.append(name)
            continue

        # Caso 3 (inclui `recorded` vazio, de manifesto v1): preserva.
        result.user_modified.append(name)

    for name in list(manifest):
        if name not in bundled:
            del manifest[name]
            result.cleaned.append(name)

    return result


def _copy_tree(src: Path, dest: Path, staging: Path, rollback: ExitStack) -> None:
    import shutil

    directory = staging / dest.name
    directory.mkdir()
    new = directory / "new"
    old = directory / "old"
    shutil.copytree(src, new)
    if dest.exists():
        os.replace(dest, old)
    rollback.callback(_restore_copy, dest, old, directory / "discard")
    os.replace(new, dest)


def _restore_copy(dest: Path, old: Path, discard: Path) -> None:
    if dest.exists() or dest.is_symlink():
        os.replace(dest, discard)
    if old.exists():
        os.replace(old, dest)


@contextmanager
def _directory(name, *, parent=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _entries(fd):
    with os.scandir(fd) as entries:
        return sorted(entries, key=lambda entry: entry.name)


def _is_skill(fd):
    try:
        info = os.stat("SKILL.md", dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("SKILL.md deve ser arquivo regular sem links.")
    return True


@contextmanager
def _snapshot_bundle(bundled_dir: Path):
    with TemporaryDirectory(prefix="kairos-bundle-sync-") as temporary:
        snapshots = {}

        def capture(name, fd):
            if name in snapshots:
                raise ValueError("Nome de skill duplicado no bundle; sincronização recusada.")
            destination = Path(temporary) / name
            _copy_directory(fd, destination)
            snapshots[name] = destination

        try:
            with _directory(bundled_dir) as root:
                for entry in _entries(root):
                    if entry.is_symlink():
                        raise ValueError("Links não são aceitos no bundle de skills.")
                    if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                        continue
                    with _directory(entry.name, parent=root) as directory:
                        if _is_skill(directory):
                            capture(entry.name, directory)
                            continue
                        for child in _entries(directory):
                            if child.is_symlink():
                                raise ValueError("Links não são aceitos no bundle de skills.")
                            if child.name.startswith(".") or not child.is_dir(
                                follow_symlinks=False
                            ):
                                continue
                            with _directory(child.name, parent=directory) as skill:
                                if _is_skill(skill):
                                    capture(child.name, skill)
        except OSError:
            raise ValueError("Não foi possível ler o bundle de skills com segurança.") from None
        yield dict(sorted(snapshots.items()))


def _copy_directory(fd: int, destination: Path) -> None:
    destination.mkdir()
    for entry in _entries(fd):
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with _directory(entry.name, parent=fd) as child:
                _copy_directory(child, destination / entry.name)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            _copy_file(fd, entry.name, destination / entry.name)
        else:
            raise ValueError("Bundle de skills contém link ou arquivo especial.")
    destination.chmod(stat.S_IMODE(os.fstat(fd).st_mode))


def _copy_file(parent: int, name: str, destination: Path) -> None:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("Bundle de skills contém link ou arquivo especial.")
        with destination.open("xb") as output:
            while chunk := os.read(fd, 64 * 1024):
                output.write(chunk)
        after = os.fstat(fd)
        if after.st_nlink != 1 or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Bundle de skills alterado durante a leitura.")
        destination.chmod(stat.S_IMODE(before.st_mode))
    finally:
        os.close(fd)
