"""Comando `kairos backup` — snapshot do home do Kairos."""

import json
import os
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def _archive_home(home: Path, archive: Path) -> int:
    excludes = {"__pycache__", ".cache", ".pytest_cache"}
    database = home / "state.db"
    files = 0
    with tempfile.TemporaryDirectory(prefix="kairos-backup-state-") as temporary:
        snapshot = Path(temporary) / "state.db"
        if database.exists():
            with (
                closing(
                    sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
                ) as source,
                closing(sqlite3.connect(snapshot)) as destination,
            ):
                source.backup(destination)
            snapshot.chmod(database.stat().st_mode & 0o777)
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted(home.rglob("*")):
                relative = path.relative_to(home)
                if any(part in excludes for part in relative.parts):
                    continue
                if relative.as_posix() in {"state.db-wal", "state.db-shm", "state.db-journal"}:
                    continue
                if not path.is_file() and not (path.is_dir() and not path.is_symlink()):
                    continue
                archived = snapshot if path == database and snapshot.exists() else path
                tar.add(archived, arcname=str(relative), recursive=False)
                files += archived.is_file()
    return files


async def run_backup(home: Path, args) -> int:
    home = Path(home)
    if not home.exists():
        print(f"kairos: home {home} não existe", file=sys.stderr)
        return 1

    output = getattr(args, "output", None)
    if output:
        archive = Path(output)
    else:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        fd, tmp_path = tempfile.mkstemp(prefix="kairos-backup-", suffix=f"-{ts}.tar.gz")
        os.close(fd)
        archive = Path(tmp_path)

    try:
        files = _archive_home(home, archive)
        size = archive.stat().st_size
    except (OSError, sqlite3.DatabaseError, tarfile.TarError):
        print("kairos: não foi possível criar o backup com segurança.", file=sys.stderr)
        return 1

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
