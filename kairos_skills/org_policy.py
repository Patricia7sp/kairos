"""Observa e revalida uma fonte local sem delegar autoridade ao conteúdo."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import stat
import uuid
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_filesystem.contract import FilesystemError
from kairos_filesystem.descriptors import check_chain, open_directory, open_fd, same_entry
from kairos_filesystem.tree import observed_mount_id
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, require, validate_name
from kairos_skills.org_policy_contract import (
    OrgBinding,
    OrgPolicyContext,
    OrgPolicyReceipt,
    OrgProtectionBinding,
    validate_org_home,
)

MAX_POLICY_BYTES = 1024 * 1024
_LOGGER = logging.getLogger(__name__)


def _refuse(kind: str, reason: str) -> None:
    _LOGGER.warning(
        "skill_org_policy_refused", extra={"action": "observe", "kind": kind, "reason": reason}
    )
    raise SkillMutationError(kind, "Política organizacional não pôde ser atestada.")


def org_policy_context(environ: Mapping[str, str]) -> OrgPolicyContext:
    if "KAIROS_SKILL_ORG_POLICY" not in environ:
        return OrgPolicyContext("none", None)
    value = environ["KAIROS_SKILL_ORG_POLICY"]
    return OrgPolicyContext("file", validate_org_home(value))


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def parse_org_policy(raw: bytes) -> tuple[OrgBinding, ...]:
    try:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_POLICY_BYTES)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        require(type(value) is dict and set(value) == {"version", "bindings"})
        require(type(value["version"]) is int and value["version"] == 1)
        require(type(value["bindings"]) is list)
        result = []
        seen = set()
        for binding in value["bindings"]:
            require(
                type(binding) is dict
                and set(binding) == {"home", "name", "organization_id", "active"}
            )
            parsed = OrgBinding(**binding)
            pair = parsed.home, parsed.name
            require(pair not in seen)
            seen.add(pair)
            result.append(parsed)
        return tuple(result)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _refuse("input", "invalid_document")


def _stamp(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _existing_chain(stack: ExitStack, path: Path):
    root = open_fd(stack, path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    chain = []
    parent = root
    for component in path.parts[1:]:
        try:
            child = open_fd(stack, component, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
        except FileNotFoundError:
            break
        chain.append((parent, component, child))
        parent = child
    return chain


def _read_source(fd: int, *, revalidating: bool = False) -> tuple[bytes, tuple[int, ...]]:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or not 0 < before.st_size <= MAX_POLICY_BYTES
    ):
        _refuse(
            "conflict" if revalidating else "input",
            "source_changed" if revalidating else "unsafe_source",
        )
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    remaining = MAX_POLICY_BYTES + 1
    while remaining:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) != before.st_size or _stamp(os.fstat(fd)) != _stamp(before):
        _refuse("conflict", "source_changed")
    return raw, _stamp(before)


class OrgPolicySession:
    def __init__(self, context: OrgPolicyContext, home: Path, stack: ExitStack):
        require(type(context) is OrgPolicyContext)
        self._context = context
        self._home = validate_org_home(str(home))
        self.home_id = catalog_home_id(self._home)
        self._closed = False
        self._source = None
        self._bindings = ()
        self._digest = None
        self._observation_id = uuid.uuid4().hex
        self._chain = _existing_chain(stack, self._home)
        if context.mode == "file":
            parent, chain = open_directory(stack, context.source.parent)
            self._chain.extend(chain)
            before = os.stat(context.source.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _refuse("input", "unsafe_source")
            fd = open_fd(stack, context.source.name, os.O_RDONLY | os.O_NONBLOCK, parent=parent)
            raw, stamp = _read_source(fd)
            same_entry(parent, context.source.name, fd)
            if _stamp(before) != stamp:
                _refuse("conflict", "source_changed")
            self._source = parent, context.source.name, fd, stamp
            self._bindings = parse_org_policy(raw)
            self._digest = hashlib.sha256(raw).hexdigest()
            for binding in self._bindings:
                _existing_chain(stack, Path(binding.home))
        self._mounts = {
            fd: observed_mount_id(fd) for edge in self._chain for fd in (edge[0], edge[2])
        }

    @property
    def context(self):
        return self._context

    def revalidate(self) -> None:
        try:
            if self._closed:
                _refuse("conflict", "closed_session")
            check_chain(self._chain)
            for fd, mount in self._mounts.items():
                if observed_mount_id(fd) != mount:
                    _refuse("conflict", "changed_mount")
            with ExitStack() as stack:
                for parent, name, fd in self._chain:
                    current = open_fd(stack, name, os.O_PATH, parent=parent)
                    if observed_mount_id(current) != self._mounts[fd]:
                        _refuse("conflict", "changed_mount")
                _existing_chain(stack, self._home)
            if self._source is not None:
                parent, name, fd, stamp = self._source
                same_entry(parent, name, fd)
                raw, current = _read_source(fd, revalidating=True)
                if current != stamp or hashlib.sha256(raw).hexdigest() != self._digest:
                    _refuse("conflict", "source_changed")
                with ExitStack() as stack:
                    current_fd = open_fd(stack, name, os.O_PATH, parent=parent)
                    if observed_mount_id(current_fd) != observed_mount_id(fd):
                        _refuse("conflict", "changed_mount")
            check_chain(self._chain)
        except (OSError, FilesystemError):
            _refuse("conflict", "changed_entry")

    def receipt(self, name: str) -> OrgPolicyReceipt:
        validate_name(name)
        self.revalidate()
        bindings = tuple(
            OrgProtectionBinding(binding.organization_id, binding.active)
            for binding in self._bindings
            if binding.home == str(self._home) and binding.name == name
        )
        return OrgPolicyReceipt(self.home_id, name, self._observation_id, self._digest, bindings)


@contextmanager
def observe_org_policy(context: OrgPolicyContext, home: Path):
    with ExitStack() as stack:
        try:
            session = OrgPolicySession(context, home, stack)
        except OSError as error:
            kind = "input" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "unavailable"
            _refuse(kind, "source_unavailable")
        except FilesystemError:
            _refuse("unavailable", "observation_unavailable")
        try:
            session.revalidate()
            yield session
        finally:
            session._closed = True
