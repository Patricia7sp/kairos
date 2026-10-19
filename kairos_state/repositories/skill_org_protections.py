"""Consulta fail-closed e preparo de recibos na transação do journal."""

import sqlite3

from kairos_skills.mutation_contract import (
    SkillMutationError,
    require,
    validate_hash,
    validate_id,
    validate_name,
)
from kairos_skills.org_policy_contract import OrgPolicyReceipt
from kairos_state.skill_org_schema import bindings_digest, read_org_receipt


def has_org_schema(connection):
    row = connection.execute("SELECT version FROM schema_version").fetchone()
    return row is not None and row[0] == 10


def validate_policy_receipt(connection, draft, receipt):
    if has_org_schema(connection):
        require(type(receipt) is OrgPolicyReceipt, "Instalação exige recibo explícito da política.")
        require((receipt.home_id, receipt.name) == (draft.home_id, draft.name))
    else:
        require(receipt is None, "Schema antigo não registra recibo de proteção.")


def insert_policy_intent(connection, draft, receipt):
    validate_policy_receipt(connection, draft, receipt)
    if receipt is None:
        return
    connection.execute(
        "INSERT INTO skill_org_intents(operation_id,home_id,name,observation_id,policy_digest,bindings_digest) VALUES(?,?,?,?,?,?)",
        (
            draft.operation_id,
            draft.home_id,
            draft.name,
            receipt.observation_id,
            receipt.source_digest,
            bindings_digest(receipt.bindings),
        ),
    )
    connection.executemany(
        "INSERT INTO skill_org_intent_bindings(operation_id,organization_id,active) VALUES(?,?,?)",
        [
            (draft.operation_id, binding.organization_id, int(binding.active))
            for binding in receipt.bindings
        ],
    )


class SkillOrgProtectionRepository:
    def __init__(self, connection: sqlite3.Connection, *, home_id: str):
        validate_hash(home_id)
        self._conn = connection
        self.home_id = home_id

    def _validate(self):
        from kairos_state.repositories.skill_mutations import validate_skill_timeline

        try:
            if not has_org_schema(self._conn):
                raise sqlite3.DatabaseError("schema de proteção incompatível")
            validate_skill_timeline(self._conn)
        except (sqlite3.DatabaseError, ValueError, TypeError):
            raise SkillMutationError(
                "corrupt", "Ledger de proteção incoerente; operação recusada."
            ) from None

    def protected_organizations(self, name: str) -> frozenset[str]:
        validate_name(name)
        self._validate()
        return frozenset(
            row[0]
            for row in self._conn.execute(
                "SELECT organization_id FROM skill_org_protections WHERE home_id=? AND name=?",
                (self.home_id, name),
            )
        )

    def prepared_receipt(self, operation_id: str) -> OrgPolicyReceipt | None:
        validate_id(operation_id)
        self._validate()
        receipt = read_org_receipt(self._conn, operation_id)
        require(receipt is None or receipt.home_id == self.home_id)
        return receipt
