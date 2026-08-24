"""As 13 máquinas de estado do sistema.

``_reversa_sdd/state-machines.md``. A tabela-resumo (§15) lista 13 entidades
com estado; a linhagem de sessão (§2) está dobrada em Sessão, porque é
classificação de parentesco e não ciclo de vida — ela vive em
``kairos_domain.session.Lineage`` desde a Tarefa 02.

Estados reaproveitados da Tarefa 02 (``ExecutionStatus``, ``JobState``,
``SkillState``, ``Visibility``) **não são redeclarados** aqui.
"""

from __future__ import annotations

from enum import StrEnum

from kairos_domain.scheduling import ExecutionStatus
from kairos_domain.statemachine import StateMachine
from kairos_domain.statemachine import Transition as T

__all__ = ["MACHINES", "machine"]


# ---------------------------------------------------------------------------
# 1. Sessão
# ---------------------------------------------------------------------------


class SessionState(StrEnum):
    ACTIVE = "ativa"
    SUSPENDED = "suspensa"
    RESUME_PENDING = "resume_pending"
    ENDED = "encerrada"


SESSION = StateMachine(
    name="sessao",
    source_ref="hermes_state.py::classify_session_status; colunas end_reason/suspended/resume_pending",
    states=frozenset(SessionState),
    initial=SessionState.ACTIVE,
    transitions=(
        T(SessionState.ACTIVE, SessionState.ACTIVE, "turno normal"),
        T(SessionState.ACTIVE, SessionState.SUSPENDED, "suspended=1"),
        T(SessionState.SUSPENDED, SessionState.ACTIVE, "reopen_session()"),
        T(
            SessionState.ACTIVE,
            SessionState.RESUME_PENDING,
            "resume_pending=1",
            "marcador de turno interrompido",
        ),
        T(SessionState.RESUME_PENDING, SessionState.ACTIVE, "session.resume + auto-continue"),
        T(SessionState.RESUME_PENDING, SessionState.ENDED, "resume_pending_expired"),
        T(SessionState.ACTIVE, SessionState.ENDED, "end_session(end_reason)"),
        # Encerrada NÃO é terminal: reabrir é caminho de primeira classe. É a
        # regra 7 de compactação — a conversa mantém UM id para a vida.
        T(SessionState.ENDED, SessionState.ACTIVE, "reopen_session()"),
    ),
)


# ---------------------------------------------------------------------------
# 3. Mensagem — três visibilidades
# ---------------------------------------------------------------------------


class MessageVisibility(StrEnum):
    ACTIVE = "ativa"
    ARCHIVED = "arquivada"
    REWOUND = "rebobinada"


MESSAGE = StateMachine(
    name="mensagem",
    source_ref="messages.active/compacted",
    states=frozenset(MessageVisibility),
    initial=MessageVisibility.ACTIVE,
    transitions=(
        T(
            MessageVisibility.ACTIVE,
            MessageVisibility.ARCHIVED,
            "archive_and_compact()",
            "compressão: active=0, compacted=1",
        ),
        T(
            MessageVisibility.ACTIVE,
            MessageVisibility.REWOUND,
            "rewind/undo",
            "active=0, compacted=0",
        ),
        # Arquivada pode voltar: a compactação é reversível por rewind, e é
        # por isso que arquivar nunca é delete.
        T(MessageVisibility.ARCHIVED, MessageVisibility.REWOUND, "rewind sobre trecho compactado"),
    ),
    terminal=frozenset({MessageVisibility.REWOUND}),
)


# ---------------------------------------------------------------------------
# 4. Skill
# ---------------------------------------------------------------------------


