"""Leitura estrita e compare-and-apply de uma única entrada do manifesto."""

from __future__ import annotations

import os
import re
import uuid
from contextlib import ExitStack
from dataclasses import dataclass

from kairos_filesystem.descriptors import check_chain, open_directory, open_fd
from kairos_filesystem.tree import _metadata, _stamp, observed_mount_id
from kairos_skills.mutation_contract import SkillMutationError, require, validate_name
from kairos_skills.publication_io import publication_errors

MANIFEST_NAME = ".bundled_manifest"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def _conflict():
    raise SkillMutationError("conflict", "Manifesto alterado; entradas preservadas.")


def _parse(raw):
    try:
        result = {}
        for raw_line in raw.decode("utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, digest = line.partition(":")
            name, digest = name.strip(), digest.strip()
            validate_name(name)
            require(name not in result, "Entrada duplicada no manifesto.")
            require(
                not separator or digest == "" or re.fullmatch(r"[0-9a-f]{32}", digest) is not None,
                "Digest inválido no manifesto.",
            )
            result[name] = digest
        return result
    except UnicodeError:
        raise SkillMutationError("input", "Manifesto não é UTF-8 válido.") from None


@dataclass(frozen=True)
class ManifestObservation:
    path: object
    stamp: tuple | None
    mount: int | None

    def check(self):
        with publication_errors(), ExitStack() as stack:
            parent, chain = open_directory(stack, self.path.parent)
            try:
                fd = open_fd(stack, self.path.name, os.O_RDONLY | os.O_NONBLOCK, parent=parent)
            except FileNotFoundError:
                if self.stamp is not None:
                    _conflict()
                check_chain(chain)
                return
            mount = observed_mount_id(fd)
            if (
                self.stamp is None
                or mount != self.mount
                or _stamp(_metadata(fd, mount)) != self.stamp
            ):
                _conflict()
            check_chain(chain)


def observe_manifest(home):
    path = home / "skills" / MANIFEST_NAME
    with publication_errors(), ExitStack() as stack:
        parent, chain = open_directory(stack, path.parent)
        try:
            fd = open_fd(stack, path.name, os.O_RDONLY | os.O_NONBLOCK, parent=parent)
        except FileNotFoundError:
            check_chain(chain)
            return {}, ManifestObservation(path, None, None)
        mount = observed_mount_id(fd)
        before = _metadata(fd, mount)
        require(0 <= before.st_size <= MAX_MANIFEST_BYTES, "Manifesto excede o limite.")
        raw = bytearray()
        while chunk := os.read(fd, min(65536, MAX_MANIFEST_BYTES + 1 - len(raw))):
            raw.extend(chunk)
            require(len(raw) <= MAX_MANIFEST_BYTES, "Manifesto excede o limite.")
        observation = ManifestObservation(path, _stamp(before), mount)
        observation.check()
        check_chain(chain)
        return _parse(bytes(raw)), observation


def apply_manifest_delta(home, delta, *, guard):
    values, observation = observe_manifest(home)
    current = values.get(delta.name)
    if current not in (delta.before, delta.after):
        _conflict()
    guard()
    observation.check()
    with publication_errors(), ExitStack() as stack:
        parent, chain = open_directory(stack, observation.path.parent)
        if current != delta.after:
            if delta.after is None:
                values.pop(delta.name, None)
            else:
                values[delta.name] = delta.after
            temporary = f".bundled-manifest-{uuid.uuid4().hex}"
            fd = open_fd(
                stack, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, parent=parent, mode=0o600
            )
            try:
                data = "".join(
                    f"{name}:{digest}\n" for name, digest in sorted(values.items())
                ).encode()
                while data:
                    written = os.write(fd, data)
                    require(written > 0, "Manifesto não pôde ser escrito.")
                    data = data[written:]
                os.fsync(fd)
                guard()
                check_chain(chain)
                observation.check()
                os.replace(temporary, observation.path.name, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        # Repetição após replace e antes da confirmação também exige durabilidade.
        elif observation.stamp is not None:
            fd = open_fd(stack, observation.path.name, os.O_RDONLY | os.O_NONBLOCK, parent=parent)
            os.fsync(fd)
        os.fsync(parent)
        guard()
        check_chain(chain)
        after, observed = observe_manifest(home)
        if after != values:
            _conflict()
        observed.check()


def reconcile_manifest(home, record, *, guard):
    delta = record.projection_delta or record.draft.manifest_delta
    require(delta is not None, "Publicação sem delta de manifesto.")
    apply_manifest_delta(home, delta, guard=guard)
