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
)
from kairos_skills.mutation_io import SkillMutationFiles, read_skill_creation
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import reconcile_pending
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

    def add(self, source: Path, *, actor: Actor):
        validate_actor(actor)
        creation = read_skill_creation(source)
        operation_id = uuid.uuid4().hex
        try:
            with skill_mutation_lock(self.home), SkillMutationFiles(self.home) as files:
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
