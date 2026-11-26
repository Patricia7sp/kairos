"""Captura de uma exportação exata; o consumidor recebe somente a cópia privada."""

from __future__ import annotations

import os
import queue
import re
import secrets
import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from kairos_filesystem.contract import FilesystemError, TreeEntry, TreeLimits
from kairos_filesystem.descriptors import open_directory, open_fd
from kairos_filesystem.tree import capture_authorized_root, observed_mount_id, validate_capture
from kairos_runtime.projects import read_export_manifest
from kairos_runtime.reviews import fingerprint_files, safe_path
from kairos_tools.audit_store import AuthorityDenied, AuthorityStore, Clock, boot_id, require

LIMITS = TreeLimits(32 << 20, 4 << 20, 4096, 32)
DIRECTORY = os.O_DIRECTORY | os.O_RDONLY
READ_SNAPSHOT = "read_snapshot"
APPROVE_EXCEPTION = "approve_audit_exception"
REVOKE_EXCEPTION = "revoke_audit_exception"


def _manifest(catalog_fd, version_fd, revision):
    for fd in (catalog_fd, version_fd):
        s = os.fstat(fd)
        require(s.st_uid in (0, os.geteuid()) and not s.st_mode & 0o022)
    return read_export_manifest(version_fd, revision, trusted_owner=True)["baseline_fingerprint"]


@dataclass(frozen=True, slots=True)
class Snapshot:
    project: str
    revision: str
    fingerprint: str
    entries: tuple[TreeEntry, ...] = field(repr=False)
    contents: Mapping[str, bytes] = field(repr=False)
    export_identity: str = field(repr=False, default="")


def _identity(fd):
    s = os.fstat(fd)
    return f"{boot_id()}:{os.stat('/proc/self/ns/mnt').st_ino}:{s.st_dev}:{s.st_ino}:{observed_mount_id(fd)}"


def _attest_chain(chain):
    for parent, name, retained in chain:
        with ExitStack() as stack:
            current = open_fd(stack, name, os.O_PATH, parent=parent)
            a, b = os.fstat(retained), os.fstat(current)
            require(
                (a.st_dev, a.st_ino, observed_mount_id(retained))
                == (b.st_dev, b.st_ino, observed_mount_id(current))
            )


