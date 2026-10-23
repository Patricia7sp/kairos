"""Comando `kairos backup` — snapshot do home do Kairos."""

import errno
import json
import logging
import os
import sqlite3
import stat
import sys
import tarfile
import tempfile
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path

from kairos_filesystem.contract import FilesystemError
from kairos_filesystem.descriptors import check_chain, open_directory, open_fd, same_entry
from kairos_skills.mutation_contract import SkillMutationError

_LOGGER = logging.getLogger(__name__)


class BackupBoundaryError(Exception):
    """A entrada observada não pode ser arquivada como banco do home."""


def _database_entry(directory: int):
    try:
        entry = os.stat("state.db", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(entry.st_mode):
        raise BackupBoundaryError
    return entry


def _database_descriptor(stack: ExitStack, directory: int, expected) -> int:
    try:
        descriptor = open_fd(stack, "state.db", os.O_RDONLY | os.O_NONBLOCK, parent=directory)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR):
            raise BackupBoundaryError from None
        raise
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        raise BackupBoundaryError
    same_entry(directory, "state.db", descriptor)
    return descriptor


def _check_database(directory: int, descriptor: int | None, chain) -> None:
    check_chain(chain)
    current = _database_entry(directory)
    if descriptor is None:
        if current is not None:
            raise BackupBoundaryError
    elif current is None:
        raise BackupBoundaryError
    else:
        same_entry(directory, "state.db", descriptor)


def _snapshot_database(directory, descriptor, chain, snapshot, *, snapshot_guard):
    def check(*_progress):
        _check_database(directory, descriptor, chain)

    check()
    # O descritor fixa a entrada observada; SQLite resolve seu nome para ler
    # também o WAL. Revalidar antes de copiar e durante/final da captura.
    with closing(sqlite3.connect(f"file:/proc/self/fd/{descriptor}?mode=ro", uri=True)) as source:
        check()
        with closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination, pages=64, progress=check)
            objects = {
                row[0]
                for row in destination.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
            from kairos_state.skill_org_schema import ORG_OBJECTS, validate_org_schema

            versions = (
                destination.execute("SELECT version FROM schema_version").fetchall()
                if "schema_version" in objects
                else []
            )
            if "schema_version" in objects and (
                len(versions) != 1 or type(versions[0][0]) is not int or versions[0][0] < 0
            ):
                raise sqlite3.DatabaseError("versão do snapshot inválida")
            if objects & ORG_OBJECTS or any(row[0] >= 10 for row in versions):
                from kairos_state.repositories.skill_mutations import validate_skill_timeline

                validate_org_schema(destination)
                try:
                    validate_skill_timeline(destination)
                except ValueError:
                    raise sqlite3.DatabaseError("snapshot de skills incoerente") from None
            snapshot_guard(destination)
    check()
    snapshot.chmod(os.fstat(descriptor).st_mode & 0o777)


def _archive_home_contents(home: Path, archive: Path, *, output, snapshot_guard) -> int:
    home, archive = home.absolute(), archive.absolute()
    outputs = {archive} if output is None else {archive, output.absolute()}
    excludes = {"__pycache__", ".cache", ".pytest_cache"}
    database = home / "state.db"
    files = 0
    with (
        ExitStack() as stack,
        tempfile.TemporaryDirectory(prefix="kairos-backup-state-") as temporary,
    ):
        directory, chain = open_directory(stack, home)
        initial = _database_entry(directory)
        descriptor = None if initial is None else _database_descriptor(stack, directory, initial)
        snapshot = Path(temporary) / "state.db"
        decided = False
        absent_namespace = None

        def check_archive():
            _check_database(directory, descriptor, chain)
            if absent_namespace is not None:
                current = os.fstat(directory)
                if (current.st_mtime_ns, current.st_ctime_ns) != absent_namespace:
                    raise BackupBoundaryError

        with tarfile.open(archive, "w:gz") as tar:
            # A entrada lógica é visitada mesmo se surgir depois da enumeração.
            for path in sorted(set(home.rglob("*")) | {database}):
                if path in outputs:
                    continue
                relative = path.relative_to(home)
                if any(part in excludes for part in relative.parts):
                    continue
                if relative.as_posix() in {"state.db-wal", "state.db-shm", "state.db-journal"}:
                    continue
                if path == database:
                    if descriptor is None:
                        observed = _database_entry(directory)
                        if observed is not None:
                            descriptor = _database_descriptor(stack, directory, observed)
                    decided = True
                    if descriptor is None:
                        observed_directory = os.fstat(directory)
                        absent_namespace = (
                            observed_directory.st_mtime_ns,
                            observed_directory.st_ctime_ns,
                        )
                    _check_database(directory, descriptor, chain)
                    if descriptor is not None:
                        _snapshot_database(
                            directory, descriptor, chain, snapshot, snapshot_guard=snapshot_guard
                        )
                        tar.add(snapshot, arcname="state.db", recursive=False)
                        files += 1
                        _check_database(directory, descriptor, chain)
                    continue
                if descriptor is not None or decided:
                    check_archive()
                if not path.is_file() and not (path.is_dir() and not path.is_symlink()):
                    continue
                tar.add(path, arcname=str(relative), recursive=False)
                files += path.is_file()
            check_archive()
        check_archive()
    return files


def _observed_version(connection):
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='schema_version'"
    ).fetchone()
    if present is None:
        return None
    rows = connection.execute("SELECT version FROM schema_version").fetchall()
    if len(rows) != 1 or type(rows[0][0]) is not int:
        raise BackupBoundaryError
    return rows[0][0]


