"""CLI de avaliação offline, executado no host que administra o Docker."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .docker_worker import DockerWorker


async def _run(args):
    worker = DockerWorker(
        args.project, image=args.image, writable=args.writable, sandbox=args.sandbox
    )
    try:
        async with worker:
            result = await worker.execute(args.command, timeout_ms=args.timeout_ms)
            report = {
                "experimental": True,
                "image_id": worker.policy.image_id,
                "worker": worker.name,
                "workspace_writable": args.writable,
                "inner_sandbox": worker.sandbox,
                "kernel": worker.evidence,
                "command": result,
            }
    except BaseException:
        if worker._owned:
            print(f"Container com limpeza pendente: {worker.name}", file=sys.stderr)
        raise
    report["container_removed"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return result["exitCode"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--image", default="kairos:external-sandbox")
    parser.add_argument("--writable", action="store_true")
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="habilita a sandbox interna (bwrap via codex sandbox) com perfis de runtime",
    )
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("informe um comando após --")
    try:
        return asyncio.run(_run(args))
    except (RuntimeError, ValueError, TimeoutError):
        parser.exit(1, "Protótipo interrompido; verifique Docker, imagem e projeto autorizado.\n")


if __name__ == "__main__":
    raise SystemExit(main())
