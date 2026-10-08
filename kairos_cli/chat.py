"""Thin terminal transport over the canonical InteractionService."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

from kairos_cli.runtime import RuntimeHumanRenderer, render_runtime_event
from kairos_cli.tool_approval import AUTOMATION_TOOLS, request_tool_decision
from kairos_integration import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionEventKind,
    interaction_event_to_json,
)
from kairos_integration import build_interaction_router as build_interaction_service
from kairos_integration.interaction_contract import InteractionServiceError
from kairos_providers import ProviderModelRef
from kairos_runtime import RuntimeErrorInfo, RuntimeEvent, public_error
from kairos_runtime.wire import runtime_event_to_json
from kairos_skills.runtime import SkillSelectionError, SkillSnapshot, load_selected_skills

__all__ = ["ChatUsageError", "run_chat"]


class ChatUsageError(ValueError):
    """Invalid terminal-only arguments detected before service composition."""


@dataclass
class _HumanRenderer:
    line_open: bool = False
    failed: bool = False

    def emit(self, event: InteractionEvent) -> None:
        if event.kind is InteractionEventKind.DELTA:
            print(event.text, end="", flush=True)
            self.line_open = self.line_open or bool(event.text)
        elif event.kind is InteractionEventKind.TURN_ERROR:
            self.finish_line()
            print(event.error or "falha ao executar interação", file=sys.stderr, flush=True)
            self.failed = True
        elif event.kind is InteractionEventKind.TURN_END:
            self.finish_line()
        elif event.kind is InteractionEventKind.TOOL_CALL and event.tool_call is not None:
            self.finish_line()
            print(
                f"[ferramenta] {json.dumps(event.tool_call.name, ensure_ascii=True)}",
                file=sys.stderr,
                flush=True,
            )
        elif event.kind is InteractionEventKind.TOOL_RESULT and event.tool_result is not None:
            status = "falhou ou foi recusada" if event.tool_result.is_error else "concluída"
            print(f"[ferramenta] {status}", file=sys.stderr, flush=True)

    def finish_line(self) -> None:
        if self.line_open:
            print(flush=True)
            self.line_open = False


async def run_chat(
    *,
    home: Path,
    session_id: str,
    prompt: str,
    provider: str | None,
    model: str | None,
    as_json: bool,
    quiet: bool = False,
    idempotency_key: str | None = None,
    web_search: bool = False,
    tools: bool = False,
    experiences: bool = True,
    workspace: str | None = None,
    allow_tools: tuple[str, ...] | list[str] = (),
    skill_names: tuple[str, ...] | list[str] = (),
) -> int:
    """Run a one-shot or interactive terminal session with one owned service graph."""
    session_id = _required_session_id(session_id)
    override = _model_override(provider, model)
    if idempotency_key is not None and not idempotency_key.strip():
        raise ChatUsageError("--idempotency-key exige um valor não vazio")
    if idempotency_key is not None and not prompt:
        raise ChatUsageError("--idempotency-key só pode ser usado com uma mensagem")
    runtime_session = _is_runtime_session(home, session_id)
    authorized_tools = _tool_authorization(
        allow_tools,
        tools=tools,
        workspace=workspace,
        prompt=prompt,
        runtime_session=runtime_session,
    )
    if runtime_session and web_search:
        raise ChatUsageError("--web-search é uma opção do Chat por modelo, não do Agent Runtime")
    if runtime_session and tools:
        raise ChatUsageError("--tools é uma opção do Chat por modelo, não do Agent Runtime")
    workspace = _selected_workspace(workspace, tools=tools, runtime_session=runtime_session)
    skills = _selected_skills(home, skill_names, prompt=prompt, runtime_session=runtime_session)
    if skills:
        print(
            "Skills selecionadas: " + ", ".join(skill.name for skill in skills) + ".",
            file=sys.stderr,
            flush=True,
        )
    service = build_interaction_service(home)
    try:
        if prompt:
            return await _run_turn(
                service,
                session_id=session_id,
                content=prompt,
                override=override,
                as_json=as_json,
                idempotency_key=idempotency_key or (uuid.uuid4().hex if runtime_session else None),
                runtime_session=runtime_session,
                web_search=web_search,
                tools=tools,
                workspace=workspace,
                authorized_tools=authorized_tools,
                skills=skills,
                home=home,
                experiences=experiences,
            )
        return await _run_interactive(
            service,
            session_id=session_id,
            override=override,
            as_json=as_json,
            quiet=quiet,
            runtime_session=runtime_session,
            web_search=web_search,
            tools=tools,
            workspace=workspace,
            home=home,
            experiences=experiences,
        )
    except InteractionServiceError as exc:
        print(exc.message, file=sys.stderr, flush=True)
        return 1
    except RuntimeErrorInfo as exc:
        print(public_error(exc.code)["message"], file=sys.stderr, flush=True)
        return 1
    finally:
        await service.aclose()


def _selected_skills(home, names, *, prompt, runtime_session) -> tuple[SkillSnapshot, ...]:
    names = tuple(names)
    if not names:
        return ()
    if runtime_session:
        raise ChatUsageError("--skill é uma opção do Chat por modelo, não do Agent Runtime")
    if not prompt.strip():
        raise ChatUsageError("--skill exige uma mensagem para um único turno")
    try:
        return load_selected_skills(home, names)
    except SkillSelectionError as exc:
        raise ChatUsageError(str(exc)) from None


def _tool_authorization(names, *, tools, workspace, prompt, runtime_session) -> frozenset[str]:
    authorized = frozenset(names)
    if not authorized:
        return authorized
    if runtime_session:
        raise ChatUsageError("--allow-tool é uma opção do Chat por modelo, não do Agent Runtime")
    if not tools:
        raise ChatUsageError("--allow-tool exige --tools")
    if workspace is None or not workspace.strip():
        raise ChatUsageError("--allow-tool exige --workspace explícito")
    if not prompt.strip():
        raise ChatUsageError("--allow-tool exige uma mensagem para um único turno")
    if not authorized <= AUTOMATION_TOOLS:
        raise ChatUsageError("--allow-tool aceita somente: " + ", ".join(sorted(AUTOMATION_TOOLS)))
    return authorized


def _selected_workspace(workspace: str | None, *, tools: bool, runtime_session: bool) -> str | None:
    if runtime_session and workspace is not None:
        raise ChatUsageError(
            "--workspace é uma opção do Chat por modelo; o runtime usa seu próprio cwd"
        )
    if workspace is not None and not tools:
        raise ChatUsageError("--workspace exige --tools")
    if tools:
        try:
            selected = Path(workspace).expanduser() if workspace is not None else Path.cwd()
            selected = selected.resolve(strict=True)
            if not selected.is_dir():
                raise NotADirectoryError
            workspace = str(selected)
        except OSError as exc:
            raise ChatUsageError("workspace deve apontar para um diretório existente") from exc
    return workspace


async def _run_interactive(
    service,
    *,
    session_id: str,
    override: ProviderModelRef | None,
    as_json: bool,
    quiet: bool,
    runtime_session: bool = False,
    web_search: bool = False,
    tools: bool = False,
    home: Path | None = None,
    experiences: bool = True,
    workspace: str | None = None,
) -> int:
    if not quiet and not as_json:
        print("Kairos Agent CLI (digite 'sair' ou Ctrl+C para encerrar)")
    exit_code = 0
    while True:
        try:
            line = input("" if as_json or quiet else "\nvocê > ").strip()
        except EOFError:
            return exit_code
        if not line:
            continue
        if line.lower() in {"exit", "quit", "sair"}:
            return exit_code
        exit_code = max(
            exit_code,
            await _run_turn(
                service,
                session_id=session_id,
                content=line,
                override=override,
                as_json=as_json,
                idempotency_key=uuid.uuid4().hex if runtime_session else None,
                runtime_session=runtime_session,
                web_search=web_search,
                tools=tools,
                workspace=workspace,
                home=home,
                experiences=experiences,
            ),
        )


async def _run_turn(
    service,
    *,
    session_id: str,
    content: str,
    override: ProviderModelRef | None,
    as_json: bool,
    idempotency_key: str | None = None,
    runtime_session: bool = False,
    web_search: bool = False,
    tools: bool = False,
    home: Path | None = None,
    experiences: bool = True,
    workspace: str | None = None,
    authorized_tools: frozenset[str] = frozenset(),
    skills: tuple[SkillSnapshot, ...] = (),
) -> int:
    content = _augment_with_experiences(content, home) if experiences and home else content
    envelope = InteractionEnvelope(
        conversation_id=session_id,
        source="cli",
        content=content,
        override=override,
        idempotency_key=idempotency_key,
        web_search=web_search,
        tools=tools,
        workspace=workspace,
        skills=skills,
    )
    renderer = _HumanRenderer()
    runtime_renderer = RuntimeHumanRenderer()
    last_runtime_event: RuntimeEvent | None = None
    accepted_turn_id: str | None = None

    def retain_accepted_turn(turn_id: str) -> None:
        nonlocal accepted_turn_id
        accepted_turn_id = turn_id

    try:
        stream = (
            service.stream(envelope, on_runtime_accepted=retain_accepted_turn)
            if runtime_session
            else service.stream(envelope)
        )
        async for event in stream:
            if isinstance(event, RuntimeEvent):
                last_runtime_event = event
                if as_json:
                    print(
                        json.dumps(
                            runtime_event_to_json(event), ensure_ascii=False, sort_keys=True
                        ),
                        flush=True,
                    )
                else:
                    await render_runtime_event(event, as_json=False, renderer=runtime_renderer)
                if event.kind == "error":
                    renderer.failed = True
                continue
            await _handle_chat_event(
                service,
                event,
                renderer,
                session_id=session_id,
                as_json=as_json,
                authorized_tools=authorized_tools,
            )
    except asyncio.CancelledError:
        if accepted_turn_id is not None:
            cursor = (
                last_runtime_event.cursor
                if last_runtime_event is not None and last_runtime_event.turn_id == accepted_turn_id
                else None
            )
            await _cancel_and_confirm(
                service,
                session_id=session_id,
                turn_id=accepted_turn_id,
                cursor=cursor,
                as_json=as_json,
            )
        elif last_runtime_event is not None:
            await _cancel_and_confirm(
                service,
                session_id=last_runtime_event.session_id,
                turn_id=last_runtime_event.turn_id,
                cursor=last_runtime_event.cursor,
                as_json=as_json,
            )
        raise
    finally:
        renderer.finish_line()
    return 1 if renderer.failed else 0


async def _handle_chat_event(
    service,
    event: InteractionEvent,
    renderer: _HumanRenderer,
    *,
    session_id: str,
    as_json: bool,
    authorized_tools: frozenset[str] = frozenset(),
) -> None:
    if as_json:
        print(
            json.dumps(
                interaction_event_to_json(event, conversation_id=session_id),
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
    else:
        renderer.emit(event)
    if event.kind is InteractionEventKind.TOOL_APPROVAL_REQUEST:
        renderer.finish_line()
        decision = await request_tool_decision(
            event, as_json=as_json, authorized_tools=authorized_tools
        )
        service.decide_tool_approval(
            approval_id=event.tool_approval_id,
            session_id=session_id,
            decision=decision,
        )
    if event.kind is InteractionEventKind.TURN_ERROR or (
        event.tool_result is not None and event.tool_result.is_error
    ):
        renderer.failed = True


def _augment_with_experiences(content: str, home: Path) -> str:
    """Prefixa o turno atual com experiências ativas relevantes (padrão ligado).

    Desligue com `--no-experiences`. A injeção é deliberadamente local: vai no
    `content` enviado ao serviço, nunca no system prompt nem em mensagens já
    persistidas. A "Lei 1" de `kairos_integration.surfaces` trata o prefixo de
    prompt como cache sagrado por conversa — reescrevê-lo a cada turno
    invalidaria o cache.
    """
    from kairos_memory import build_experience_context

    context = build_experience_context(content, home)
    if not context:
        return content
    return f"{context}\n\n{content}"


def _model_override(provider: str | None, model: str | None) -> ProviderModelRef | None:
    if provider is None and model is None:
        return None
    if not provider or not provider.strip() or not model or not model.strip():
        raise ChatUsageError("--provider e --model devem ser informados juntos")
    return ProviderModelRef(provider.strip(), model.strip())


def _required_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id.strip():
        raise ChatUsageError("--session exige um ID não vazio")
    return session_id.strip()


def _is_runtime_session(home: Path, session_id: str) -> bool:
    from kairos_state import connect

    db_path = Path(home) / "state.db"
    if not db_path.exists():
        return False
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT execution_kind FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return row is not None and row["execution_kind"] == "agent_runtime"
    except Exception:  # noqa: BLE001 - legacy/uninitialized DB remains model-compatible
        return False
    finally:
        connection.close()


async def _cancel_and_confirm(
    service,
    *,
    session_id: str,
    turn_id: str,
    cursor: str | None,
    as_json: bool,
) -> None:
    """A Ctrl-C is a cancellation command; closing a stream alone is not."""
    client = getattr(service, "runtime_client", None)
    if client is None:
        return
    try:
        await client.cancel(session_id, turn_id)
        async with aclosing(client.subscribe(session_id, cursor)) as subscription:
            async for confirmation in subscription:
                if confirmation.turn_id != turn_id:
                    continue
                await render_runtime_event(confirmation, as_json=as_json)
                if confirmation.kind == "turn_end":
                    return
    except RuntimeErrorInfo as exc:
        print(public_error(exc.code)["message"], file=sys.stderr, flush=True)
