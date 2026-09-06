"""Repositórios especializados.

`hermes-state/requirements.md` §7 marca como dívida a eliminar: o legado
concentra 218 métodos numa classe única de 578 KB. A spec nomeia a
substituição — `SessionRepository`, `MessageRepository`, `LedgerRepository`,
`SearchIndex` — e é o que este pacote implementa.
"""

from kairos_state.repositories.leases import LeaseRepository
from kairos_state.repositories.ledger import LedgerRepository
from kairos_state.repositories.messages import CompressionLockLost, MessageRepository
from kairos_state.repositories.runtime import RuntimeRepository
from kairos_state.repositories.search import (
    Route,
    SearchCapabilities,
    SearchIndex,
    probe,
)
from kairos_state.repositories.sessions import SessionRepository
from kairos_state.repositories.usage import BillingRoute, TokenDelta, UsageRepository

__all__ = [
    "BillingRoute",
    "CompressionLockLost",
    "LeaseRepository",
    "LedgerRepository",
    "MessageRepository",
    "Route",
    "RuntimeRepository",
    "SearchCapabilities",
    "SearchIndex",
    "SessionRepository",
    "TokenDelta",
    "UsageRepository",
    "probe",
]
