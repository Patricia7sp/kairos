"""Entrada CLI e confirmação humana específica de /learn."""

import asyncio
import io
from contextlib import redirect_stderr
from unittest.mock import AsyncMock, patch

import pytest
from skill_mutation_fixtures import creation_text
from test_chat_search_loop import RoundGateway, answer_round, make_service
from test_chat_tool_approval import mutator_round
from test_learn_interaction import call

from kairos_cli.chat import ChatUsageError, _run_turn, run_chat
from kairos_cli.tool_approval import request_tool_decision
from kairos_integration.interaction_contract import InteractionEvent
from kairos_providers import CanonicalToolCall, ProviderEvent
from kairos_state import connect, initialize_schema


@pytest.mark.parametrize("answer,expected", [("criar revisar-docs", 0), ("sim", 77), ("", 77)])
def test_cli_real_turn_creation_preview_receipt_and_denial(
    tmp_path, monkeypatch, capsys, answer, expected
):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)
        monkeypatch.setattr("kairos_cli.tool_approval._read_answer", AsyncMock(return_value=answer))
        gateway = RoundGateway([mutator_round(call()), answer_round()])
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        try:
            code = await _run_turn(
                service,
                session_id="learn-session",
                content="/learn revisar",
                override=None,
                as_json=False,
                home=tmp_path,
            )
            assert code == expected
            assert (tmp_path / "skills/revisar-docs/SKILL.md").exists() == (expected == 0)
            assert gateway.requests[0].tools[0]["function"]["name"] == "skill_create"
        finally:
            db.close()

    asyncio.run(scenario())
    output = capsys.readouterr().err
    assert "Revise documentos." in output
    assert "| name: revisar-docs" in output
    assert "| Procedimento fictício." in output
    assert "criar revisar-docs" in output
    if expected == 0:
        assert "kairos skills rollback " in output
        assert "committed" in output


def test_generic_approval_cannot_bypass_creator_even_with_internal_allow_set():
    event = InteractionEvent.tool_approval_request(
        "approval", CanonicalToolCall("call", "skill_create", "{}"), "session"
    )
    result = asyncio.run(
        request_tool_decision(event, as_json=False, authorized_tools=frozenset({"skill_create"}))
    )
    assert result == "deny"


@pytest.mark.parametrize(
    "runtime,as_json,tty,names",
    [
        (True, False, True, ()),
        (False, True, True, ()),
        (False, False, False, ()),
        (False, False, True, ("selected",)),
    ],
)
def test_rejects_before_service_and_skill_loader(tmp_path, runtime, as_json, tty, names):
    async def scenario():
        with (
            patch("kairos_cli.chat._is_runtime_session", return_value=runtime),
            patch("sys.stdin.isatty", return_value=tty),
            patch("sys.stderr.isatty", return_value=tty),
            patch(
                "kairos_cli.chat.build_interaction_service",
                side_effect=AssertionError("não compor serviço"),
            ),
            patch(
                "kairos_cli.chat.load_selected_skills", side_effect=AssertionError("não ler skill")
            ),
        ):
            if names or runtime:
                with pytest.raises(ChatUsageError):
                    await run_chat(
                        home=tmp_path,
                        session_id="s",
                        prompt="/learn revisar",
                        provider=None,
                        model=None,
                        as_json=as_json,
                        skill_names=names,
                    )
            else:
                assert (
                    await run_chat(
                        home=tmp_path,
                        session_id="s",
                        prompt="/learn revisar",
                        provider=None,
                        model=None,
                        as_json=as_json,
                    )
                    == 77
                )

    asyncio.run(scenario())


def test_empty_learn_and_allow_tool_refused_before_provider(tmp_path):
    async def scenario():
        for prompt, tools, names in [
            ("/learn", False, ()),
            ("/learn revisar", True, ("skill_create",)),
        ]:
            with (
                patch(
                    "kairos_cli.chat.build_interaction_service",
                    side_effect=AssertionError("não compor"),
                ),
                patch("sys.stdin.isatty", return_value=True),
                patch("sys.stderr.isatty", return_value=True),
                pytest.raises(ChatUsageError),
            ):
                await run_chat(
                    home=tmp_path,
                    session_id="s",
                    prompt=prompt,
                    provider=None,
                    model=None,
                    as_json=False,
                    tools=tools,
                    workspace=str(tmp_path),
                    allow_tools=names,
                )

    asyncio.run(scenario())


