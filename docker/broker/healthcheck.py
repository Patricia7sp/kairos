"""Readiness do broker externo sem imprimir dados da conta ou das sessões."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from kairos_runtime.client import RuntimeClient
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.host import _load_config


async def main() -> int:
    home = Path(os.environ["KAIROS_HOME"])
    client = RuntimeClient(home / "run" / "runtime.sock")
    try:
        try:
            status = await asyncio.wait_for(client.status(), timeout=5)
        except (TimeoutError, RuntimeErrorInfo):
            return 1
        if not status.get("enabled") or status.get("state") != "ready":
            return 1
        try:
            config = _load_config(home)
        except (RuntimeErrorInfo, ValueError):
            return 1
        if not config.enabled or config.backend != "docker":
            return 1
        docker = shutil.which("docker")
        if docker is None:
            return 1
        # Inspecionar a imagem exige daemon acessível e detecta sua remoção
        # mesmo quando o registry vazio ainda informa ready.
        # A soma dos dois limites fica abaixo do timeout de 10s do Compose.
        try:
            subprocess.run(  # noqa: S603 — executável do PATH da imagem e argumentos fixos
                [docker, "image", "inspect", "--", config.docker_image],
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
