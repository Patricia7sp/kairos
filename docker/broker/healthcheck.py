"""Readiness do broker externo sem imprimir dados da conta ou das sessões."""

import asyncio
import os
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
        return 0 if status.get("enabled") and status.get("state") == "ready" else 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