def _reject_publication_snapshot(connection):
    if _observed_version(connection) == 11:
        raise BackupBoundaryError


def _publication_area_safe(home):
    area = home / ".skill-publications"
    for path in (area, *area.rglob("*")):
        try:
            value = path.lstat()
        except FileNotFoundError:
            if path == area:
                return
            raise BackupBoundaryError from None
        if not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode)) or (
            stat.S_ISREG(value.st_mode) and value.st_nlink != 1
        ):
            raise BackupBoundaryError


def _archive_publications(home, archive, *, output, directory, descriptor, chain):
    from kairos_skills.catalog import catalog_home_id
    from kairos_skills.mutation_lock import skill_mutation_lock
    from kairos_skills.publication_backup import publication_backup_inventory
    from kairos_skills.publication_manifest import observe_manifest
    from kairos_skills.removal_state import database_connection
    from kairos_state.migrations import canonical_fingerprint
    from kairos_state.repositories.skill_publications import SkillPublicationRepository

    with skill_mutation_lock(home), database_connection(home) as connection:
        _check_database(directory, descriptor, chain)
        if connection is None or _observed_version(connection) != 11:
            raise BackupBoundaryError
        repository = SkillPublicationRepository(connection, home_id=catalog_home_id(home))
        before = publication_backup_inventory(home, repository)
        _manifest, manifest_observation = observe_manifest(home)
        _publication_area_safe(home)
        fingerprint = canonical_fingerprint(connection)

        def snapshot_guard(snapshot):
            if _observed_version(snapshot) != 11 or canonical_fingerprint(snapshot) != fingerprint:
                raise BackupBoundaryError

        files = _archive_home_contents(home, archive, output=output, snapshot_guard=snapshot_guard)
        _check_database(directory, descriptor, chain)
        if publication_backup_inventory(home, repository) != before:
            raise BackupBoundaryError
        manifest_observation.check()
        _publication_area_safe(home)
        return files


def _archive_home(home: Path, archive: Path, *, output: Path | None = None) -> int:
    home = Path(home).absolute()
    with ExitStack() as stack:
        directory, chain = open_directory(stack, home)
        initial = _database_entry(directory)
        descriptor = None if initial is None else _database_descriptor(stack, directory, initial)
        version = None
        if descriptor is not None:
            with closing(
                sqlite3.connect(f"file:/proc/self/fd/{descriptor}?mode=ro", uri=True)
            ) as peek:
                version = _observed_version(peek)
        _check_database(directory, descriptor, chain)
        if version == 11:
            return _archive_publications(
                home,
                archive,
                output=output,
                directory=directory,
                descriptor=descriptor,
                chain=chain,
            )
        return _archive_home_contents(
            home, archive, output=output, snapshot_guard=_reject_publication_snapshot
        )


