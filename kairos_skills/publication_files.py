"""Staging e versões anteriores por descritores no filesystem da instalação."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from dataclasses import replace

from kairos_filesystem.descriptors import check_chain, open_directory, open_fd, rename_no_replace
from kairos_filesystem.tree import (
    _create_directory,
    _metadata,
    assert_creation_supported,
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


def capture_source(path):
    with publication_errors(), ExitStack() as stack:
        parent, chain = open_directory(stack, path.parent)
        result = capture_skill(parent, path.name)
        check_chain(chain)
        return result


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

    def sync_parents(self, *, guard):
        _expect(callable(guard), "Guard obrigatório.")
        with publication_errors():
            self.check()
            for fd in (self._root, self._installed, self._area, *self._operations.values()):
                guard()
                self.check()
                os.fsync(fd)
