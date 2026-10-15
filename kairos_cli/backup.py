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


def _snapshot_database(directory, descriptor, chain, snapshot):
    def check(*_progress):
        _check_database(directory, descriptor, chain)

    check()
    # O descritor fixa a entrada observada; SQLite resolve seu nome para ler
    # também o WAL. Revalidar antes de copiar e durante/final da captura.
    with closing(sqlite3.connect(f"file:/proc/self/fd/{descriptor}?mode=ro", uri=True)) as source:
        check()
        with closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination, pages=64, progress=check)
    check()
    snapshot.chmod(os.fstat(descriptor).st_mode & 0o777)


def _archive_home(home: Path, archive: Path, *, output: Path | None = None) -> int:
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
                        _snapshot_database(directory, descriptor, chain, snapshot)
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
        os.close(fd)
        files = _archive_home(home, partial, output=archive)
        size = partial.stat().st_size
        if partial != archive:
            os.replace(partial, archive)
        partial = None
    except (BackupBoundaryError, FilesystemError):
        print(
            "kairos: fronteira de state.db inválida ou alterada; backup recusado.", file=sys.stderr
        )
        return 1
    except (OSError, sqlite3.DatabaseError, tarfile.TarError):
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

    as_json = getattr(args, "json", False)
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
    return 0
