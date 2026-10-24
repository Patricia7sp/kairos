"""Operações append-only e projeção verificável de propriedade."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, replace

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.adoption_contract import (
    AdoptionAuthority,
    SkillAdoptionAction,
    SkillAdoptionRecord,
)
from kairos_skills.identification_contract import InstallationEvidence
from kairos_skills.mutation_contract import (
    SkillDirectoryIdentity,
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationRecord,
    SkillMutationState,
    require,
    validate_hash,
    validate_id,
    validate_name,
    validate_skill_creation,
)
from kairos_skills.publication_contract import SkillPublicationAction, SkillPublicationRecord
from kairos_skills.removal_contract import (
    CommonRecord,
    InstallationProof,
    SkillTreeAction,
    SkillTreeDraft,
    SkillTreeRecord,
)
from kairos_state.contention import Budget
from kairos_state.skill_adoptions_schema import ADOPTION_TABLES, validate_adoption_schema
from kairos_state.skill_org_schema import ORG_OBJECTS, ORG_TABLES, validate_org_schema
from kairos_state.skill_publications_schema import PUBLICATION_TABLES, validate_publication_schema
from kairos_state.skill_writer import register_mutation_writer
from kairos_state.writes import write_with_retry


@contextmanager
def corrupt_errors():
    try:
        yield
    except (SkillMutationError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise SkillMutationError(
            "corrupt", "Ledger de skills incoerente; operação recusada e arquivos preservados."
        ) from None
    except sqlite3.DatabaseError:
        raise SkillMutationError("io", "Não foi possível ler o ledger de skills.") from None


def identity_json(identity: SkillDirectoryIdentity) -> str:
    return json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))


def authority_query(connection, query):
    """Seleciona a timeline canônica sem interpolar nomes ou entrada externa."""
    version = connection.execute("SELECT version FROM schema_version").fetchone()
    return (
        query.replace("skill_authority_timeline", "skill_publication_timeline")
        if version is not None and version[0] == 11
        else query
    )


def _adopt_installation(current, record, founders):
    draft = record.draft
    same_root = current is not None and (current.device, current.inode) == (
        draft.device,
        draft.inode,
    )
    if same_root:
        require(
            (current.adoption is not None and not current.adoption.active)
            or (
                current.adoption is None
                and current.provenance is None
                and current.installation_source
                == draft.installation_source
                == "verified_acquisition"
            )
        )
        require(draft.installation_id == current.operation_id)
    else:
        require(draft.installation_id == draft.operation_id)
    adoption = AdoptionAuthority(draft.adoption_id, draft.installation_id, True)
    require(draft.adoption_id not in founders)
    founders[draft.adoption_id] = record
    return InstallationProof(
        draft.installation_id,
        Provenance.USER,
        draft.device,
        draft.inode,
        adoption,
        current.installation_source if same_root else None,
    ), same_root


def _revoke_installation(current, record, founders):
    draft = record.draft
    require(current is not None and current.adoption is not None and current.adoption.active)
    require(
        (current.adoption.adoption_id, current.operation_id, current.device, current.inode)
        == (draft.adoption_id, draft.installation_id, draft.device, draft.inode)
    )
    require(
        draft.adoption_id in founders
        and founders[draft.adoption_id].draft.installation_source == draft.installation_source
    )
    return replace(current, adoption=replace(current.adoption, active=False))


def _restore_adoption(removed, record, adoption):
    if removed.draft.adoption is None:
        require(record.draft.adoption is None)
        return None
    require(adoption is not None and adoption.active)
    require(
        record.draft.adoption
        == replace(removed.draft.adoption, installation_id=record.operation_id)
    )
    require(record.draft.installation_source == removed.draft.installation_source)
    return record.draft.adoption


def _track_removed_tombstone(record, legacy_bundled_removed, adopted_removals):
    if record.provenance is Provenance.BUNDLED:
        legacy_bundled_removed = True
    if record.draft.adoption is not None:
        adopted_removals.add(record.operation_id)
    return legacy_bundled_removed


def _clear_linked_adoption_tombstone(removed, adopted_removals):
    if removed.draft.adoption is not None:
        adopted_removals.discard(removed.operation_id)


def _record_pending(record):
    return (
        record.pending
        if isinstance(record, SkillPublicationRecord)
        else record.state
        in (
            SkillMutationState.PREPARED,
            SkillMutationState.CONFLICT,
        )
    )


def _publication_installation(current, record):
    require(current == record.draft.previous_installation)
    root = record.draft.staged_tree.entries[0]
    return InstallationProof(
        record.operation_id,
        record.provenance,
        root.device,
        root.inode,
        installation_source="package_bundle"
        if record.provenance is Provenance.BUNDLED
        else "verified_acquisition",
    )


def _validate_removed_installation(current, record, founders):
    root = record.draft.proof.entries[0]
    if current is None:
        require(record.provenance is Provenance.BUNDLED and record.draft.adoption is None)
    else:
        require(
            (current.provenance, current.device, current.inode)
            == (record.provenance, root.device, root.inode)
        )
        require(current.adoption == record.draft.adoption)
        if current.adoption is not None:
            require(current.adoption.active)
            require(
                record.draft.installation_source
                == founders[current.adoption.adoption_id].draft.installation_source
            )


def _project_committed_records(
    records: tuple[CommonRecord, ...],
):
    current = None
    removed = None
    legacy_bundled_removed = False
    adopted_removals = set()
    historical_creation = None
    adoption = None
    founders = {}
    for record in records:
        if isinstance(record, SkillPublicationRecord):
            current = _publication_installation(current, record)
            historical_creation = adoption = removed = None
        elif record.action is SkillMutationAction.CREATE:
            require(current is None)
            identity = record.draft.identity
            current = InstallationProof(
                record.operation_id,
                Provenance.USER,
                identity.directory_dev,
                identity.directory_ino,
            )
            historical_creation = record
            adoption = None
        elif record.action is SkillMutationAction.ROLLBACK:
            require(current is not None and current.operation_id == record.reverts)
            current = None
            historical_creation = None
            adoption = None
        elif record.action is SkillAdoptionAction.ADOPT:
            current, same_root = _adopt_installation(current, record, founders)
            historical_creation = historical_creation if same_root else None
            adoption = current.adoption
        elif record.action is SkillAdoptionAction.UNADOPT:
            current = _revoke_installation(current, record, founders)
            adoption = current.adoption
        elif record.action is SkillTreeAction.REMOVE:
            _validate_removed_installation(current, record, founders)
            historical_creation = None if current is None else historical_creation
            current = None
            removed = record
            legacy_bundled_removed = _track_removed_tombstone(
                record, legacy_bundled_removed, adopted_removals
            )
        elif record.action is SkillTreeAction.RESTORE:
            require(
                current is None and removed is not None and removed.operation_id == record.reverts
            )
            root = record.draft.proof.entries[0]
            adoption = _restore_adoption(removed, record, adoption)
            current = InstallationProof(
                record.operation_id, record.provenance, root.device, root.inode, adoption
            )
            _clear_linked_adoption_tombstone(removed, adopted_removals)
            removed = None
            # Compatibilidade histórica: qualquer RESTORE committed encerra a
            # projeção antiga de remoção BUNDLED. Exclusões por adoção têm
            # identidade própria e só são encerradas pelo restore vinculado.
            legacy_bundled_removed = False
        else:
            require(False)
    return (
        current,
        removed,
        legacy_bundled_removed or bool(adopted_removals),
        historical_creation,
        adoption,
    )


def _timeline_rows(connection):
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    modern = {
        "skill_tree_operations",
        "skill_tree_events",
        "skill_tree_snapshots",
        "skill_tree_blobs",
        "skill_mutation_timeline",
    }
    version = None
    if "schema_version" in names:
        versions = connection.execute("SELECT version FROM schema_version").fetchall()
        require(len(versions) == 1 and type(versions[0][0]) is int and 0 <= versions[0][0] <= 11)
        version = versions[0][0]
    require(version in (10, 11) or not names & ORG_OBJECTS)
    require(version == 11 or not names & PUBLICATION_TABLES)
    if not names & modern:
        require((version is None or version < 8) and not names & ADOPTION_TABLES)
        return None
    require(modern <= names and version in (8, 9, 10, 11))
    adopted = version >= 9
    require((names >= ORG_TABLES) if version in (10, 11) else not names & ORG_TABLES)
    require((names >= ADOPTION_TABLES) if adopted else not names & ADOPTION_TABLES)
    if adopted:
        try:
            if version == 11:
                validate_publication_schema(connection)
            else:
                validate_adoption_schema(connection)
            if version == 10:
                validate_org_schema(connection)
        except sqlite3.DatabaseError:
            require(False)
    require(
        {"skill_mutation_operations", "skill_mutation_events", "skill_mutation_contents"} <= names
    )
    if version == 11:
        rows = connection.execute(
            "SELECT t.sequence,t.legacy_event,t.tree_event,COALESCE(l.operation_id,r.operation_id,a.operation_id,p.operation_id),COALESCE(l.state,r.state,a.state,p.state),t.adoption_event,t.publication_event "
            "FROM skill_publication_timeline t LEFT JOIN skill_mutation_events l ON l.sequence=t.legacy_event "
            "LEFT JOIN skill_tree_events r ON r.sequence=t.tree_event LEFT JOIN skill_adoption_events a ON a.sequence=t.adoption_event "
            "LEFT JOIN skill_publication_events p ON p.sequence=t.publication_event ORDER BY t.sequence"
        ).fetchall()
    elif adopted:
        rows = connection.execute(
            "SELECT t.sequence,t.legacy_event,t.tree_event,COALESCE(l.operation_id,r.operation_id,a.operation_id),COALESCE(l.state,r.state,a.state),t.adoption_event,NULL "
            "FROM skill_authority_timeline t LEFT JOIN skill_mutation_events l ON l.sequence=t.legacy_event "
            "LEFT JOIN skill_tree_events r ON r.sequence=t.tree_event LEFT JOIN skill_adoption_events a ON a.sequence=t.adoption_event ORDER BY t.sequence"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT t.sequence,t.legacy_event,t.tree_event,COALESCE(l.operation_id,r.operation_id),COALESCE(l.state,r.state),NULL,NULL "
            "FROM skill_mutation_timeline t LEFT JOIN skill_mutation_events l ON l.sequence=t.legacy_event "
            "LEFT JOIN skill_tree_events r ON r.sequence=t.tree_event ORDER BY t.sequence"
        ).fetchall()
    families = ("mutation", "tree", "adoption") if adopted else ("mutation", "tree")
    if version == 11:
        families += ("publication",)
    return rows, adopted, families


def validate_skill_timeline(connection: sqlite3.Connection):
    """Atesta famílias e referências antes de usar a sequência comum."""
    with corrupt_errors():
        observed = _timeline_rows(connection)
        if observed is None:
            return None
        rows, adopted, families = observed
        seen = [set() for _ in families]
        last_event = [0 for _ in families]
        operations = [set() for _ in families]
        previous = 0
        for sequence, legacy, tree, operation_id, state, authority, publication in rows:
            require(
                type(sequence) is int
                and sequence > previous
                and sum(value is not None for value in (legacy, tree, authority, publication)) == 1
            )
            family = next(
                index
                for index, value in enumerate((legacy, tree, authority, publication))
                if value is not None
            )
            event = (legacy, tree, authority, publication)[family]
            require(type(event) is int and event > 0 and event not in seen[family])
            require(event > last_event[family])
            last_event[family] = event
            validate_id(operation_id)
            if publication is not None and state in (
                "manifest_prepared",
                "manifest_applied",
                "source_cleaned",
            ):
                pass  # Confirmações são validadas pela família, sem reaplicar instalação.
            else:
                SkillMutationState(state)
            seen[family].add(event)
            operations[family].add(operation_id)
            previous = sequence
        queries = (
            (
                "SELECT sequence FROM skill_mutation_events",
                "SELECT operation_id FROM skill_mutation_operations",
            ),
            (
                "SELECT sequence FROM skill_tree_events",
                "SELECT operation_id FROM skill_tree_operations",
            ),
            (
                "SELECT sequence FROM skill_adoption_events",
                "SELECT operation_id FROM skill_adoption_operations",
            ),
            (
                "SELECT sequence FROM skill_publication_events",
                "SELECT operation_id FROM skill_publication_operations",
            ),
        )
        for family, (events_query, operations_query) in enumerate(queries[: len(families)]):
            require(seen[family] == {row[0] for row in connection.execute(events_query)})
            require(operations[family] == {row[0] for row in connection.execute(operations_query)})
            require(not any(operations[family] & other for other in operations[:family]))
        if adopted:
            require(
                operations[1]
                == {
                    row[0]
                    for row in connection.execute("SELECT operation_id FROM skill_tree_adoptions")
                }
            )
        return [tuple(row[:5]) for row in rows]


class SkillMutationRepository:
    def __init__(self, connection: sqlite3.Connection, *, home_id: str):
        validate_hash(home_id)
        self._conn = connection
        self._home_id = home_id
        self._record_memo = None

    @property
    def home_id(self):
        return self._home_id

    def _blob(self, draft: SkillMutationDraft):
        row = self._conn.execute(
            "SELECT size_bytes,length(CAST(text AS BLOB)) FROM skill_mutation_contents WHERE sha256=?",
            (draft.sha256,),
        ).fetchone()
        require(row is not None and type(row[0]) is int and row[0] == row[1] == draft.size_bytes)
        text = self._conn.execute(
            "SELECT text FROM skill_mutation_contents WHERE sha256=?", (draft.sha256,)
        ).fetchone()[0]
        creation = validate_skill_creation(text)
        require(
            (creation.name, creation.sha256, creation.size_bytes)
            == (draft.name, draft.sha256, draft.size_bytes)
        )
        return creation

    def _base(self, operation_id: str) -> SkillMutationRecord | None:
        length = self._conn.execute(
            "SELECT length(CAST(identity_json AS BLOB)) FROM skill_mutation_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if length is None:
            require(
                self._conn.execute(
                    "SELECT 1 FROM skill_mutation_events WHERE operation_id=?", (operation_id,)
                ).fetchone()
                is None
            )
            return None
        require(type(length[0]) is int and 0 < length[0] <= 1024)
        row = self._conn.execute(
            "SELECT sequence,operation_id,home_id,action,name,actor,created_at,identity_json,sha256,size_bytes,reverts "
            "FROM skill_mutation_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        require(type(row[7]) is str)
        identity = SkillDirectoryIdentity(**json.loads(row[7]))
        require(identity_json(identity) == row[7])
        draft = SkillMutationDraft(
            row[1],
            row[2],
            SkillMutationAction(row[3]),
            row[4],
            Actor(row[5]),
            row[6],
            identity,
            row[8],
            row[9],
            row[10],
        )
        require(draft.home_id == self.home_id)
        self._blob(draft)
        events = self._conn.execute(
            "SELECT sequence,state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence LIMIT 4",
            (operation_id,),
        ).fetchall()
        require(1 <= len(events) <= 3)
        state = None
        for event in events:
            require(type(event[0]) is int and event[0] > 0)
            next_state = SkillMutationState(event[1])
            require(
                (state is None and next_state is SkillMutationState.PREPARED)
                or (
                    state is SkillMutationState.PREPARED
                    and next_state is not SkillMutationState.PREPARED
                )
                or (
                    state is SkillMutationState.CONFLICT
                    and next_state in (SkillMutationState.COMMITTED, SkillMutationState.ABORTED)
                )
            )
            state = next_state
        return SkillMutationRecord(draft, row[0], state)

    def _legacy_get(self, operation_id: str) -> SkillMutationRecord | None:
        validate_id(operation_id)
        with corrupt_errors():
            record = self._base(operation_id)
            if record is not None and record.action is SkillMutationAction.ROLLBACK:
                parent = self._base(record.reverts)
                require(
                    parent is not None
                    and parent.action is SkillMutationAction.CREATE
                    and parent.state is SkillMutationState.COMMITTED
                    and parent.sequence < record.sequence
                )
                require(
                    (record.name, record.draft.identity, record.sha256, record.draft.size_bytes)
                    == (parent.name, parent.draft.identity, parent.sha256, parent.draft.size_bytes)
                )
            return record

    def _tree_repository(self):
        from kairos_state.repositories.skill_removals import SkillRemovalRepository

        return SkillRemovalRepository(self._conn, home_id=self.home_id)

    def _adoption_repository(self):
        from kairos_state.repositories.skill_adoptions import SkillAdoptionRepository

        return SkillAdoptionRepository(self._conn, home_id=self.home_id)

    def _timeline(self):
        return validate_skill_timeline(self._conn)

    def get(self, operation_id: str) -> CommonRecord | None:
        validate_id(operation_id)
        modern = self._timeline() is not None
        record = self._get_validated(operation_id, modern=modern)
        if record is not None:
            self._validate_adoption_projection(record.name)
        return record

    def _validate_adoption_projection(self, name: str) -> None:
        adopted = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='skill_adoption_operations'"
        ).fetchone()
        if (
            adopted is not None
            and self._conn.execute(
                "SELECT 1 FROM skill_adoption_operations WHERE name=? LIMIT 1", (name,)
            ).fetchone()
            is not None
        ):
            self._projection(name)

    def _get_validated(self, operation_id, *, modern):
        if self._record_memo is not None:
            return self._record_memo.load(
                operation_id,
                modern=modern,
                loader=lambda: self._get_validated_uncached(operation_id, modern=modern),
            )
        return self._get_validated_uncached(operation_id, modern=modern)

    def _get_validated_uncached(self, operation_id, *, modern):
        record = self._legacy_get(operation_id)
        if record is not None or not modern:
            return record
        record = self._tree_repository().get(operation_id)
        if record is not None:
            return record
        adopted = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='skill_adoption_operations'"
        ).fetchone()
        record = None if adopted is None else self._adoption_repository().get(operation_id)
        if record is not None:
            return record
        published = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='skill_publication_operations'"
        ).fetchone()
        if published is not None:
            from kairos_state.repositories.skill_publications import SkillPublicationRepository

            return SkillPublicationRepository(self._conn, home_id=self.home_id).get(operation_id)
        return None

    def content(self, operation_id: str):
        record = self.get(operation_id)
        if record is None or not isinstance(record, SkillMutationRecord):
            raise SkillMutationError("input", "Operação não encontrada neste histórico.")
        with corrupt_errors():
            return self._blob(record.draft)

    def _load_validated_records(self, name: str, *, timeline) -> tuple[CommonRecord, ...]:
        if timeline is not None:
            ids = dict.fromkeys(row[3] for row in timeline)
        else:
            rows = self._conn.execute(
                "SELECT operation_id FROM skill_mutation_operations WHERE name=? ORDER BY sequence",
                (name,),
            ).fetchall()
            ids = tuple(row[0] for row in rows)

        def load_records(loader):
            return tuple(loader(operation_id, modern=timeline is not None) for operation_id in ids)

        records = (
            load_records(self._get_validated)
            if self._record_memo is None
            else self._record_memo.run_batch(
                lambda: load_records(self._get_validated),
                uncached=lambda: load_records(self._get_validated_uncached),
            )
        )
        require(all(record is not None for record in records))
        return tuple(record for record in records if record.name == name)

    def _records(self, name: str):
        return self._load_validated_records(name, timeline=self._timeline())

    def pending(self, name: str):
        validate_name(name)
        with corrupt_errors():
            return tuple(record for record in self._records(name) if _record_pending(record))

    def latest_rollback(self, create_id: str):
        parent = self.get(create_id)
        require(
            parent is not None and parent.action is SkillMutationAction.CREATE,
            "Informe o ID de uma criação.",
        )
        with corrupt_errors():
            row = self._conn.execute(
                "SELECT operation_id FROM skill_mutation_operations WHERE reverts=? ORDER BY sequence DESC LIMIT 1",
                (create_id,),
            ).fetchone()
            return None if row is None else self.get(row[0])

    def history(self, *, name: str | None = None, limit: int = 20):
        require(type(limit) is int and 1 <= limit <= 100, "Limite deve ser um inteiro de 1 a 100.")
        if name is not None:
            validate_name(name)
        with corrupt_errors():
            timeline = self._timeline()
            if timeline is not None:
                ids = dict.fromkeys(row[3] for row in timeline)
                records = tuple(
                    self._get_validated(operation_id, modern=True) for operation_id in reversed(ids)
                )
                require(all(record is not None for record in records))
                for record_name in {
                    record.name for record in records if name is None or record.name == name
                }:
                    self._validate_adoption_projection(record_name)
                return tuple(record for record in records if name is None or record.name == name)[
                    :limit
                ]
            rows = self._conn.execute(
                "SELECT operation_id FROM skill_mutation_operations WHERE (? IS NULL OR name=?) ORDER BY sequence DESC LIMIT ?",
                (name, name, limit),
            ).fetchall()
            return tuple(self.get(row[0]) for row in rows)

    def provenance(self, name: str) -> Provenance | None:
        current = self.current_installation(name)
        return (
            None
            if current is None or (current.adoption is not None and not current.adoption.active)
            else current.provenance
        )

    def _current_create(self, name: str) -> str | None:
        current = self.current_installation(name)
        return None if current is None else current.operation_id

    def _projection_inputs(self, name: str) -> tuple[tuple[CommonRecord, ...], tuple[str, ...]]:
        validate_name(name)
        with corrupt_errors():
            timeline = self._timeline()
            records = self._load_validated_records(name, timeline=timeline)
            if timeline is not None:
                by_id = {record.operation_id: record for record in records}
                committed = tuple(
                    by_id[row[3]] for row in timeline if row[4] == "committed" and row[3] in by_id
                )
            else:
                committed = tuple(
                    record for record in records if record.state is SkillMutationState.COMMITTED
                )
            pending_ids = tuple(
                record.operation_id for record in records if _record_pending(record)
            )
            return committed, pending_ids

    def _projection(self, name: str):
        records, _ = self._projection_inputs(name)
        with corrupt_errors():
            return _project_committed_records(records)[:3]

    def installation_evidence(self, name: str) -> InstallationEvidence:
        records, pending_ids = self._projection_inputs(name)
        with corrupt_errors():
            current, removed, _, historical_creation, adoption = _project_committed_records(records)
            return InstallationEvidence(
                current,
                None if historical_creation is None else historical_creation.operation_id,
                None if removed is None else removed.operation_id,
                pending_ids,
                adoption,
            )

    def current_installation(self, name: str) -> InstallationProof | None:
        return self._projection(name)[0]

    def current_removal(self, name: str) -> SkillTreeRecord | None:
        return self._projection(name)[1]

    def has_later_installation(self, remove_id: str) -> bool:
        parent = self.get(remove_id)
        require(
            parent is not None
            and parent.action is SkillTreeAction.REMOVE
            and parent.state is SkillMutationState.COMMITTED,
            "Informe o ID de uma remoção concluída.",
        )
        with corrupt_errors():
            timeline = self._timeline()
            require(timeline is not None)
            records = {record.operation_id: record for record in self._records(parent.name)}
            committed = [row for row in timeline if row[4] == "committed"]
            removed_sequence = next(row[0] for row in committed if row[3] == remove_id)
            return any(
                row[0] > removed_sequence
                and row[3] in records
                and records[row[3]].action
                in (
                    SkillMutationAction.CREATE,
                    SkillTreeAction.RESTORE,
                    SkillPublicationAction.PUBLISH,
                )
                for row in committed
            )

    def snapshot(self, operation_id: str):
        if isinstance(self.get(operation_id), SkillAdoptionRecord):
            return self._adoption_repository().snapshot(operation_id)
        return self._tree_repository().snapshot(operation_id)

    def latest_restore(self, remove_id: str):
        parent = self.get(remove_id)
        require(
            parent is not None and parent.action is SkillTreeAction.REMOVE,
            "Informe o ID de uma remoção.",
        )
        with corrupt_errors():
            records = self._records(parent.name)
            return next(
                (
                    record
                    for record in reversed(records)
                    if record.action is SkillTreeAction.RESTORE and record.reverts == remove_id
                ),
                None,
            )

    def bundled_tombstones(self) -> frozenset[str]:
        with corrupt_errors():
            names = {
                row[0]
                for row in self._conn.execute("SELECT DISTINCT name FROM skill_mutation_operations")
            }
            if self._timeline() is not None:
                names.update(
                    row[0]
                    for row in self._conn.execute("SELECT DISTINCT name FROM skill_tree_operations")
                )
            return frozenset(name for name in names if self._projection(name)[2])

    def _assert_no_pending(self, name: str):
        pending = self.pending(name)
        if pending:
            raise SkillMutationError(
                "conflict",
                "Existe operação pendente para este nome; resolva seu ID.",
                operation_id=pending[0].operation_id,
            )

    def _prepare_tree(self, draft, prepare):
        require(type(draft) is SkillTreeDraft)
        tree = self._tree_repository()

        def guarded_write(operation):
            def guarded():
                self._assert_no_pending(draft.name)
                current, removed, _ = self._projection(draft.name)
                if draft.action is SkillTreeAction.REMOVE:
                    root = draft.proof.entries[0]
                    require(
                        (current is None and draft.provenance is Provenance.BUNDLED)
                        or (
                            current is not None
                            and (current.adoption is None or current.adoption.active)
                            and current.adoption == draft.adoption
                            and (current.provenance, current.device, current.inode)
                            == (draft.provenance, root.device, root.inode)
                        ),
                        "A instalação não tem a prova corrente exigida.",
                    )
                elif draft.action is SkillTreeAction.RESTORE:
                    if (
                        current is not None
                        or removed is None
                        or removed.operation_id != draft.reverts
                    ):
                        raise SkillMutationError(
                            "conflict",
                            "A remoção não é corrente ou o nome já tem instalação; versões preservadas.",
                            operation_id=current.operation_id
                            if current is not None
                            else (removed.operation_id if removed is not None else draft.reverts),
                        )
                else:
                    require(False)
                return operation()

            return self._write(guarded)

        tree._write = guarded_write
        return prepare(tree)

    def prepare_remove(self, draft, snapshot):
        return self._prepare_tree(draft, lambda tree: tree.prepare_remove(draft, snapshot))

    def prepare_restore(self, draft, *, policy_receipt=None):
        return self._prepare_tree(
            draft, lambda tree: tree.prepare_restore(draft, policy_receipt=policy_receipt)
        )

    def _write(self, operation, *, memo_records=False):
        register_mutation_writer(self._conn)

        def transaction():
            if self._conn.in_transaction:
                raise SkillMutationError("io", "Mutação exige uma conexão sem transação pendente.")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if memo_records:
                    from kairos_state.repositories.skill_record_memo import RecordMemo

                    self._record_memo = RecordMemo(self._conn, self.home_id, owner_token=object())
                try:
                    result = operation()
                finally:
                    if self._record_memo is not None:
                        self._record_memo.close()
                        self._record_memo = None
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            return result

        try:
            return write_with_retry(transaction, budget=Budget.TRANSCRIPT, detail="skill_mutation")
        except sqlite3.IntegrityError:
            raise SkillMutationError(
                "conflict", "O ledger recusou uma operação conflitante; arquivos preservados."
            ) from None
        except sqlite3.DatabaseError:
            raise SkillMutationError(
                "io", "Não foi possível confirmar a operação no ledger; consulte seu ID."
            ) from None

    def prepare(self, draft: SkillMutationDraft, text: str, *, policy_receipt=None):
        from kairos_state.repositories.skill_org_protections import (
            insert_policy_intent,
            validate_policy_receipt,
        )

        require(type(draft) is SkillMutationDraft)
        creation = validate_skill_creation(text)
        require(
            draft.home_id == self.home_id
            and (creation.name, creation.sha256, creation.size_bytes)
            == (draft.name, draft.sha256, draft.size_bytes)
        )

        def operation():
            if draft.action is SkillMutationAction.CREATE:
                validate_policy_receipt(self._conn, draft, policy_receipt)
            else:
                require(policy_receipt is None)
            self._assert_no_pending(draft.name)
            current = self._current_create(draft.name)
            if draft.action is SkillMutationAction.CREATE:
                require(current is None, "Já existe uma criação ativa para este nome.")
            elif draft.action is SkillMutationAction.ROLLBACK:
                parent = self.get(draft.reverts)
                require(
                    parent is not None
                    and parent.action is SkillMutationAction.CREATE
                    and parent.state is SkillMutationState.COMMITTED
                    and current == parent.operation_id
                )
                require(
                    (parent.name, parent.draft.identity, parent.sha256, parent.draft.size_bytes)
                    == (draft.name, draft.identity, draft.sha256, draft.size_bytes)
                )
            else:
                require(False)
            self._conn.execute(
                "INSERT INTO skill_mutation_contents(sha256,text,size_bytes) VALUES (?,?,?) ON CONFLICT(sha256) DO NOTHING",
                (draft.sha256, text, draft.size_bytes),
            )
            with corrupt_errors():
                self._blob(draft)
            self._conn.execute(
                "INSERT INTO skill_mutation_operations(operation_id,home_id,action,name,actor,created_at,identity_json,sha256,size_bytes,reverts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    draft.operation_id,
                    draft.home_id,
                    draft.action.value,
                    draft.name,
                    draft.actor.value,
                    draft.created_at,
                    identity_json(draft.identity),
                    draft.sha256,
                    draft.size_bytes,
                    draft.reverts,
                ),
            )
            if draft.action is SkillMutationAction.CREATE:
                insert_policy_intent(self._conn, draft, policy_receipt)
            self._conn.execute(
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?,?)",
                (draft.operation_id, "prepared"),
            )
            return self.get(draft.operation_id)

        return self._write(operation, memo_records=True)

    def finish(self, operation_id: str, state: SkillMutationState):
        validate_id(operation_id)
        require(
            type(state) is SkillMutationState and state is not SkillMutationState.PREPARED,
            "Estado terminal inválido.",
        )
        if isinstance(self.get(operation_id), SkillTreeRecord):
            return self._tree_repository().finish(operation_id, state)
        if isinstance(self.get(operation_id), SkillAdoptionRecord):
            return self._adoption_repository().finish(operation_id, state)

        def operation():
            record = self.get(operation_id)
            require(record is not None, "Operação não encontrada no histórico.")
            if record.state is state:
                return record
            require(
                record.state is SkillMutationState.PREPARED
                or (
                    record.state is SkillMutationState.CONFLICT
                    and state in (SkillMutationState.COMMITTED, SkillMutationState.ABORTED)
                ),
                "Transição de estado recusada.",
            )
            self._conn.execute(
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?,?)",
                (operation_id, state.value),
            )
            return self.get(operation_id)

        return self._write(operation, memo_records=True)
