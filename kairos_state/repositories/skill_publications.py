"""Journal de publicações com guard obrigatório na fronteira de commit."""

from __future__ import annotations

import json
import logging
import sqlite3

from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
    require,
    validate_hash,
    validate_id,
    validate_name,
)
from kairos_skills.publication_contract import (
    AcquisitionReceipt,
    ManifestDelta,
    PublicationDraft,
    PublicationWriter,
    draft_from_json,
    receipt_from_json,
    record_payload,
)
from kairos_state.skill_publications_schema import publication_record, validate_publication_schema

_LOGGER = logging.getLogger(__name__)


class SkillPublicationRepository:
    def __init__(self, connection, *, home_id):
        validate_hash(home_id)
        self._conn = connection
        self.home_id = home_id

    def _validate(self):
        try:
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None or row[0] != 11:
                raise sqlite3.DatabaseError("schema incompatível")
            validate_publication_schema(self._conn)
        except sqlite3.DatabaseError:
            raise SkillMutationError(
                "corrupt", "Ledger de publicação incoerente; operação recusada."
            ) from None

    def _write(self, operation, guard):
        require(callable(guard), "Guard de publicação obrigatório.")
        if self._conn.in_transaction:
            raise SkillMutationError("io", "Publicação exige transação própria.")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._validate()
            result = operation()
            self._validate()
            guard()
            self._conn.commit()
            return result
        except BaseException as error:
            self._conn.rollback()
            if isinstance(error, sqlite3.DatabaseError):
                _LOGGER.warning(
                    "skill_publication_write_refused",
                    extra={"action": "publish", "kind": "io", "reason": "database_write_failed"},
                )
                raise SkillMutationError("io", "Não foi possível registrar a publicação.") from None
            raise

    def receipt(self, acquisition_id):
        validate_id(acquisition_id)
        self._validate()
        row = self._conn.execute(
            "SELECT payload FROM skill_acquisition_receipts WHERE acquisition_id=? AND home_id=?",
            (acquisition_id, self.home_id),
        ).fetchone()
        return None if row is None else receipt_from_json(json.loads(row[0]))

    def issue(self, receipt, *, guard):
        require(type(receipt) is AcquisitionReceipt and receipt.home_id == self.home_id)

        def insert():
            current = self.receipt(receipt.acquisition_id)
            if current is not None:
                require(current == receipt, "ID de aquisição já ocupado.")
                return current
            self._conn.execute(
                "INSERT INTO skill_acquisition_receipts VALUES(?,?,?,?)",
                (receipt.acquisition_id, receipt.home_id, receipt.name, record_payload(receipt)),
            )
            return receipt

        return self._write(insert, guard)

    def get(self, operation_id):
        validate_id(operation_id)
        self._validate()
        row = self._conn.execute(
            "SELECT payload FROM skill_publication_operations WHERE operation_id=? AND home_id=?",
            (operation_id, self.home_id),
        ).fetchone()
        if row is None:
            return None
        draft = draft_from_json(json.loads(row[0]))
        events = [
            (r[0], r[1])
            for r in self._conn.execute(
                "SELECT state,payload FROM skill_publication_events WHERE operation_id=? ORDER BY sequence",
                (operation_id,),
            )
        ]
        return publication_record(draft, events)

    def history(self, *, name=None):
        if name is not None:
            validate_name(name)
        self._validate()
        ids = [
            row[0]
            for row in self._conn.execute(
                "SELECT operation_id FROM skill_publication_operations WHERE home_id=? AND (? IS NULL OR name=?) ORDER BY sequence",
                (self.home_id, name, name),
            )
        ]
        return tuple(self.get(operation_id) for operation_id in ids)

    def pending(self, name):
        return tuple(record for record in self.history(name=name) if record.pending)

    def prepare(self, draft, *, guard):
        require(type(draft) is PublicationDraft and draft.home_id == self.home_id)

        def insert():
            if self.get(draft.operation_id) is not None:
                raise SkillMutationError(
                    "conflict", "ID de publicação ocupado.", operation_id=draft.operation_id
                )
            pending = self.pending(draft.name)
            if pending:
                raise SkillMutationError(
                    "conflict",
                    "Publicação pendente bloqueia o nome.",
                    operation_id=pending[0].operation_id,
                )
            if draft.writer is PublicationWriter.QUARANTINE_PROMOTE:
                receipt = self.receipt(draft.source_ref.split("/")[0])
                require(
                    receipt is not None
                    and (receipt.source_proof, receipt.source_key, receipt.name)
                    == (draft.source_proof, draft.source_key, draft.name)
                )
            self._conn.execute(
                "INSERT INTO skill_publication_operations(operation_id,home_id,name,writer,payload) VALUES(?,?,?,?,?)",
                (
                    draft.operation_id,
                    draft.home_id,
                    draft.name,
                    draft.writer.value,
                    record_payload(draft),
                ),
            )
            from kairos_state.repositories.skill_org_protections import insert_policy_intent

            insert_policy_intent(self._conn, draft, draft.policy_receipt)
            self._conn.execute(
                "INSERT INTO skill_publication_events(operation_id,state) VALUES(?,'prepared')",
                (draft.operation_id,),
            )

        self._write(insert, guard)
        return self.get(draft.operation_id)

    def _event(self, operation_id, event, *, guard, delta=None):
        validate_id(operation_id)

        def insert():
            record = self.get(operation_id)
            require(record is not None, "Publicação não encontrada.")
            if event in ("committed", "aborted", "conflict"):
                if record.state.value == event:
                    return
                require(record.state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT))
            else:
                require(record.state is SkillMutationState.COMMITTED)
                if event == "manifest_prepared":
                    require(type(delta) is ManifestDelta and delta.name == record.name)
                    if delta == record.projection_delta:
                        return
                    require(record.manifest_complete and record.cleanup_complete)
                if (event == "manifest_applied" and record.manifest_complete) or (
                    event == "source_cleaned" and record.cleanup_complete
                ):
                    return
            self._conn.execute(
                "INSERT INTO skill_publication_events(operation_id,state,payload) VALUES(?,?,?)",
                (operation_id, event, None if delta is None else record_payload(delta)),
            )

        self._write(insert, guard)
        return self.get(operation_id)

    def finish(self, operation_id, state, *, guard):
        require(type(state) is SkillMutationState and state is not SkillMutationState.PREPARED)
        return self._event(operation_id, state.value, guard=guard)

    def prepare_manifest(self, operation_id, delta, *, guard):
        return self._event(operation_id, "manifest_prepared", guard=guard, delta=delta)

    def confirm_manifest(self, operation_id, *, guard):
        return self._event(operation_id, "manifest_applied", guard=guard)

    def confirm_cleanup(self, operation_id, *, guard):
        return self._event(operation_id, "source_cleaned", guard=guard)
