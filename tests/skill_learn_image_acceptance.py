"""Aceite offline de /learn pelos módulos instalados na imagem."""

import asyncio
import hashlib
import io
import json
import os
import pty
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import kairos_cli
from kairos_cli.chat import _run_turn
from kairos_domain.ownership import Actor
from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_service import InteractionService
from kairos_providers import (
    CanonicalToolCall,
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ModelSelectionContext,
    ModelSelectionResolver,
    ProviderEvent,
    ProviderModelRef,
)
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutations import SkillMutationService
from kairos_skills.org_policy import observe_org_policy, org_policy_context
from kairos_skills.removal_state import database_connection
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository
from kairos_state.repositories.skill_mutations import SkillMutationRepository

NAME = "learn-image"
TEXT = "---\nname: learn-image\ndescription: Revise documentos.\nversion: '0.1.0'\n---\nConfira o diff e rode os testes.\n"


class ControlledGateway:
    def __init__(self):
        self.requests = []

    def create_adapter(self, _):
        return self

    async def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            assert [t["function"]["name"] for t in request.tools] == ["skill_create"]
            yield ProviderEvent(
                kind="tool_call",
                tool_call=CanonicalToolCall(
                    "create", "skill_create", json.dumps({"name": NAME, "skill_md": TEXT})
                ),
            )
            yield ProviderEvent(kind="finish", finish_reason="tool_calls")
        else:
            yield ProviderEvent(kind="text_delta", text="Criação concluída.")
            yield ProviderEvent(kind="finish", finish_reason="stop")


async def acceptance(home):
    assert Path(kairos_cli.__file__).is_relative_to("/opt/kairos")
    db = connect(home / "state.db")
    migrate(db)
    ref = ProviderModelRef("controlled", "offline")
    catalog = ModelCatalog()
    catalog.merge(
        [CatalogModel(ref, "Offline", capabilities=ModelCapabilities(chat=True, tools=True))],
        origin=CatalogOrigin.CURATED,
    )
    gateway = ControlledGateway()
    service = InteractionService(
        gateway=gateway,
        resolver=ModelSelectionResolver(catalog),
        context_loader=SimpleNamespace(load=lambda _: ModelSelectionContext(global_default=ref)),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=UsageRepository(db),
        skill_catalog_home=home,
    )
    master, slave = pty.openpty()
    original_stdin = sys.stdin
    output = io.StringIO()
    try:
        with os.fdopen(slave, "r") as terminal, redirect_stdout(output), redirect_stderr(output):
            sys.stdin = terminal
            os.write(master, b"criar learn-image\n")
            code = await _run_turn(
                service,
                session_id="learn-image",
                content="/learn transforme em procedimento",
                override=None,
                as_json=False,
                home=home,
            )
        assert code == 0, output.getvalue()
        assert (home / "skills" / NAME / "SKILL.md").read_bytes() == TEXT.encode()
        with database_connection(home) as connection:
            records = SkillMutationRepository(connection, home_id=catalog_home_id(home)).history()
            assert len(records) == 1
            record = records[0]
            assert record.state.value == "committed" and record.provenance.value == "user"
            assert record.sha256 == hashlib.sha256(TEXT.encode()).hexdigest()
        assert "kairos skills rollback " + record.operation_id in output.getvalue()
        row = db.execute("SELECT content,api_content FROM messages WHERE role='user'").fetchone()
        assert tuple(row) == ("/learn transforme em procedimento",) * 2
        async for _ in service.stream(InteractionEnvelope("learn-image", "cli", "continue")):
            pass
        assert gateway.requests[-1].tools == ()
        with (
            observe_org_policy(org_policy_context({}), home) as policy,
            database_connection(home, write=True) as connection,
        ):
            repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
            inverse = SkillMutationService(home, repository, policy=policy).rollback(
                record.operation_id, actor=Actor.USER_FOREGROUND
            )
            assert inverse.state.value == "committed"
        assert not (home / "skills" / NAME).exists()
        return {
            "creation": "committed",
            "origin": "user",
            "rollback": "committed",
            "provider": "controlled",
            "installed": True,
        }
    finally:
        sys.stdin = original_stdin
        os.close(master)
        db.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        print(json.dumps(asyncio.run(acceptance(Path(temporary))), sort_keys=True))