@contextmanager
def _export(path, revision):
    require(re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", revision))
    with ExitStack() as stack:
        catalog, chain = open_directory(stack, path)
        version = open_fd(stack, revision, DIRECTORY, parent=catalog)
        root = open_fd(stack, "workspace", DIRECTORY, parent=version)
        chain.extend([(catalog, revision, version), (version, "workspace", root)])
        yield catalog, version, root, chain


def current_grants(db, now, principal, project, revision, *, action, ref=None):
    rows = db.execute(
        """SELECT g.*,p.catalog,p.catalog_identity,e.root_identity,e.fingerprint
        FROM grants g JOIN policy ON policy.singleton=1 AND g.epoch=policy.epoch
        JOIN projects p ON p.id=g.project JOIN exports e ON e.project=p.id AND e.revision=g.revision
        WHERE g.principal=? AND g.project=? AND g.revision=? AND g.action=?
        AND g.revoked=0 AND g.boot_id=? AND g.issued<=? AND g.expires>? AND (? IS NULL OR g.id=?)
        ORDER BY g.expires DESC,g.id""",
        (principal, project, revision, action, now.generation, now.utc, now.utc, ref, ref),
    )
    return (
        row
        for row in rows
        if now.within(
            row["boot_id"],
            row["issued"],
            row["issued_boot_ns"],
            row["expires"],
            row["deadline"],
        )
    )


class AuditAuthority(AuthorityStore):
    def register(self, catalog: Path, revision: str) -> str:
        require(catalog.is_absolute() and ".." not in catalog.parts)
        with (
            self.connection(write=True) as db,
            _export(catalog, revision) as (cat, ver, root, chain),
        ):
            ci, ri = _identity(cat), _identity(root)
            fp = _manifest(cat, ver, revision)
            _attest_chain(chain)
            old = db.execute(
                "SELECT id,catalog_identity FROM projects WHERE catalog=?", (str(catalog),)
            ).fetchone()
            if old:
                project = old["id"]
                require(old["catalog_identity"] == ci)
            else:
                project = str(uuid.uuid4())
                db.execute("INSERT INTO projects VALUES(?,?,?)", (project, str(catalog), ci))
            db.execute("INSERT INTO exports VALUES(?,?,?,?)", (project, revision, ri, fp))
            return project

    def grant(
        self,
        principal: str,
        project: str,
        revision: str,
        *,
        expires: int,
        action: str = READ_SNAPSHOT,
    ) -> str:
        with self.connection(write=True) as db:
            now = Clock.read()
            require(
                principal
                and type(expires) is int
                and now.utc < expires < 2**33
                and action in (READ_SNAPSHOT, APPROVE_EXCEPTION, REVOKE_EXCEPTION)
            )
            ref = "gr_" + secrets.token_hex(32)
            db.execute(
                """INSERT INTO grants (id,principal,project,revision,action,epoch,expires,issued,deadline,boot_id,issued_boot_ns)
                SELECT ?,?,?,?,?,epoch,?,?,?,?,? FROM policy WHERE singleton=1""",
                (
                    ref,
                    principal,
                    project,
                    revision,
                    action,
                    expires,
                    now.utc,
                    now.deadline(expires),
                    now.generation,
                    now.boot_ns,
                ),
            )
            require(db.execute("SELECT changes()").fetchone()[0] == 1)
            return ref

    def revoke(self, ref: str):
        with self.connection(write=True) as db:
            require(db.execute("UPDATE grants SET revoked=1 WHERE id=?", (ref,)).rowcount == 1)

    def invalidate_policy(self):
        with self.connection(write=True) as db:
            require(db.execute("UPDATE policy SET epoch=epoch+1 WHERE singleton=1").rowcount == 1)

    def retire(self, project: str):
        """Retira o cadastro e seus grants; recadastro no mesmo catálogo recebe outro UUID."""
        with self.connection(write=True) as db:
            db.execute("DELETE FROM grants WHERE project=?", (project,))
            db.execute("DELETE FROM exports WHERE project=?", (project,))
            require(db.execute("DELETE FROM projects WHERE id=?", (project,)).rowcount == 1)

    @contextmanager
    def _guard(self, principal, project, revision, grant):
        with self.connection() as db:
            row = next(
                current_grants(
                    db, Clock.read(), principal, project, revision, action=READ_SNAPSHOT, ref=grant
                ),
                None,
            )
            if row is None:
                raise AuthorityDenied()
            yield tuple(
                row[k]
                for k in (
                    "catalog",
                    "catalog_identity",
                    "root_identity",
                    "fingerprint",
                    "issued",
                    "expires",
                    "deadline",
                    "boot_id",
                    "issued_boot_ns",
                )
            )

    def capture(self, principal, project, revision, grant, inbox):
        """Só para adaptadores confiáveis; nunca passar o principal do payload."""
        require(type(inbox) is queue.Queue and inbox.maxsize > 0)

        def guard():
            return self._guard(principal, project, revision, grant)

        try:
            with ExitStack() as stack:
                with guard() as selected:
                    path, ci, ri, expected, issued, expires, deadline, generation, issued_boot = (
                        selected
                    )
                    with ExitStack() as dispatch:
                        cat, chain = open_directory(dispatch, Path(path))
                        require(_identity(cat) == ci)
                        version = open_fd(dispatch, revision, DIRECTORY, parent=cat)
                        _attest_chain([*chain, (cat, revision, version)])
                        require(_manifest(cat, version, revision) == expected)
                with guard() as target:
                    require(target == selected)
                    cat, version, root, chain = stack.enter_context(_export(Path(path), revision))

                    def attest():
                        _attest_chain(chain)
                        require((_identity(cat), _identity(root)) == (ci, ri))
                        require(_manifest(cat, version, revision) == expected)

                    attest()
                result = capture_authorized_root(
                    version, "workspace", root, limits=LIMITS, guard=guard
                )
                validate_capture(result, limits=LIMITS, require_contents=True)
                contents = result.contents
                assert contents is not None
                files = {
                    safe_path(e.path): (contents[e.path], 0o755 if e.mode & 0o111 else 0o644)
                    for e in result.entries
                    if e.kind == "file"
                }
                require(fingerprint_files(files) == expected)
                snapshot = Snapshot(
                    project, revision, expected, result.entries, MappingProxyType(contents), ri
                )
                with guard() as current:
                    require(current == target)
                    attest()
                    require(Clock.read().within(generation, issued, issued_boot, expires, deadline))
                    inbox.put_nowait(snapshot)
        except (OSError, sqlite3.Error, FilesystemError, ValueError, queue.Full):
            raise AuthorityDenied() from None
