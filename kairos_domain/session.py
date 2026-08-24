"""Sessão, linhagem e as três identidades.

Reconstruído de ``_reversa_sdd/domain.md`` §1.1 e
``_reversa_sdd/data-dictionary.md`` §6.9 (Tarefa 02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from kairos_domain.identity import SessionSource

__all__ = [
    "RESET_END_REASONS",
    "ChildKind",
    "EndReason",
    "Lineage",
    "Session",
]


class EndReason(StrEnum):
    COMPRESSION = "compression"
    BRANCHED = "branched"
    SESSION_RESET = "session_reset"
    SESSION_SWITCH = "session_switch"
    IDLE = "idle"
    DAILY = "daily"
    SUSPENDED = "suspended"
    RESUME_PENDING_EXPIRED = "resume_pending_expired"


#: data-dictionary §6.9 — as razões de fim que caracterizam um filho de reset.
RESET_END_REASONS = frozenset(
    {
        EndReason.SESSION_RESET,
        EndReason.SESSION_SWITCH,
        EndReason.IDLE,
        EndReason.DAILY,
        EndReason.SUSPENDED,
        EndReason.RESUME_PENDING_EXPIRED,
    }
)


class ChildKind(StrEnum):
    """Como uma sessão nasceu de outra."""

    ROOT = "root"
    BRANCH = "branch"
    COMPRESSION = "compression"
    RESET = "reset"


@dataclass
class Session:
    """Uma conversa persistida.

    Tem **três identidades** (domain §1.1), e confundi-las é a origem do
    #64934:

    * ``id`` — identidade **durável**. Navegação, pins, e o lado onde o lock
      mora. Sobrevive a tudo menos ao delete.
    * ``session_key`` — identidade de **roteamento**. Resolve N:1 para ``id``,
      e por isso **nunca** deve ancorar um lock.
    * a raiz da linhagem — sobrevive à compressão e carrega o histórico
      completo, enquanto a sessão ativa raramente passa de ~300K tokens.
    """

    id: str
    source: str
    started_at: float
    session_key: str | None = None
    origin: SessionSource | None = None
    parent_session_id: str | None = None
    ended_at: float | None = None
    end_reason: EndReason | None = None
    model: str | None = None
    branched_from: str | None = None
    reset_from: str | None = None
    compression_ineffective_count: int = 0
    archived: bool = False
    pinned: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Session exige id")
        if self.parent_session_id == self.id:
            raise ValueError("uma sessão não pode ser pai de si mesma")

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


@dataclass
class Lineage:
    """A cadeia ``parent_session_id``.

    Traduz para o domínio os predicados SQL de ``data-dictionary`` §6.9.
    """

    sessions: dict[str, Session] = field(default_factory=dict)

    def add(self, session: Session) -> None:
        self.sessions[session.id] = session

    def parent_of(self, session: Session) -> Session | None:
        if session.parent_session_id is None:
            return None
        return self.sessions.get(session.parent_session_id)

    def child_kind(self, session: Session) -> ChildKind:
        """``_BRANCH_CHILD_SQL`` / ``_COMPRESSION_CHILD_SQL`` / ``_RESET_CHILD_SQL``."""
        parent = self.parent_of(session)
        if parent is None:
            return ChildKind.ROOT

        # Branch: marcador explícito, OU pai encerrado por branch com a filha
        # começando depois (a forma legada, antes do marcador existir).
        if session.branched_from is not None:
            return ChildKind.BRANCH
        if (
            parent.end_reason is EndReason.BRANCHED
            and parent.ended_at is not None
            and session.started_at >= parent.ended_at
        ):
            return ChildKind.BRANCH

        if parent.end_reason is EndReason.COMPRESSION:
            return ChildKind.COMPRESSION

        if session.reset_from is not None or parent.end_reason in RESET_END_REASONS:
            return ChildKind.RESET

        return ChildKind.ROOT

    def is_listable(self, session: Session) -> bool:
        """``_LISTABLE_CHILD_SQL``.

        Filhos de **compressão não aparecem** na listagem: a compactação é um
        evento interno da mesma conversa, não uma conversa nova. Mostrá-los
        faria a lista crescer sozinha enquanto o usuário conversa.
        """
        return self.child_kind(session) is not ChildKind.COMPRESSION

    def root_of(self, session: Session) -> Session:
        """A raiz da linhagem — a identidade que sobrevive à compressão."""
        seen: set[str] = set()
        current = session
        while True:
            if current.id in seen:
                raise ValueError(f"ciclo na linhagem em {current.id!r}")
            seen.add(current.id)
            parent = self.parent_of(current)
            if parent is None:
                return current
            current = parent

    def chain(self, session: Session) -> list[Session]:
        """Da raiz até ``session``, inclusive."""
        out: list[Session] = []
        seen: set[str] = set()
        current: Session | None = session
        while current is not None:
            if current.id in seen:
                raise ValueError(f"ciclo na linhagem em {current.id!r}")
            seen.add(current.id)
            out.append(current)
            current = self.parent_of(current)
        return list(reversed(out))
