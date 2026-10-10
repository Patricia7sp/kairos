"""Autoria manual coordenada entre journal durável e filesystem."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationState,
    require,
    validate_actor,
    validate_id,
)
from kairos_skills.mutation_io import SkillMutationFiles, read_skill_creation
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import expected_entry, mark_conflict, reconcile_pending
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def operation_error(error, operation_id: str) -> SkillMutationError:
    kind = error.kind if isinstance(error, SkillMutationError) else "io"
    message = (
        str(error)
        if isinstance(error, SkillMutationError)
        else "Não foi possível concluir a operação; arquivos preservados."
    )
    return SkillMutationError(
        kind, message, operation_id=getattr(error, "operation_id", None) or operation_id
    )


class SkillMutationService:
    def __init__(self, home: Path, repository: SkillMutationRepository):
        self.home = Path(home).expanduser().absolute()
        require(".." not in self.home.parts, "Home não pode conter escapes.")
        require(
            repository.home_id == catalog_home_id(self.home), "Ledger pertence a outra instalação."
        )
        self.repository = repository

    def history(self, *, name: str | None = None, limit: int = 20):
        return self.repository.history(name=name, limit=limit)

    def ownership(self, name: str) -> Provenance:
        return self.repository.provenance(name) or Provenance.USER

    def _abort_staged(self, files, operation_id, expected):
        # Só há autorização de limpeza para a identidade preparada por esta chamada.
        if files.inspect_private(operation_id) != expected:
            raise SkillMutationError(
                "conflict", "Staging alterado; conteúdo preservado.", operation_id=operation_id
            )
        files.discard_staging(operation_id, expected)
        record = self.repository.get(operation_id)
        if record is not None and record.state in (
            SkillMutationState.PREPARED,
            SkillMutationState.CONFLICT,
        ):
            self.repository.finish(operation_id, SkillMutationState.ABORTED)

    def _assert_legacy_pending(self, name):
        for record in self.repository.pending(name):
            if record.action not in (SkillMutationAction.CREATE, SkillMutationAction.ROLLBACK):
                raise SkillMutationError(
                    "conflict",
                    "Existe operação de árvore pendente; resolva seu ID antes de escrever.",
                    operation_id=record.operation_id,
                )

    def add(self, source: Path, *, actor: Actor):
        validate_actor(actor)
        creation = read_skill_creation(source)
        operation_id = uuid.uuid4().hex
        try:
            with skill_mutation_lock(self.home), SkillMutationFiles(self.home) as files:
                self._assert_legacy_pending(creation.name)
                reconcile_pending(self.repository, files, name=creation.name)
                files.assert_name_available(creation.name)
                entry = files.stage(operation_id, creation)
                attempted = False
                try:
                    draft = SkillMutationDraft(
                        operation_id,
                        self.repository.home_id,
                        SkillMutationAction.CREATE,
                        creation.name,
                        actor,
                        time.time(),
                        entry.identity,
                        creation.sha256,
                        creation.size_bytes,
                    )
                    self.repository.prepare(draft, creation.text)
                    attempted = True
                    files.publish(operation_id, creation.name)
                    if files.inspect_installed(creation.name) != entry:
                        raise SkillMutationError(
                            "conflict", "Publicação divergiu; conteúdo preservado."
                        )
                    return self.repository.finish(operation_id, SkillMutationState.COMMITTED)
                except (SkillMutationError, OSError):
                    if not attempted:
                        self._abort_staged(files, operation_id, entry)
                    raise
        except (SkillMutationError, OSError) as error:
            raise operation_error(error, operation_id) from None

    def rollback(self, create_id: str, *, actor: Actor):
        validate_actor(actor)
        validate_id(create_id)
        operation_id = create_id
        try:
            with skill_mutation_lock(self.home):
                created = self.repository.get(create_id)
                require(
                    created is not None
                    and created.action is SkillMutationAction.CREATE
                    and created.state is SkillMutationState.COMMITTED,
                    "Informe o ID de uma criação concluída neste home.",
                )
                previous = self.repository.latest_rollback(create_id)
                if previous is not None and previous.state is SkillMutationState.COMMITTED:
                    return previous
                with SkillMutationFiles(self.home) as files:
                    self._assert_legacy_pending(created.name)
                    reconcile_pending(self.repository, files, name=created.name)
                    previous = self.repository.latest_rollback(create_id)
                    if previous is not None and previous.state is SkillMutationState.COMMITTED:
                        return previous
                    current = self.repository.current_installation(created.name)
                    if current is None or current.operation_id != create_id:
                        raise SkillMutationError(
                            "conflict",
                            "A criação não é a instalação corrente; arquivos preservados.",
                            operation_id=None if current is None else current.operation_id,
                        )
                    expected = expected_entry(self.repository, created)
                    if files.inspect_installed(created.name) != expected:
                        raise SkillMutationError(
                            "conflict",
                            "A criação foi editada, substituída ou removida; arquivos preservados.",
                        )
                    operation_id = uuid.uuid4().hex
                    draft = SkillMutationDraft(
                        operation_id,
                        self.repository.home_id,
                        SkillMutationAction.ROLLBACK,
                        created.name,
                        actor,
                        time.time(),
                        expected.identity,
                        expected.creation.sha256,
                        expected.creation.size_bytes,
                        create_id,
                    )
                    self.repository.prepare(draft, expected.creation.text)
                    files.retire(created.name, operation_id)
                    try:
                        unchanged = files.inspect_private(operation_id, retired=True) == expected
                    except SkillMutationError as error:
                        if error.kind not in ("input", "conflict"):
                            raise
                        unchanged = False
                    if not unchanged:
                        self._restore_changed(files, draft, expected)
                        raise SkillMutationError(
                            "conflict",
                            "Edição concorrente preservada e devolvida; rollback abortado.",
                        )
                    if not files.installed_name_absent(created.name):
                        mark_conflict(self.repository, self.repository.get(operation_id))
                    return self.repository.finish(operation_id, SkillMutationState.COMMITTED)
        except (SkillMutationError, OSError) as error:
            raise operation_error(error, operation_id) from None

    def _restore_changed(self, files, draft, expected):
        record = self.repository.get(draft.operation_id)
        if not files.directory_owned(draft.operation_id, expected.identity, retired=True):
            mark_conflict(self.repository, record)
        try:
            files.restore(draft.operation_id, draft.name)
        except SkillMutationError as error:
            if error.kind == "conflict":
                mark_conflict(self.repository, record)
            raise
        if not files.directory_owned(draft.name, expected.identity):
            mark_conflict(self.repository, record)
        self.repository.finish(draft.operation_id, SkillMutationState.ABORTED)
