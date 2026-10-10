"""Árvores de skills por descritores, sem validação de conteúdo editorial."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_filesystem.contract import TreeCapture, TreeLimits
from kairos_filesystem.tree import (
    _create_directory,
    _metadata,
    assert_creation_supported,
    capture_tree,
    observed_mount_id,
    restore_tree,
)
from kairos_skills.mutation_contract import SkillMutationError, validate_id, validate_name
from kairos_skills.mutation_io import (
    _filesystem_errors,
    check_chain,
    io_errors,
    open_directory,
    open_fd,
    rename_no_replace,
    same_entry,
)
from kairos_skills.removal_contract import (
    MAX_FILE_BYTES,
    MAX_TREE_BYTES,
    MAX_TREE_DEPTH,
    MAX_TREE_ENTRIES,
    validate_capture,
)

LIMITS = TreeLimits(MAX_TREE_BYTES, MAX_FILE_BYTES, MAX_TREE_ENTRIES, MAX_TREE_DEPTH)


@contextmanager
def _observed_tree_errors():
    try:
        yield
    except SkillMutationError as error:
        if error.kind == "input":
            raise SkillMutationError("conflict", str(error)) from None
        raise


def read_skill_file(parent: int, name: str, limit: int, *, identity: tuple[int, int]):
    with ExitStack() as stack:
        fd = open_fd(stack, name, os.O_RDONLY | os.O_NONBLOCK, parent=parent)
        before = _metadata(fd, observed_mount_id(parent))
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != limit
            or (before.st_dev, before.st_ino) != identity
        ):
            raise SkillMutationError("conflict", "Arquivo alterado antes da leitura.")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = _metadata(fd, observed_mount_id(parent))
        if len(data) != limit or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_mode,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode):
            raise SkillMutationError("conflict", "Arquivo alterado durante leitura.")
        same_entry(parent, name, fd)
        return bytes(data), after


def capture_skill(parent: int, name: str) -> TreeCapture:
    """Atesta limites antes de alocar bytes, inclusive o limite próprio de SKILL.md."""
    with _observed_tree_errors(), io_errors(), _filesystem_errors(), ExitStack() as stack:
        proof = capture_tree(parent, name, limits=LIMITS, include_contents=False)
        validate_capture(proof)
        root = open_fd(stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
        expected = proof.entries[0]
        observed = _metadata(root, expected.mount_id)
        if (observed.st_dev, observed.st_ino) != (expected.device, expected.inode):
            raise SkillMutationError("conflict", "Árvore substituída durante captura.")
        directories = {"": root}
        contents = {}
        for entry in proof.entries[1:]:
            parent_path, _, component = entry.path.rpartition("/")
            directory = directories[parent_path]
            if entry.kind == "directory":
                fd = open_fd(stack, component, os.O_RDONLY | os.O_DIRECTORY, parent=directory)
                value = _metadata(fd, expected.mount_id)
                if (value.st_dev, value.st_ino) != (entry.device, entry.inode):
                    raise SkillMutationError("conflict", "Diretório substituído durante captura.")
                same_entry(directory, component, fd)
                directories[entry.path] = fd
            else:
                data, value = read_skill_file(
                    directory, component, entry.size, identity=(entry.device, entry.inode)
                )
                if (value.st_dev, value.st_ino) != (entry.device, entry.inode):
                    raise SkillMutationError("conflict", "Arquivo substituído durante captura.")
                contents[entry.path] = data
        result = TreeCapture(proof.entries, contents)
        validate_capture(result, contents=True)
        same_entry(parent, name, root)
        final = capture_tree(parent, name, limits=LIMITS, include_contents=False)
        if final.entries != proof.entries:
            raise SkillMutationError("conflict", "Árvore alterada durante captura.")
        return result


class SkillRemovalFiles:
    """O chamador detém o lock; IDs desconhecidos nunca são descobertos ou limpos."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.stack = ExitStack()
        self.chain = []
        self.mounts = {}
        self.private_directories = set()

    def _directory(self, parent: int, name: str, *, private: bool = False) -> int:
        try:
            fd = open_fd(self.stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
        except FileNotFoundError:
            self.check()
            fd = _create_directory(self.stack, parent, name, observed_mount_id(parent))
            os.fsync(parent)
        self.chain.append((parent, name, fd))
        self.mounts[fd] = observed_mount_id(fd)
        value = _metadata(fd, observed_mount_id(parent))
        if private and stat.S_IMODE(value.st_mode) != 0o700:
            raise SkillMutationError("conflict", "Área privada exige permissões 0700.")
        if private:
            self.private_directories.add(fd)
        return fd

    def __enter__(self):
        try:
            with io_errors(), _filesystem_errors():
                self.root, self.chain = open_directory(self.stack, self.home)
                self.mounts = {fd: observed_mount_id(fd) for _, _, fd in self.chain}
                self.mounts[self.root] = observed_mount_id(self.root)
                assert_creation_supported(self.root)
                self.installed = self._directory(self.root, "skills")
                private = self._directory(self.root, ".skill-mutations", private=True)
                self.staging = self._directory(private, "staging", private=True)
                self.retired = self._directory(private, "retired", private=True)
                self.check()
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def check(self) -> None:
        with _filesystem_errors():
            check_chain(self.chain)
            if any(observed_mount_id(fd) != mount for fd, mount in self.mounts.items()):
                raise SkillMutationError("conflict", "Montagem alterada; arquivos preservados.")
            for fd in self.private_directories:
                value = _metadata(fd, self.mounts[fd])
                if stat.S_IMODE(value.st_mode) != 0o700:
                    raise SkillMutationError("conflict", "Permissões privadas foram alteradas.")

    def _capture(self, parent: int, name: str) -> TreeCapture | None:
        with io_errors():
            self.check()
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            result = capture_skill(parent, name)
            self.check()
            return result

    def capture_installed(self, name: str) -> TreeCapture | None:
        validate_name(name)
        return self._capture(self.installed, name)

    def capture_private(self, operation_id: str, *, retired: bool) -> TreeCapture | None:
        validate_id(operation_id)
        return self._capture(self.retired if retired else self.staging, operation_id)

    def installed_name_absent(self, name: str) -> bool:
        validate_name(name)
        with io_errors():
            self.check()
            try:
                os.stat(name, dir_fd=self.installed, follow_symlinks=False)
            except FileNotFoundError:
                return True
            return False

    def retired_owned(self, operation_id: str, proof: TreeCapture) -> bool:
        validate_id(operation_id)
        with io_errors(), _filesystem_errors(), ExitStack() as stack:
            self.check()
            try:
                fd = open_fd(stack, operation_id, os.O_RDONLY | os.O_DIRECTORY, parent=self.retired)
            except FileNotFoundError:
                return False
            value = _metadata(fd, observed_mount_id(self.retired))
            same_entry(self.retired, operation_id, fd)
            root = proof.entries[0]
            return (value.st_dev, value.st_ino) == (root.device, root.inode)

    def _move(self, source: int, source_name: str, target: int, target_name: str) -> None:
        with io_errors():
            self.check()
            rename_no_replace(source, source_name, target, target_name)
            self.check()

    def retire(self, name: str, operation_id: str) -> None:
        validate_name(name)
        validate_id(operation_id)
        self._move(self.installed, name, self.retired, operation_id)

    def return_retired(self, operation_id: str, name: str) -> None:
        validate_id(operation_id)
        validate_name(name)
        self._move(self.retired, operation_id, self.installed, name)

    def stage(self, operation_id: str, snapshot: TreeCapture) -> TreeCapture:
        validate_id(operation_id)
        validate_capture(snapshot, contents=True)
        with _observed_tree_errors(), io_errors(), _filesystem_errors():
            self.check()
            assert_creation_supported(self.staging)
            result = restore_tree(self.staging, operation_id, snapshot, guard=self.check)
            validate_capture(result, contents=True)
            self.check()
            os.fsync(self.staging)
            return result

    def publish(self, operation_id: str, name: str) -> None:
        validate_id(operation_id)
        validate_name(name)
        self._move(self.staging, operation_id, self.installed, name)

    def sync_parents(self) -> None:
        with io_errors():
            self.check()
            for fd in (self.installed, self.staging, self.retired):
                os.fsync(fd)
