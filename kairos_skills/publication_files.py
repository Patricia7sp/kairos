"""Staging e versões anteriores por descritores no filesystem da instalação."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import replace

from kairos_filesystem.descriptors import (
    check_chain,
    open_directory,
    open_fd,
    rename_no_replace,
    same_entry,
)
from kairos_filesystem.tree import (
    _create_directory,
    _metadata,
    _stamp,
    assert_creation_supported,
    delete_verified_tree,
    observed_mount_id,
    restore_tree,
)
from kairos_skills.mutation_contract import SkillMutationError, validate_id, validate_name
from kairos_skills.publication_io import publication_errors
from kairos_skills.removal_contract import validate_capture
from kairos_skills.removal_io import capture_skill


def _expect(condition, message="Prova de publicação inválida.", *, kind="input"):
    if not condition:
        raise SkillMutationError(kind, message)


def capture_source(path, *, source_input=False):
    with publication_errors(), ExitStack() as stack:
        parent, chain = open_directory(stack, path.parent)
        result = capture_skill(parent, path.name, observed=not source_input)
        check_chain(chain)
        return result


@contextmanager
def observe_publication_trees(trees):
    """Captura fora da transação; guard de commit verifica só metadados presos a fds."""
    with publication_errors(), ExitStack() as stack:
        checks = []
        for path, proof in trees:
            parent, chain = open_directory(stack, path.parent)
            if proof is None:
                checks.append((parent, path.name, None, None, None, chain))
                continue
            directories = {}
            for entry in proof.entries:
                directory = parent if not entry.path else directories[entry.path.rpartition("/")[0]]
                component = path.name if not entry.path else entry.path.rpartition("/")[2]
                fd = open_fd(
                    stack,
                    component,
                    os.O_RDONLY
                    | os.O_NONBLOCK
                    | (os.O_DIRECTORY if entry.kind == "directory" else 0),
                    parent=directory,
                )
                value = _metadata(fd, entry.mount_id)
                _expect(
                    (value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode))
                    == (entry.device, entry.inode, entry.mode),
                    kind="conflict",
                )
                checks.append((directory, component, fd, _stamp(value), entry.mount_id, chain))
                if entry.kind == "directory":
                    directories[entry.path] = fd
            _expect(capture_source(path).entries == proof.entries, kind="conflict")

        def check():
            with publication_errors(), ExitStack() as current:
                for parent, name, fd, stamp, mount, chain in checks:
                    check_chain(chain)
                    if fd is None:
                        try:
                            os.stat(name, dir_fd=parent, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        raise SkillMutationError("conflict", "Destino antes ausente foi ocupado.")
                    same_entry(parent, name, fd)
                    opened = open_fd(current, name, os.O_PATH, parent=parent)
                    _expect(
                        observed_mount_id(opened) == mount
                        and _stamp(_metadata(fd, mount)) == stamp,
                        kind="conflict",
                    )

        check()
        yield check


class PublicationFiles:
    def __init__(self, home):
        self.home = home
        self._stack = ExitStack()
        self._closed = True
        self._chain = []
        self._mounts = {}
        self._private = set()
        self._operations = {}

    def __enter__(self):
        _expect(self._closed, "Área de publicação já está aberta.")
        try:
            with publication_errors():
                self._root, self._chain = open_directory(self._stack, self.home)
                self._closed = False
                self._mounts = {fd: observed_mount_id(fd) for _, _, fd in self._chain}
                self._mounts[self._root] = observed_mount_id(self._root)
                assert_creation_supported(self._root)
                self._installed = self._directory(self._root, "skills", private=False)
                self._area = self._directory(self._root, ".skill-publications", private=True)
                self.check()
                return self
        except BaseException:
            self._closed = True
            self._stack.close()
            raise

    def __exit__(self, *args):
        self._closed = True
        return self._stack.__exit__(*args)

    def check(self):
        if self._closed:
            raise SkillMutationError("conflict", "Área de publicação encerrada.")
        with publication_errors(), ExitStack() as stack:
            check_chain(self._chain)
            for parent, name, fd in self._chain:
                current = open_fd(stack, name, os.O_PATH, parent=parent)
                _expect(
                    observed_mount_id(current) == self._mounts[fd],
                    "Montagem de publicação alterada.",
                    kind="conflict",
                )
            for fd in self._private:
                _expect(
                    stat.S_IMODE(_metadata(fd, self._mounts[fd]).st_mode) == 0o700,
                    "Área privada alterada.",
                    kind="conflict",
                )

    def _directory(self, parent, name, *, private):
        self.check()
        try:
            fd = open_fd(self._stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
        except FileNotFoundError:
            fd = _create_directory(self._stack, parent, name, observed_mount_id(parent))
            os.fsync(parent)
        self._chain.append((parent, name, fd))
        self._mounts[fd] = observed_mount_id(fd)
        value = _metadata(fd, observed_mount_id(parent))
        _expect(value.st_dev == os.fstat(parent).st_dev, kind="conflict")
        if private:
            _expect(
                stat.S_IMODE(value.st_mode) == 0o700, "Área privada exige 0700.", kind="conflict"
            )
            self._private.add(fd)
        return fd

    def _operation(self, operation_id, *, create=False, guard=None):
        validate_id(operation_id)
        self.check()
        if operation_id not in self._operations:
            try:
                os.stat(operation_id, dir_fd=self._area, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    return None
                guard()
            else:
                if create:
                    raise SkillMutationError(
                        "conflict", "Área de operação ocupada; resíduos preservados."
                    )
            self._operations[operation_id] = self._directory(self._area, operation_id, private=True)
        return self._operations[operation_id]

    def _capture(self, parent, name):
        self.check()
        if parent is None:
            return None
        with publication_errors():
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            result = replace(capture_skill(parent, name), contents=None)
            self.check()
            return result

    def capture_installed(self, name):
        validate_name(name)
        return self._capture(self._installed, name)

    def capture_staged(self, operation_id):
        with publication_errors():
            return self._capture(self._operation(operation_id), "staged")

    def capture_previous(self, operation_id):
        with publication_errors():
            return self._capture(self._operation(operation_id), "previous")

    def check_operation(self, operation_id):
        with publication_errors():
            parent = self._operation(operation_id)
            _expect(parent is not None, "Área da operação ausente.", kind="conflict")
            with os.scandir(parent) as entries:
                _expect(
                    all(entry.name in {"staged", "previous"} for entry in entries),
                    "Artefato privado desconhecido; entrada preservada.",
                    kind="conflict",
                )

    def stage(self, operation_id, source, expected, *, guard):
        validate_id(operation_id)
        validate_capture(expected)
        _expect(callable(guard), "Guard obrigatório.")
        self.check()
        guard()
        snapshot = capture_source(source)
        _expect(snapshot.entries == expected.entries, "Fonte alterada.", kind="conflict")

        def check():
            self.check()
            guard()

        with publication_errors():
            parent = self._operation(operation_id, create=True, guard=check)
            check()
            # restore_tree cria exclusivamente; colisões e resíduos ficam preservados.
            result = restore_tree(parent, "staged", snapshot, guard=check)
            validate_capture(result)
            _expect(
                capture_source(source).entries == expected.entries,
                "Fonte alterada.",
                kind="conflict",
            )
            check()
            os.fsync(parent)
            os.fsync(self._area)
            return replace(result, contents=None)

    def _move(self, parent, name, target, target_name, expected, *, guard):
        validate_capture(expected)
        _expect(callable(guard), "Guard obrigatório.")
        with publication_errors():
            _expect(parent is not None and target is not None, "Artefato ausente.", kind="conflict")
            self.check()
            _expect(
                self._capture(parent, name) == replace(expected, contents=None),
                "Árvore alterada.",
                kind="conflict",
            )
            guard()
            self.check()
            _expect(
                self._capture(parent, name) == replace(expected, contents=None),
                "Árvore substituída antes do movimento.",
                kind="conflict",
            )
            rename_no_replace(parent, name, target, target_name)
            self.check()
            guard()
            _expect(
                self._capture(target, target_name) == replace(expected, contents=None),
                "Árvore alterada.",
                kind="conflict",
            )

    def retire(self, name, operation_id, expected, *, guard):
        validate_name(name)
        with publication_errors():
            parent = self._operation(operation_id, create=True, guard=guard)
            self._move(self._installed, name, parent, "previous", expected, guard=guard)

    def publish(self, operation_id, name, expected, *, guard):
        validate_name(name)
        with publication_errors():
            self._move(
                self._operation(operation_id),
                "staged",
                self._installed,
                name,
                expected,
                guard=guard,
            )

    def return_previous(self, operation_id, name, expected, *, guard):
        validate_name(name)
        with publication_errors():
            self._move(
                self._operation(operation_id),
                "previous",
                self._installed,
                name,
                expected,
                guard=guard,
            )

    def discard_staged(self, operation_id, expected, *, guard):
        with publication_errors():
            parent = self._operation(operation_id)
            _expect(parent is not None, kind="conflict")

            def check():
                self.check()
                guard()

            delete_verified_tree(parent, "staged", expected, guard=check)

    def sync_parents(self, *, guard):
        _expect(callable(guard), "Guard obrigatório.")
        with publication_errors():
            self.check()
            for fd in (self._root, self._installed, self._area, *self._operations.values()):
                guard()
                self.check()
                os.fsync(fd)
