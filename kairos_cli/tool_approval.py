"""Consentimento por chamada no terminal, sem bloquear o loop do agente."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from kairos_integration.chat_tools import MUTATING_TOOLS, TOOL_APPROVAL_TIMEOUT_SECONDS
from kairos_integration.interaction_contract import InteractionEvent

AUTOMATION_TOOLS = MUTATING_TOOLS


async def _read_answer() -> str:
    loop = asyncio.get_running_loop()
    descriptor = sys.stdin.fileno()
    answer = loop.create_future()
    data = bytearray()
    oversized = False

    def readable() -> None:
        nonlocal oversized
        if answer.done():
            return
        try:
            chunk = os.read(descriptor, 1)
        except OSError as exc:
            answer.set_exception(exc)
            return
        if not chunk or chunk == b"\n":
            value = data.decode("utf-8", errors="replace") if chunk and not oversized else ""
            answer.set_result(value)
        elif len(data) < 64:
            data.extend(chunk)
        else:
            oversized = True

    loop.add_reader(descriptor, readable)
    try:
        return await asyncio.wait_for(answer, timeout=TOOL_APPROVAL_TIMEOUT_SECONDS)
    finally:
        loop.remove_reader(descriptor)


async def request_tool_decision(
    event: InteractionEvent, *, as_json: bool, authorized_tools: frozenset[str] = frozenset()
) -> str:
    call = event.tool_call
    if call is None:
        return "deny"
    if call.name in authorized_tools:
        print(f"Execução autorizada explicitamente: {call.name}.", file=sys.stderr, flush=True)
        return "allow"
    if as_json or not sys.stdin.isatty():
        print(
            "Execução recusada: a aprovação exige terminal interativo sem --json.",
            file=sys.stderr,
            flush=True,
        )
        return "deny"
    detail = json.dumps({"ferramenta": call.name, "argumentos": call.arguments}, ensure_ascii=True)
    print(
        f"\nAprovação solicitada: {detail}\nExecutar esta chamada? [sim/N] ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = await _read_answer()
    except (OSError, ValueError, NotImplementedError, TimeoutError):
        print(
            "\nExecução recusada: resposta indisponível ou prazo de aprovação expirado.",
            file=sys.stderr,
            flush=True,
        )
        return "deny"
    return "allow" if answer.strip().casefold() in {"s", "sim", "y", "yes"} else "deny"
