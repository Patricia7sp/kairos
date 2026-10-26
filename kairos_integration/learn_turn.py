"""Proposta em memória e publicação vinculada a uma confirmação específica."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import uuid
from collections.abc import Callable
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path

from kairos_domain.ownership import Actor
from kairos_providers._async_cleanup import run_persistent_cleanup
from kairos_skills.catalog import catalog_home_id
from kairos_skills.frontmatter import parse_frontmatter
from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
    validate_skill_creation,
)
from kairos_skills.mutations import SkillMutationService
from kairos_skills.org_policy import observe_org_policy, org_policy_context
from kairos_skills.removal_state import database_connection
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_tools.skill_create import SkillCreationCapability

_LOGGER = logging.getLogger(__name__)


def _require(condition, message="Proposta de criação inválida.", *, kind="input"):
    if not condition:
        raise SkillMutationError(kind, message)


@dataclass(frozen=True)
class CreationProposal:
    id: str
    session_id: str
    call_id: str
    name: str
    description: str
    skill_md: str
    sha256: str
    size_bytes: int


class LearnTurn:
    def __init__(self, *, home: Path, session_id: str, request: str, guard: Callable[[], None]):
        self.home = home
        self.session_id = session_id
        self.request = request
        self._guard = guard
        self.capability = SkillCreationCapability(self.publish)
        self._pending: tuple[CreationProposal, asyncio.Future[str]] | None = None
        self._publishing = False

    def check(self):
        _require(not self.capability.closed, "Capacidade de criação encerrada.", kind="denied")
        self._guard()

    def propose(self, call):
        from kairos_integration.chat_tools import _body_arguments

        self.check()
        _require(
            self._pending is None and not self._publishing,
            "Já existe uma proposta neste turno.",
            kind="denied",
        )
        try:
            arguments = _body_arguments(call.arguments)
        except UnicodeError:
            arguments = None
        _require(arguments is not None and set(arguments) == {"name", "skill_md"})
        _require(type(arguments["name"]) is str and type(arguments["skill_md"]) is str)
        creation = validate_skill_creation(arguments["skill_md"])
        _require(arguments["name"] == creation.name, "Nome diverge do frontmatter.")
        metadata, _ = parse_frontmatter(creation.text)
        proposal = CreationProposal(
            uuid.uuid4().hex,
            self.session_id,
            call.id,
            creation.name,
            metadata.description,
            creation.text,
            creation.sha256,
            creation.size_bytes,
        )
        future = asyncio.get_running_loop().create_future()
        self._pending = proposal, future
        return proposal, future

    def proposal(self, approval_id: str, session_id: str) -> CreationProposal:
        self.check()
        _require(self._pending is not None, "Proposta expirada.", kind="denied")
        proposal, future = self._pending
        _require(
            proposal.id == approval_id and proposal.session_id == session_id and not future.done(),
            "Proposta expirada ou de outra conversa.",
            kind="denied",
        )
        return proposal

    def decide(self, approval_id: str, session_id: str, sha256: str, answer: str):
        proposal = self.proposal(approval_id, session_id)
        _require(proposal.sha256 == sha256, "Hash da proposta diverge.", kind="denied")
        allowed = answer == f"criar {proposal.name}"
        self._pending[1].set_result("allow" if allowed else "deny")
        if not allowed:
            self.capability.closed = True

    def close(self):
        self.capability.closed = True
        if self._pending is not None and not self._pending[1].done():
            self._pending[1].cancel()

    async def publish(self, name: str, skill_md: str):
        try:
            self.check()
            _require(
                self._pending is not None and not self._publishing,
                "Criação não aprovada.",
                kind="denied",
            )
            proposal, future = self._pending
            _require(
                future.done() and not future.cancelled() and future.result() == "allow",
                "Criação não aprovada.",
                kind="denied",
            )
            _require(
                (name, skill_md) == (proposal.name, proposal.skill_md),
                "Conteúdo aprovado diverge.",
                kind="denied",
            )
        except SkillMutationError as error:
            return {"error": str(error), "status": error.kind}
        self._publishing = True
        worker = asyncio.get_running_loop().run_in_executor(
            None, copy_context().run, self._publish_approved, proposal
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            self.close()

            async def finish_writer():
                await asyncio.shield(worker)

            outcome = await run_persistent_cleanup(
                finish_writer, task_name="kairos-learn-writer-drain"
            )
            if outcome.error is not None:
                _LOGGER.warning(
                    "skill_learn_cancel_cleanup_failed",
                    extra={
                        "kind": "cancelled",
                        "error_class": type(outcome.error).__name__,
                        "phase": "cleanup",
                    },
                )
                raise cancellation from outcome.error
            raise
        finally:
            self.close()

    def _publish_approved(self, proposal: CreationProposal):
        record = None
        try:
            self.check()
            with (
                observe_org_policy(org_policy_context(os.environ), self.home) as policy,
                database_connection(self.home, write=True) as connection,
            ):
                migrate(connection)
                repository = SkillMutationRepository(connection, home_id=catalog_home_id(self.home))
                service = SkillMutationService(self.home, repository, policy=policy)
                record = service.add_text(
                    proposal.skill_md, actor=Actor.USER_FOREGROUND, publication_guard=self.check
                )
                _require(
                    record.state is SkillMutationState.COMMITTED
                    and (record.name, record.sha256) == (proposal.name, proposal.sha256),
                    "Publicação não comprovada.",
                    kind="conflict",
                )
                self.capability.closed = True
                return {
                    "operation_id": record.operation_id,
                    "name": record.name,
                    "state": record.state.value,
                    "origin": record.provenance.value,
                    "sha256": record.sha256,
                }
        except SkillMutationError as error:
            operation_id = error.operation_id or (None if record is None else record.operation_id)
            _LOGGER.warning(
                "skill_learn_publication_refused",
                extra={"kind": error.kind, "operation_id": operation_id, "phase": "publication"},
            )
            return {"error": str(error), "status": error.kind, "operation_id": operation_id}
        except (OSError, sqlite3.DatabaseError) as error:
            operation_id = None if record is None else record.operation_id
            _LOGGER.warning(
                "skill_learn_publication_failed",
                extra={
                    "kind": "io",
                    "error_class": type(error).__name__,
                    "operation_id": operation_id,
                    "phase": "publication",
                },
            )
            return {
                "error": "Falha ao publicar skill; consulte skills history.",
                "status": "io",
                "operation_id": operation_id,
            }
        finally:
            self.close()