def test_preview_escapes_controls_and_accepts_full_length_name(tmp_path, monkeypatch):
    from kairos_cli.tool_approval import request_skill_creation
    from kairos_integration.learn_turn import LearnTurn
    from kairos_tools.skill_create import skill_creation_scope

    async def scenario():
        name = "a" * 64
        content = creation_text(name, body="linha\x1b[2J\u202e\núltima linha")
        import json

        turn = LearnTurn(home=tmp_path, session_id="s", request="r", guard=lambda: None)
        with skill_creation_scope(turn.capability):
            proposal, _ = turn.propose(
                CanonicalToolCall(
                    "c", "skill_create", json.dumps({"name": name, "skill_md": content})
                )
            )
            monkeypatch.setattr("sys.stdin.isatty", lambda: True)
            monkeypatch.setattr("sys.stderr.isatty", lambda: True)
            monkeypatch.setattr(
                "kairos_cli.tool_approval._read_answer",
                AsyncMock(return_value="criar " + name + "\r"),
            )
            out = io.StringIO()
            with redirect_stderr(out):
                assert await request_skill_creation(proposal, as_json=False) == "criar " + name
            rendered = out.getvalue()
            assert "\x1b" not in rendered and "\u202e" not in rendered
            assert "\\u001b" in rendered and "\\u202e" in rendered
            assert "| última linha" in rendered
            turn.close()

    asyncio.run(scenario())


def test_model_success_claim_without_creation_is_failure(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)
        gateway = RoundGateway(
            [
                [
                    ProviderEvent(kind="text_delta", text="Salvei a skill!"),
                    ProviderEvent(kind="finish", finish_reason="stop"),
                ]
            ]
        )
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        try:
            assert (
                await _run_turn(
                    service,
                    session_id="s",
                    content="/learn revisar",
                    override=None,
                    as_json=False,
                    home=tmp_path,
                )
                == 1
            )
            assert not (tmp_path / "skills").exists()
        finally:
            db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "text,expected",
    [
        ("criar " + "a" * 64 + "\n", True),
        ("sim\n", False),
        ("criar " + "a" * 65 + "\n", False),
        ("\x04", False),
    ],
)
def test_strong_reader_on_real_terminal(tmp_path, monkeypatch, text, expected):
    import json

    from test_cli_tools import terminal_input

    from kairos_cli.tool_approval import request_skill_creation
    from kairos_integration.learn_turn import LearnTurn

    async def scenario():
        name = "a" * 64
        turn = LearnTurn(home=tmp_path, session_id="s", request="r", guard=lambda: None)
        proposal, _ = turn.propose(
            CanonicalToolCall(
                "c", "skill_create", json.dumps({"name": name, "skill_md": creation_text(name)})
            )
        )
        with terminal_input(monkeypatch, text):
            answer = await asyncio.wait_for(request_skill_creation(proposal, as_json=False), 2)
        turn.decide(proposal.id, "s", proposal.sha256, answer)
        assert turn._pending[1].result() == ("allow" if expected else "deny")
        turn.close()

    asyncio.run(scenario())


def test_strong_reader_timeout_refuses_and_preserves_normal_reader_limit(tmp_path, monkeypatch):
    from test_cli_tools import terminal_input

    from kairos_cli.tool_approval import _read_answer

    async def scenario():
        with terminal_input(monkeypatch, "x" * 65 + "\n"):
            assert await asyncio.wait_for(_read_answer(), 2) == ""
        monkeypatch.setattr("kairos_cli.tool_approval.TOOL_APPROVAL_TIMEOUT_SECONDS", 0.02)
        with terminal_input(monkeypatch, ""), pytest.raises(TimeoutError):
            await _read_answer(max_bytes=72)

    asyncio.run(scenario())


