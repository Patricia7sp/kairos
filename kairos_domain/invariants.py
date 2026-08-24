"""Os 15 invariantes do sistema, nomeados e rastreáveis.

``_reversa_sdd/domain.md`` §4: *"Violar qualquer um destes é bug por
definição."*

Este módulo é um **registro**, não uma camada de execução. Cada invariante
aponta para onde ele é imposto — no domínio, quando é imponível aqui; na
unit responsável, quando depende de infraestrutura que ainda não existe.
O valor do registro é que um invariante sem local de imposição fica
**visível** em vez de esquecido.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["INVARIANTS", "Enforcement", "Invariant", "by_module", "unenforced"]


class Enforcement(StrEnum):
    DOMAIN = "domain"  # imposto neste pacote, com teste
    SCHEMA = "schema"  # imposto pelo banco (Tarefa 01)
    HARNESS = "harness"  # imposto pela suíte — quem viola é o teste
    DEFERRED = "deferred"  # depende de unit ainda não construída


@dataclass(frozen=True)
class Invariant:
    number: int
    statement: str
    module: str
    enforcement: Enforcement
    #: Onde vive a imposição: função do domínio, objeto de schema, ou tarefa.
    enforced_at: str


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        1,
        "O prefixo de cache não muda mid-conversa (exceto compactação)",
        "agent",
        Enforcement.DEFERRED,
        "Tarefa 13 — agent",
    ),
    Invariant(
        2,
        "Alternância estrita de papéis; nunca duas do mesmo papel seguidas",
        "agent",
        Enforcement.DOMAIN,
        "message.check_role_alternation",
    ),
    Invariant(
        3,
        "Nunca uma mensagem de usuário sintética injetada mid-loop",
        "agent",
        Enforcement.DOMAIN,
        "message.check_no_synthetic_user_message",
    ),
    Invariant(
        4,
        "Compactação nunca corta um par tool_call/tool_result",
        "agent",
        Enforcement.DOMAIN,
        "message.check_tool_pairs_intact + compaction.plan_boundary",
    ),
    Invariant(
        5,
        "O lock pertence à identidade durável do dado, nunca à de roteamento",
        "gateway",
        Enforcement.SCHEMA,
        "session_turn_leases.conversation_id → sessions.id",
    ),
    Invariant(
        6,
        "Linhas canônicas de sessions/messages nunca são modificadas pelo reparo",
        "hermes_state",
        Enforcement.DOMAIN,
        "kairos_state.migrations.repair_derived_objects + kairos_state.migrations.canonical_fingerprint",
    ),
    Invariant(
        7,
        "Estados terminais do ledger são imutáveis",
        "cron",
        Enforcement.DOMAIN,
        "scheduling.assert_terminal_immutable",
    ),
    Invariant(
        8,
        "'Abandonado' só com prova de morte (PID + hora de início)",
        "cron",
        Enforcement.DOMAIN,
        "scheduling.finish_status_for_dead_owner",
    ),
    Invariant(
        9,
        "Atores autônomos nunca fazem hard-delete",
        "skills",
        Enforcement.DOMAIN,
        "ownership.assert_can_hard_delete",
    ),
    Invariant(
        10,
        "Skill do usuário nunca é auto-curada",
        "skills",
        Enforcement.DOMAIN,
        "ownership.can_archive",
    ),
    # Único invariante cuja imposição vive no HARNESS, não no código de
    # produção: quem o viola é o teste. Ver tests/conftest.py.
    Invariant(
        11,
        "Um teste nunca toca o state.db de produção",
        "hermes_state",
        Enforcement.HARNESS,
        "tests/conftest.py",
    ),
    Invariant(
        12,
        "Após qualquer swap, socket ativo + perfil ativo + átomos de conexão concordam",
        "desktop",
        Enforcement.DEFERRED,
        "Tarefa 19 — apps-desktop",
    ),
    Invariant(
        13,
        "Caminhos sensíveis pedem aprovação mesmo sob política autônoma",
        "acp_adapter",
        Enforcement.DEFERRED,
        "Tarefa 17 — acp-adapter (T-15)",
    ),
    Invariant(
        14,
        "Exceção no aprovador = negação (fail-closed)",
        "acp_adapter",
        Enforcement.DEFERRED,
        "Tarefa 17 — acp-adapter",
    ),
    Invariant(
        15,
        "Um job com subprocesso morto nunca reporta sucesso",
        "cron",
        Enforcement.DOMAIN,
        "scheduling.assert_not_reporting_success_when_dead",
    ),
)


def unenforced() -> tuple[Invariant, ...]:
    """Os que ainda dependem de units futuras. Deve encolher a cada tarefa."""
    return tuple(i for i in INVARIANTS if i.enforcement is Enforcement.DEFERRED)


def by_module(module: str) -> tuple[Invariant, ...]:
    return tuple(i for i in INVARIANTS if i.module == module)
