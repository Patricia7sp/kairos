"""Readiness do broker externo sem imprimir dados da conta ou das sessões."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from kairos_runtime.client import RuntimeClient
from kairos_runtime.errors import RuntimeErrorInfo


async def main() -> int:
    client = RuntimeClient(Path(os.environ["KAIROS_HOME"]) / "run" / "runtime.sock")
    try:
        try:
            status = await asyncio.wait_for(client.status(), timeout=5)
        except (TimeoutError, RuntimeErrorInfo):
            return 1
        if not status.get("enabled") or status.get("state") != "ready":
            return 1
        docker = shutil.which("docker")
        if docker is None:
            return 1
        # Um registry vazio pode estar ready sem ter consultado o daemon.
        # A soma dos dois limites fica abaixo do timeout de 10s do Compose.
        try:
            subprocess.run(  # noqa: S603 — executável do PATH da imagem e argumentos fixos
                [docker, "info", "--format", "{{.ServerVersion}}"],
                check=True,
                timeout=3,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return 1
        return 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