def test_cli_entrypoint_creates_skill_without_workspace_or_tools(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from test_cli_tools import terminal_input

    from kairos_cli.main import main

    db = connect(tmp_path / "state.db")
    initialize_schema(db)
    gateway = RoundGateway([mutator_round(call()), answer_round()])
    service = make_service(db, gateway, skill_catalog_home=tmp_path)
    client = SimpleNamespace(
        stream=service.stream,
        skill_creation_proposal=service.skill_creation_proposal,
        decide_skill_creation=service.decide_skill_creation,
        aclose=AsyncMock(),
    )
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    monkeypatch.setattr("kairos_cli.chat.build_interaction_service", lambda _: client)

    def forbidden(*_):
        raise AssertionError("experiências não são fontes deste /learn")

    monkeypatch.setattr("kairos_cli.chat._augment_with_experiences", forbidden)
    try:
        with terminal_input(monkeypatch, "criar revisar-docs\n"):
            assert main(["run", "--session", "learn-session", "/learn revisar"]) == 0
        assert (tmp_path / "skills/revisar-docs/SKILL.md").read_text() == creation_text()
        assert "Rollback: kairos skills rollback " in capsys.readouterr().err
    finally:
        db.close()


def test_cancel_cli_consumer_revokes_pending_creation_before_return(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from kairos_integration.interaction_contract import InteractionServiceError
    from kairos_skills.mutation_contract import SkillMutationError

    async def scenario():
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        gateway = RoundGateway([mutator_round(call()), answer_round()])
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        awaiting, suspended = asyncio.Event(), asyncio.Event()
        proposals, streams, cancellations = [], [], []

        def stream(envelope):
            current = service.stream(envelope)
            streams.append(current)
            return current

        async def confirm(proposal, **_):
            proposals.append(proposal)
            awaiting.set()
            await suspended.wait()
            return ""

        client = SimpleNamespace(
            stream=stream,
            skill_creation_proposal=service.skill_creation_proposal,
            decide_skill_creation=service.decide_skill_creation,
        )
        monkeypatch.setattr("kairos_cli.chat.request_skill_creation", confirm)
        task = asyncio.create_task(
            _run_turn(
                client,
                session_id="learn-session",
                content="/learn revisar",
                override=None,
                as_json=False,
                home=tmp_path,
            )
        )
        try:
            await asyncio.wait_for(awaiting.wait(), 2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError as error:
                cancellations.append(error)
            assert cancellations
            with pytest.raises((SkillMutationError, InteractionServiceError)):
                service.skill_creation_proposal(
                    approval_id=proposals[0].id, session_id="learn-session"
                )
            assert not (tmp_path / "skills").exists()
        finally:
            for current in streams:
                await current.aclose()
            db.close()

    asyncio.run(scenario())


def test_cli_publication_error_shows_operation_id_without_claiming_absence(
    tmp_path, monkeypatch, capsys
):
    from contextlib import contextmanager

    from kairos_skills.removal_state import database_connection

    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(
            "kairos_cli.tool_approval._read_answer", AsyncMock(return_value="criar revisar-docs")
        )

        @contextmanager
        def fail_after_commit(*args, **kwargs):
            with database_connection(*args, **kwargs) as connection:
                yield connection
            raise OSError("private-io-fixture")

        monkeypatch.setattr("kairos_integration.learn_turn.database_connection", fail_after_commit)
        gateway = RoundGateway([mutator_round(call()), answer_round()])
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        try:
            assert (
                await _run_turn(
                    service,
                    session_id="s",
                    content="/learn revisar",
                    override=None,
                    as_json=False,
                    home=tmp_path,
                )
                == 1
            )
            assert (tmp_path / "skills/revisar-docs/SKILL.md").read_text() == creation_text()
            result = next(m for m in gateway.requests[-1].messages if m.role == "tool")
            import json

            operation_id = json.loads(result.content[0].value)["operation_id"]
            output = capsys.readouterr().err
            assert operation_id in output
            assert "skills history" in output
            assert "Nenhuma skill criada" not in output
            assert "private-io-fixture" not in output
        finally:
            db.close()

    asyncio.run(scenario())