class SkillLifecycle(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"
    ABSORBED = "absorvida"


SKILL = StateMachine(
    name="skill",
    source_ref="tools/skill_usage.py::_VALID_STATES + agent/curator.py::apply_automatic_transitions",
    states=frozenset(SkillLifecycle),
    initial=SkillLifecycle.ACTIVE,
    transitions=(
        T(SkillLifecycle.ACTIVE, SkillLifecycle.STALE, "sem uso por get_stale_after_days()"),
        T(SkillLifecycle.STALE, SkillLifecycle.ACTIVE, "usada de novo"),
        T(
            SkillLifecycle.STALE,
            SkillLifecycle.ARCHIVED,
            "sem uso por get_archive_after_days()",
            "proteções: referência de cron, builtin, proveniência do usuário",
        ),
        T(
            SkillLifecycle.ACTIVE,
            SkillLifecycle.ABSORBED,
            "_delete_skill(absorbed_into=X)",
            "consolidação por LLM",
        ),
    ),
    terminal=frozenset({SkillLifecycle.ARCHIVED, SkillLifecycle.ABSORBED}),
)


# ---------------------------------------------------------------------------
# 5. Execução de cron — reusa ExecutionStatus da Tarefa 02
# ---------------------------------------------------------------------------

CRON_EXECUTION = StateMachine(
    name="execucao_de_cron",
    source_ref="cron/executions.py — CHECK(status IN (...))",
    states=frozenset(ExecutionStatus),
    initial=ExecutionStatus.CLAIMED,
    transitions=(
        T(
            ExecutionStatus.CLAIMED,
            ExecutionStatus.RUNNING,
            "mark_execution_running()",
            "EXATAMENTE UMA VEZ",
        ),
        T(ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED, "finish_execution()"),
        T(ExecutionStatus.RUNNING, ExecutionStatus.FAILED, "finish_execution()"),
        T(
            ExecutionStatus.CLAIMED,
            ExecutionStatus.UNKNOWN,
            "recover_interrupted_executions()",
            "dono PROVADO morto: pid + process_started_at",
        ),
        T(
            ExecutionStatus.RUNNING,
            ExecutionStatus.UNKNOWN,
            "recover_interrupted_executions()",
            "dono PROVADO morto",
        ),
    ),
    # Invariante 7: terminais são imutáveis. A primitiva impõe isso ao
    # recusar qualquer transição declarada a partir deles.
    terminal=frozenset(
        {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.UNKNOWN,
        }
    ),
)


# ---------------------------------------------------------------------------
# 6. Job de cron
# ---------------------------------------------------------------------------


class CronJobState(StrEnum):
    SCHEDULED = "scheduled"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


CRON_JOB = StateMachine(
    name="job_de_cron",
    source_ref="campos enabled/state/paused_at do dict de job",
    states=frozenset(CronJobState),
    initial=CronJobState.SCHEDULED,
    transitions=(
        T(CronJobState.SCHEDULED, CronJobState.PAUSED, "pause_job()", "enabled=false + paused_at"),
        T(CronJobState.PAUSED, CronJobState.SCHEDULED, "resume_job()"),
        T(CronJobState.SCHEDULED, CronJobState.SCHEDULED, "disparo recorrente"),
        T(CronJobState.SCHEDULED, CronJobState.COMPLETED, "repeat.completed == repeat.times"),
        T(CronJobState.SCHEDULED, CronJobState.ERROR, "failure_streak alto"),
        T(CronJobState.ERROR, CronJobState.SCHEDULED, "resume_job()"),
    ),
    terminal=frozenset({CronJobState.COMPLETED}),
)


# ---------------------------------------------------------------------------
# 7. Monitor de cron
# ---------------------------------------------------------------------------


class MonitorOutcome(StrEnum):
    FIRST_RUN = "primeira_execucao"
    CHANGED = "mudou"
    UNCHANGED = "inalterado"
    ERROR = "erro"


CRON_MONITOR = StateMachine(
    name="monitor_de_cron",
    source_ref="cron/monitor.py::MonitorOutcome + job['monitor_state']",
    states=frozenset(MonitorOutcome),
    initial=MonitorOutcome.FIRST_RUN,
    transitions=(
        T(MonitorOutcome.FIRST_RUN, MonitorOutcome.CHANGED, "sempre", "first_run=True"),
        T(
            MonitorOutcome.UNCHANGED,
            MonitorOutcome.UNCHANGED,
            "hash == last_output_hash",
            "execução do agente SUPRIMIDA inteiramente",
        ),
        T(MonitorOutcome.UNCHANGED, MonitorOutcome.CHANGED, "hash != last_output_hash"),
        T(MonitorOutcome.CHANGED, MonitorOutcome.UNCHANGED, "próximo tick, hash igual"),
        T(MonitorOutcome.CHANGED, MonitorOutcome.CHANGED, "próximo tick, hash diferente"),
        T(MonitorOutcome.UNCHANGED, MonitorOutcome.ERROR, "fonte falhou", "hash fica INTOCADO"),
        T(MonitorOutcome.CHANGED, MonitorOutcome.ERROR, "fonte falhou", "hash fica INTOCADO"),
        T(MonitorOutcome.ERROR, MonitorOutcome.UNCHANGED, "fonte recuperou com a MESMA saída"),
        T(MonitorOutcome.ERROR, MonitorOutcome.CHANGED, "fonte recuperou com saída diferente"),
    ),
)


# ---------------------------------------------------------------------------
# 8. Delegação assíncrona — DOIS eixos ortogonais
# ---------------------------------------------------------------------------


class DelegationExecution(StrEnum):
    DISPATCHED = "dispatched"
    COMPLETED = "completed"


class DelegationDelivery(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"


DELEGATION_EXECUTION = StateMachine(
    name="delegacao_execucao",
    source_ref="async_delegations.state",
    states=frozenset(DelegationExecution),
    initial=DelegationExecution.DISPATCHED,
    transitions=(
        T(DelegationExecution.DISPATCHED, DelegationExecution.COMPLETED, "resultado chegou"),
    ),
    terminal=frozenset({DelegationExecution.COMPLETED}),
)

DELEGATION_DELIVERY = StateMachine(
    name="delegacao_entrega",
    source_ref="async_delegations.delivery_state",
    states=frozenset(DelegationDelivery),
    initial=DelegationDelivery.PENDING,
    transitions=(
        T(
            DelegationDelivery.PENDING,
            DelegationDelivery.CLAIMED,
            "delivery_claim + delivery_claimed_at",
        ),
        T(DelegationDelivery.CLAIMED, DelegationDelivery.DELIVERED, "delivered_at"),
        T(
            DelegationDelivery.CLAIMED,
            DelegationDelivery.PENDING,
            "claim expirou",
            "delivery_attempts++",
        ),
    ),
    terminal=frozenset({DelegationDelivery.DELIVERED}),
)


# ---------------------------------------------------------------------------
# 9. Handoff de sessão
# ---------------------------------------------------------------------------


class HandoffState(StrEnum):
    REQUESTED = "requested"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


HANDOFF = StateMachine(
    name="handoff",
    source_ref="hermes_state.py — request/claim/complete/fail_handoff",
    states=frozenset(HandoffState),
    initial=HandoffState.REQUESTED,
    transitions=(
        T(HandoffState.REQUESTED, HandoffState.CLAIMED, "claim_handoff()"),
        T(HandoffState.CLAIMED, HandoffState.COMPLETED, "complete_handoff()"),
        T(HandoffState.CLAIMED, HandoffState.FAILED, "fail_handoff()", "grava handoff_error"),
        T(HandoffState.FAILED, HandoffState.REQUESTED, "nova tentativa"),
    ),
    terminal=frozenset({HandoffState.COMPLETED}),
)


# ---------------------------------------------------------------------------
# 10. Aprovação de comando
# ---------------------------------------------------------------------------


class CommandApproval(StrEnum):
    PENDING = "pendente"
    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"
    DENY = "deny"
    #: DIVERGÊNCIA — ver a nota abaixo.
    NEVER = "never"


COMMAND_APPROVAL = StateMachine(
    name="aprovacao_de_comando",
    source_ref="acp_adapter/permissions.py::_OPTION_ID_TO_HERMES + tools/approval.py",
    states=frozenset(CommandApproval),
    initial=CommandApproval.PENDING,
    transitions=(
        T(CommandApproval.PENDING, CommandApproval.ONCE, "allow_once"),
        T(CommandApproval.PENDING, CommandApproval.SESSION, "allow_session"),
        T(
            CommandApproval.PENDING,
            CommandApproval.ALWAYS,
            "allow_always",
            "persiste em command_allowlist",
        ),
        T(CommandApproval.PENDING, CommandApproval.DENY, "deny | timeout | exceção", "FAIL-CLOSED"),
        T(
            CommandApproval.PENDING,
            CommandApproval.NEVER,
            "deny_always",
            "persiste em approvals.deny — DIVERGÊNCIA, ver docs/decisoes.md D-03.1",
        ),
    ),
    terminal=frozenset(
        {
            CommandApproval.ONCE,
            CommandApproval.SESSION,
            CommandApproval.ALWAYS,
            CommandApproval.DENY,
            CommandApproval.NEVER,
        }
    ),
)


# ---------------------------------------------------------------------------
# 11. Aprovação de edição (ACP)
# ---------------------------------------------------------------------------


class EditApproval(StrEnum):
    #: O ponto onde o despacho chega. A spec desenha DOIS pontos de entrada
    #: ([*] --> bypass e [*] --> avaliando); na verdade é um só, seguido de
    #: uma decisão — o ContextVar está ligado? Modelar assim torna a decisão
    #: visível em vez de escondê-la na seta de entrada.
    DISPATCH = "despacho"
    BYPASS = "bypass"
    EVALUATING = "avaliando"
    ALWAYS_ASK = "pergunta_sempre"
    AUTO_APPROVED = "auto_aprovado"
    APPROVED = "aprovado"
    BLOCKED = "bloqueado"


EDIT_APPROVAL = StateMachine(
    name="aprovacao_de_edicao_acp",
    source_ref="acp_adapter/edit_approval.py",
    states=frozenset(EditApproval),
    initial=EditApproval.DISPATCH,
    transitions=(
        # CLI, gateway e cron nunca ligam o ContextVar: para eles a guarda
        # inteira é inexistente, não "permissiva".
        T(
            EditApproval.DISPATCH,
            EditApproval.BYPASS,
            "ContextVar NÃO ligado",
            "CLI, gateway, cron",
        ),
        T(EditApproval.DISPATCH, EditApproval.EVALUATING, "ContextVar ligado (run ACP)"),
        T(
            EditApproval.EVALUATING,
            EditApproval.ALWAYS_ASK,
            "caminho sensível",
            "vale MESMO sob política autônoma",
        ),
        T(EditApproval.EVALUATING, EditApproval.ALWAYS_ASK, "policy=ask"),
        T(
            EditApproval.EVALUATING,
            EditApproval.AUTO_APPROVED,
            "policy=session | workspace_session",
            "DIVERGÊNCIA: só DENTRO do workspace — ver D-03.2",
        ),
        T(
            EditApproval.EVALUATING,
            EditApproval.ALWAYS_ASK,
            "caminho fora do workspace",
            "DIVERGÊNCIA: piso, ver D-03.2",
        ),
        T(EditApproval.ALWAYS_ASK, EditApproval.APPROVED, "usuário permite"),
        T(
            EditApproval.ALWAYS_ASK,
            EditApproval.BLOCKED,
            "usuário nega | timeout | exceção",
            "FAIL-CLOSED",
        ),
    ),
    terminal=frozenset(
        {
            EditApproval.BYPASS,
            EditApproval.AUTO_APPROVED,
            EditApproval.APPROVED,
            EditApproval.BLOCKED,
        }
    ),
)


# ---------------------------------------------------------------------------
# 12. Pareamento de usuário
# ---------------------------------------------------------------------------


class Pairing(StrEnum):
    UNKNOWN = "desconhecido"
    CODE_ISSUED = "codigo_emitido"
    APPROVED = "aprovado"
    EXPIRED = "expirado"
    LOCKED_OUT = "bloqueado"


PAIRING = StateMachine(
    name="pareamento",
    source_ref="gateway/pairing.py",
    states=frozenset(Pairing),
    initial=Pairing.UNKNOWN,
    transitions=(
        T(
            Pairing.UNKNOWN,
            Pairing.CODE_ISSUED,
            "unauthorized_dm_behavior='pair'",
            "8 chars, alfabeto de 32 sem 0/O/1/I, secrets.choice()",
        ),
        T(Pairing.CODE_ISSUED, Pairing.APPROVED, "dono aprova via CLI"),
        T(Pairing.CODE_ISSUED, Pairing.EXPIRED, "1 hora"),
        T(Pairing.CODE_ISSUED, Pairing.LOCKED_OUT, "5 tentativas falhas", "lockout de 1 hora"),
        T(Pairing.EXPIRED, Pairing.UNKNOWN, "volta ao início"),
        T(Pairing.LOCKED_OUT, Pairing.UNKNOWN, "após 1 hora"),
    ),
    terminal=frozenset({Pairing.APPROVED}),
)


# ---------------------------------------------------------------------------
# 13. Drain do gateway
# ---------------------------------------------------------------------------


class Drain(StrEnum):
    SERVING = "servindo"
    DRAINING = "drenando"
    STOPPING = "parando"
    CLEAN_EXIT = "saida_limpa"
    WEDGED = "travado"
    SIGKILL = "sigkill"
    CGROUP_CLEANUP = "cgroup_cleanup"


DRAIN = StateMachine(
    name="drain_do_gateway",
    source_ref="gateway/drain_control.py + _enter/_exit_external_drain",
    states=frozenset(Drain),
    initial=Drain.SERVING,
    transitions=(
        T(
            Drain.SERVING,
            Drain.DRAINING,
            "dashboard escreve .drain_request.json",
            "semântica BASEADA EM PRESENÇA; não há canal HTTP de controle",
        ),
        T(Drain.DRAINING, Drain.SERVING, "marcador REMOVIDO (cancel)"),
        T(Drain.DRAINING, Drain.STOPPING, "turnos concluíram OU restart_drain_timeout"),
        T(Drain.STOPPING, Drain.CLEAN_EXIT, "shutdown_flush OK"),
        T(Drain.STOPPING, Drain.WEDGED, "loop asyncio congelado"),
        # O watchdog é thread de OS PURA porque, com o loop congelado, todo
        # caminho de recuperação baseado em asyncio é estruturalmente incapaz
        # de disparar.
        T(
            Drain.WEDGED,
            Drain.SIGKILL,
            "watchdog (thread de OS pura)",
            "dump de stacks via faulthandler",
        ),
        T(Drain.SIGKILL, Drain.CGROUP_CLEANUP, "ExecStopPost="),
    ),
    terminal=frozenset({Drain.CLEAN_EXIT, Drain.CGROUP_CLEANUP}),
)


# ---------------------------------------------------------------------------
# 14. Fase de compactação
# ---------------------------------------------------------------------------


class CompactionPhase(StrEnum):
    FREE = "livre"
    COMPRESSING = "comprimindo"
    INEFFECTIVE = "ineficaz"
    FAILED = "falhou"
    COOLDOWN = "cooldown"
    BLOCKED = "bloqueado"


COMPACTION = StateMachine(
    name="fase_de_compactacao",
    source_ref="colunas compression_* de sessions + compression_locks",
    states=frozenset(CompactionPhase),
    initial=CompactionPhase.FREE,
    transitions=(
        T(
            CompactionPhase.FREE,
            CompactionPhase.COMPRESSING,
            "try_acquire_compression_lock()",
            "holder + expires_at",
        ),
        T(CompactionPhase.COMPRESSING, CompactionPhase.FREE, "release_compression_lock()"),
        T(
            CompactionPhase.COMPRESSING,
            CompactionPhase.INEFFECTIVE,
            "compression_made_progress() == False",
        ),
        T(
            CompactionPhase.INEFFECTIVE,
            CompactionPhase.FREE,
            "_record_ineffective_compression_verdict()",
            "compression_ineffective_count++",
        ),
        T(CompactionPhase.COMPRESSING, CompactionPhase.FAILED, "timeout / erro do sumarizador"),
        T(
            CompactionPhase.FAILED,
            CompactionPhase.COOLDOWN,
            "record_timeout_failure()",
            "escada de cooldown",
        ),
        T(CompactionPhase.COOLDOWN, CompactionPhase.FREE, "cooldown expirou"),
        T(CompactionPhase.INEFFECTIVE, CompactionPhase.BLOCKED, "strikes suficientes"),
        # O contador é DURÁVEL: sobrevive a reinícios, então o breaker não é
        # zerado por um restart oportuno.
        T(
            CompactionPhase.BLOCKED,
            CompactionPhase.FREE,
            "_refresh_durable_guards()",
            "relê estado durável do banco",
        ),
    ),
)


MACHINES: tuple[StateMachine, ...] = (
    SESSION,
    MESSAGE,
    SKILL,
    CRON_EXECUTION,
    CRON_JOB,
    CRON_MONITOR,
    DELEGATION_EXECUTION,
    DELEGATION_DELIVERY,
    HANDOFF,
    COMMAND_APPROVAL,
    EDIT_APPROVAL,
    PAIRING,
    DRAIN,
    COMPACTION,
)


def machine(name: str) -> StateMachine:
    for m in MACHINES:
        if m.name == name:
            return m
    raise KeyError(f"máquina desconhecida: {name!r}")


#: 13 **entidades** com estado (§15), 14 objetos de máquina: a delegação
#: assíncrona tem dois eixos ortogonais e vira duas máquinas. A linhagem de
#: sessão (§2) não entra: é classificação de parentesco, não ciclo de vida,
#: e vive em ``kairos_domain.session.Lineage``.
STATEFUL_ENTITY_COUNT = 13


# ---------------------------------------------------------------------------
# Lógica derivada
# ---------------------------------------------------------------------------


class SessionStatus(StrEnum):
    """Status **derivado da forma da última mensagem** — não é coluna."""

    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    EMPTY = "empty"


#: `finish_reason` que caracteriza erro.
ERROR_FINISH_REASONS = frozenset({"error", "agent_error", "content_filter"})


def classify_session_status(
    last_role: str | None,
    *,
    has_pending_tool_calls: bool = False,
    finish_reason: str | None = None,
) -> SessionStatus:
    """Deriva o status da sessão da forma da última linha.

    O default para forma **desconhecida** é deliberadamente benigno
    (``COMPLETE``), e a razão está na spec: *"pickers must not alarm on
    unknown shapes"*. Uma sessão de formato novo não deve aparecer marcada
    como quebrada só porque o classificador não a reconhece — o custo de um
    falso alarme recorrente na lista é maior que o de um status otimista.
    """
    if last_role is None:
        return SessionStatus.EMPTY

    if last_role == "assistant":
        if has_pending_tool_calls:
            # Nenhuma linha de resultado seguiu: o turno morreu no meio.
            return SessionStatus.INTERRUPTED
        if finish_reason in ERROR_FINISH_REASONS:
            return SessionStatus.ERROR
        return SessionStatus.COMPLETE

    if last_role == "user":
        return SessionStatus.INTERRUPTED  # o agente nunca respondeu
    if last_role == "tool":
        return SessionStatus.INTERRUPTED  # o resultado nunca foi consumido

    return SessionStatus.COMPLETE  # default benigno


def monitor_outcome(
    current_hash: str,
    previous_hash: str | None,
    *,
    source_failed: bool = False,
) -> MonitorOutcome:
    """Desfecho de um tick de monitor.

    Duas regras que não são intuitivas e existem por motivo:

    * **Erro ≠ mudança.** Se a fonte falhou, o desfecho é ``ERROR`` e o hash
      guardado fica **intocado** — de modo que uma fonte que se recupera para
      a saída anterior ainda suprime. Tratar erro como mudança produziria um
      alerta falso exatamente quando o sistema observado está indisponível.
    * **Primeira execução sempre notifica.** Sem hash anterior não há
      comparação possível, e silenciar seria esconder a primeira observação.
    """
    if source_failed:
        return MonitorOutcome.ERROR
    if previous_hash is None:
        return MonitorOutcome.CHANGED
    # Comparação por bytes exatos: sem stripping de timestamp, sem
    # normalização de whitespace. Normalizar seria adivinhar o que o usuário
    # considera ruído.
    return MonitorOutcome.UNCHANGED if current_hash == previous_hash else MonitorOutcome.CHANGED


def next_monitor_hash(
    current_hash: str,
    previous_hash: str | None,
    *,
    source_failed: bool = False,
) -> str | None:
    """O hash a persistir depois do tick. Falha da fonte **não** o altera."""
    return previous_hash if source_failed else current_hash
