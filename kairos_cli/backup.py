"""Comando `kairos backup` — snapshot do home do Kairos."""

import json
import os
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path


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

    excludes = {"__pycache__", ".cache", ".pytest_cache"}
    with tarfile.open(archive, "w:gz") as tar:
        for p in sorted(home.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(home)
            if any(part in excludes for part in rel.parts):
                continue
            tar.add(p, arcname=str(rel))

    size = archive.stat().st_size
    files = sum(
        1
        for _ in home.rglob("*")
        if _.is_file() and not any(part in excludes for part in _.relative_to(home).parts)
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