def _archive_and_publish(home, partial, archive, descriptor):
    if partial.parent != archive.parent:
        raise BackupBoundaryError
    with ExitStack() as stack:
        parent, chain = open_directory(stack, partial.parent)
        same_entry(parent, partial.name, descriptor)
        files = _archive_home(home, partial, output=archive)
        check_chain(chain)
        same_entry(parent, partial.name, descriptor)
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise BackupBoundaryError
        os.fsync(descriptor)
        if partial != archive:
            os.replace(partial.name, archive.name, src_dir_fd=parent, dst_dir_fd=parent)
            same_entry(parent, archive.name, descriptor)
        os.fsync(parent)
        check_chain(chain)
        return files, observed.st_size


def _publication_backup_error(error):
    _LOGGER.warning(
        "backup_publication_refused",
        extra={
            "action": "backup",
            "writer": "backup",
            "phase": "publication_inventory",
            "kind": error.kind,
            "operation_id": error.operation_id,
            "reason": "publication_artifacts_unverified",
        },
    )
    message = (
        "kairos: não foi possível atestar os artefatos de publicação; backup anterior preservado."
    )
    if error.operation_id is not None:
        message += f" Operação: {error.operation_id}."
    print(message, file=sys.stderr)


def _backup_summary(home, archive, size, files, *, as_json):
    if as_json:
        print(
            json.dumps(
                {"archive": str(archive), "home": str(home), "size_bytes": size, "files": files},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Backup de {home}")
        print(f"  archive: {archive}")
        print(f"  tamanho: {size} bytes em {files} arquivos")


async def run_backup(home: Path, args) -> int:
    home = Path(home)
    if not home.exists():
        print(f"kairos: home {home} não existe", file=sys.stderr)
        return 1

    partial = None
    try:
        output = getattr(args, "output", None)
        if output:
            archive = Path(output).absolute()
            archive = archive.parent.resolve() / archive.name
            if archive.name in {
                "state.db",
                "state.db-wal",
                "state.db-shm",
                "state.db-journal",
            } and archive.parent.samefile(home):
                raise BackupBoundaryError
            fd, tmp_path = tempfile.mkstemp(prefix=".kairos-backup-", dir=archive.parent)
        else:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            fd, tmp_path = tempfile.mkstemp(prefix="kairos-backup-", suffix=f"-{ts}.tar.gz")
            archive = Path(tmp_path)
        partial = Path(tmp_path)
        with os.fdopen(fd, "r+b") as descriptor:
            files, size = _archive_and_publish(home, partial, archive, descriptor.fileno())
        partial = None
    except SkillMutationError as error:
        _publication_backup_error(error)
        return 1
    except (BackupBoundaryError, FilesystemError):
        _LOGGER.warning(
            "backup_boundary_refused",
            extra={
                "action": "backup",
                "writer": "backup",
                "phase": "archive",
                "kind": "conflict",
                "operation_id": None,
                "reason": "state_or_output_boundary_changed",
            },
        )
        print(
            "kairos: fronteira de state.db inválida ou alterada; backup recusado. "
            "Confira state.db, os diretórios do home e o destino de saída. "
            "Corrija entradas inválidas antes de repetir; se houve alteração concorrente, "
            "tente novamente com o home estável.",
            file=sys.stderr,
        )
        return 1
    except (OSError, sqlite3.DatabaseError, tarfile.TarError):
        _LOGGER.warning(
            "backup_archive_refused",
            extra={
                "action": "backup",
                "writer": "backup",
                "phase": "archive",
                "kind": "io",
                "operation_id": None,
                "reason": "archive_write_failed",
            },
        )
        print("kairos: não foi possível criar o backup com segurança.", file=sys.stderr)
        return 1
    finally:
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                _LOGGER.error(
                    "backup_partial_cleanup_failed",
                    extra={"action": "backup", "reason": "partial_cleanup_failed"},
                )

    _backup_summary(home, archive, size, files, as_json=getattr(args, "json", False))
    return 0
