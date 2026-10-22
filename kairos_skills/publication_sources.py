"""Fontes vivas da composição; metadados do bundle nunca concedem autoridade."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path

from kairos_filesystem.descriptors import check_chain, open_directory, open_fd, same_entry
from kairos_filesystem.tree import _metadata, observed_mount_id
from kairos_skills.mutation_contract import (
    SkillMutationError,
    require,
    validate_actor,
    validate_id,
    validate_name,
)
from kairos_skills.org_policy_guard import attest_policy_home
from kairos_skills.publication_contract import PublicationWriter
from kairos_skills.publication_files import capture_source
from kairos_skills.publication_io import publication_errors
from kairos_skills.removal_contract import validate_capture
from kairos_skills.removal_origin import trusted_bundle_root

_CONTEXT_KEY = object()


def _stamp(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class PublicationContext:
    def __init__(self, key, *, home, root, writer, source_key, actor, policy, replacement, stack):
        require(key is _CONTEXT_KEY, "Fonte exige composição confiável.")
        attest_policy_home(policy, home)
        self._home_id = policy.home_id
        self._home = Path(home).absolute()
        self._writer = writer
        self._source_key = source_key
        self._root = Path(root).absolute()
        self._actor = actor
        self._replacement = replacement
        self._policy = policy
        self._stack = stack
        self._closed = False
        self._fd, self._chain = open_directory(stack, self.root)
        self._mounts = {fd: observed_mount_id(fd) for _, _, fd in self._chain}
        value = _metadata(self._fd, observed_mount_id(self._fd))
        self._root_identity = value.st_dev, value.st_ino, observed_mount_id(self._fd)
        self._bundled = {}
        self._inventory = {}
        self._observations = {}
        if writer is PublicationWriter.BUNDLED_SYNC:
            self._discover(self._fd, self.root, category=True)

    @property
    def home_id(self):
        return self._home_id

    @property
    def writer(self):
        return self._writer

    @property
    def root(self):
        return self._root

    @property
    def root_identity(self):
        return self._root_identity

    @property
    def source_key(self):
        return self._source_key

    @property
    def actor(self):
        return self._actor

    @property
    def replacement(self):
        return self._replacement

    def _discover(self, fd, path, *, category):
        self._inventory[fd] = os.fstat(fd)
        with os.scandir(fd) as entries:
            names = sorted(entry.name for entry in entries if not entry.name.startswith("."))
        for name in names:
            value = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(value.st_mode):
                raise SkillMutationError("conflict", "Link não comprova origem bundled.")
            if not stat.S_ISDIR(value.st_mode):
                continue
            child = open_fd(self._stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=fd)
            self._chain.append((fd, name, child))
            self._mounts[child] = observed_mount_id(child)
            _metadata(child, self.root_identity[2])
            try:
                skill = os.stat("SKILL.md", dir_fd=child, follow_symlinks=False)
            except FileNotFoundError:
                if category:
                    self._discover(child, path / name, category=False)
                continue
            validate_name(name)
            require(stat.S_ISREG(skill.st_mode) and skill.st_nlink == 1)
            if name in self._bundled:
                raise SkillMutationError("conflict", "Nomes bundled duplicados.")
            self._bundled[name] = path / name

    def revalidate(self):
        if self._closed:
            raise SkillMutationError("conflict", "Fonte de publicação encerrada.")
        self._policy.revalidate()
        with publication_errors(), ExitStack() as stack:
            check_chain(self._chain)
            for parent, name, fd in self._chain:
                current = open_fd(stack, name, os.O_PATH, parent=parent)
                if observed_mount_id(current) != self._mounts[fd]:
                    raise SkillMutationError("conflict", "Montagem da fonte alterada.")
            value = _metadata(self._fd, self.root_identity[2])
            if (value.st_dev, value.st_ino, observed_mount_id(self._fd)) != self.root_identity:
                raise SkillMutationError("conflict", "Raiz da fonte alterada.")
            for fd, before in self._inventory.items():
                after = os.fstat(fd)
                if (before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise SkillMutationError("conflict", "Inventário bundled alterado.")
            for chain, handles in self._observations.values():
                check_chain(chain)
                for parent, name, fd in chain:
                    current = open_fd(stack, name, os.O_PATH, parent=parent)
                    if observed_mount_id(current) != observed_mount_id(fd):
                        raise SkillMutationError(
                            "conflict", "Montagem da cadeia da fonte alterada."
                        )
                for parent, name, fd, stamp, mount in handles:
                    same_entry(parent, name, fd)
                    current = open_fd(stack, name, os.O_PATH, parent=parent)
                    if observed_mount_id(current) != mount or _stamp(_metadata(fd, mount)) != stamp:
                        raise SkillMutationError("conflict", "Árvore da fonte alterada.")

    def _track_source(self, path, proof):
        """Guarda identidades e stamps; revalidação de commit nunca copia conteúdo."""
        self.revalidate()
        if path in self._observations:
            return
        parent, chain = open_directory(self._stack, path.parent)
        handles = []
        directories = {}
        for entry in proof.entries:
            directory = parent if not entry.path else directories[entry.path.rpartition("/")[0]]
            component = path.name if not entry.path else entry.path.rpartition("/")[2]
            flags = os.O_RDONLY | os.O_NONBLOCK
            if entry.kind == "directory":
                flags |= os.O_DIRECTORY
            fd = open_fd(self._stack, component, flags, parent=directory)
            value = _metadata(fd, entry.mount_id)
            if (value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode)) != (
                entry.device,
                entry.inode,
                entry.mode,
            ):
                raise SkillMutationError("conflict", "Árvore substituída antes da observação.")
            handles.append((directory, component, fd, _stamp(value), entry.mount_id))
            if entry.kind == "directory":
                directories[entry.path] = fd
        if capture_source(path).entries != proof.entries:
            raise SkillMutationError("conflict", "Árvore alterada durante observação.")
        self._observations[path] = chain, handles
        self.revalidate()

    def contains(self, name):
        validate_name(name)
        self.revalidate()
        require(self.writer is PublicationWriter.BUNDLED_SYNC, "Quarentena exige ID de aquisição.")
        return name in self._bundled

    def resolve(self, name, *, acquisition_id=None):
        validate_name(name)
        self.revalidate()
        if self.writer is PublicationWriter.BUNDLED_SYNC:
            require(acquisition_id is None, "Bundle não aceita recibo de quarentena.")
            require(name in self._bundled, "Skill ausente no pacote.")
            path = self._bundled[name]
            proof = replace(capture_source(path), contents=None)
        else:
            validate_id(acquisition_id)
            from kairos_skills.removal_state import database_connection
            from kairos_state.repositories.skill_publications import SkillPublicationRepository

            with publication_errors(), database_connection(self._home, write=False) as connection:
                require(connection is not None, "Recibo de aquisição ausente.")
                receipt = SkillPublicationRepository(connection, home_id=self.home_id).receipt(
                    acquisition_id
                )
            require(receipt is not None, "Recibo de aquisição ausente.")
            require(
                (receipt.name, receipt.source_key, receipt.home_id)
                == (name, self.source_key, self.home_id)
            )
            if receipt.quarantine_identity != self.root_identity:
                raise SkillMutationError("conflict", "Recibo pertence a outra raiz de quarentena.")
            path = self.root / acquisition_id / name
            proof = replace(capture_source(path), contents=None)
            if proof != receipt.source_proof:
                raise SkillMutationError(
                    "conflict", "Aquisição alterada; recibo não autoriza a fonte."
                )
        self.revalidate()
        self._track_source(path, proof)
        return path, proof


@contextmanager
def observe_bundled_source(home, *, policy):
    with publication_errors(), ExitStack() as stack:
        context = PublicationContext(
            _CONTEXT_KEY,
            home=home,
            root=trusted_bundle_root(),
            writer=PublicationWriter.BUNDLED_SYNC,
            source_key="package_bundle",
            actor=None,
            policy=policy,
            replacement=None,
            stack=stack,
        )
        try:
            context.revalidate()
            yield context
        finally:
            context._closed = True


@contextmanager
def observe_quarantine_source(
    home, quarantine_root, *, source_key, actor, policy, replacement=None
):
    validate_actor(actor)
    validate_id(source_key)
    if replacement is not None:
        validate_capture(replacement)
        require(replacement.contents is None)
    with publication_errors(), ExitStack() as stack:
        context = PublicationContext(
            _CONTEXT_KEY,
            home=home,
            root=quarantine_root,
            writer=PublicationWriter.QUARANTINE_PROMOTE,
            source_key=source_key,
            actor=actor,
            policy=policy,
            replacement=replacement,
            stack=stack,
        )
        try:
            context.revalidate()
            yield context
        finally:
            context._closed = True
