"""Tarefa 03 — Máquinas de Estado.

Critério de pronto: "Todos os estados e transições documentados estão
implementados e transição não documentada é rejeitada explicitamente."

A segunda metade é a que importa. Cada máquina tem um teste que tenta uma
transição **ausente da spec** e exige recusa.
"""

from __future__ import annotations

import unittest

from kairos_domain.machines import (
    COMMAND_APPROVAL,
    COMPACTION,
    CRON_EXECUTION,
    CRON_JOB,
    CRON_MONITOR,
    DELEGATION_DELIVERY,
    DELEGATION_EXECUTION,
    DRAIN,
    EDIT_APPROVAL,
    HANDOFF,
    MACHINES,
    MESSAGE,
    PAIRING,
    SESSION,
    SKILL,
    STATEFUL_ENTITY_COUNT,
    CommandApproval,
    CompactionPhase,
    CronJobState,
    DelegationDelivery,
    Drain,
    EditApproval,
    HandoffState,
    MessageVisibility,
    MonitorOutcome,
    Pairing,
    SessionState,
    SessionStatus,
    SkillLifecycle,
    classify_session_status,
    machine,
    monitor_outcome,
    next_monitor_hash,
)
from kairos_domain.scheduling import ExecutionStatus
from kairos_domain.statemachine import (
    IllegalTransition,
    StateMachine,
    Transition,
    UnknownState,
    validate,
)


class PrimitiveTests(unittest.TestCase):
    def test_transicao_citando_estado_nao_declarado_e_recusada(self):
        with self.assertRaises(UnknownState):
            StateMachine(
                name="m", states=frozenset({"a"}), initial="a",
                transitions=(Transition("a", "fantasma", "x"),),
            )

    def test_estado_inicial_precisa_existir(self):
        with self.assertRaises(UnknownState):
            StateMachine(name="m", states=frozenset({"a"}), initial="z", transitions=())

    def test_terminal_com_saida_declarada_e_recusado(self):
        with self.assertRaises(IllegalTransition):
            StateMachine(
                name="m", states=frozenset({"a", "b"}), initial="a",
                transitions=(Transition("a", "b", "x"), Transition("b", "a", "volta")),
                terminal=frozenset({"b"}),
            )

    def test_estado_inalcancavel_e_detectado(self):
        m = StateMachine(
            name="m", states=frozenset({"a", "b", "orfao"}), initial="a",
            transitions=(Transition("a", "b", "x"),), terminal=frozenset({"b"}),
        )
        self.assertEqual(m.unreachable(), frozenset({"orfao"}))
        with self.assertRaises(UnknownState):
            validate([m])

    def test_beco_sem_saida_nao_declarado_terminal_e_detectado(self):
        m = StateMachine(
            name="m", states=frozenset({"a", "b"}), initial="a",
            transitions=(Transition("a", "b", "x"),),
        )
        self.assertEqual(m.dead_ends(), frozenset({"b"}))
        with self.assertRaises(IllegalTransition):
            validate([m])

    def test_mensagem_de_erro_lista_os_alvos_validos(self):
        with self.assertRaises(IllegalTransition) as ctx:
            SESSION.transition(SessionState.SUSPENDED, SessionState.ENDED)
        self.assertIn("ativa", str(ctx.exception))


class TodasAsMaquinasTests(unittest.TestCase):
    def test_as_13_entidades_com_estado(self):
        # 13 entidades, 14 objetos: a delegação tem dois eixos ortogonais.
        self.assertEqual(STATEFUL_ENTITY_COUNT, 13)
        self.assertEqual(len(MACHINES), 14)

    def test_todas_bem_formadas(self):
        validate(MACHINES)

    def test_nomes_unicos(self):
        nomes = [m.name for m in MACHINES]
        self.assertEqual(len(nomes), len(set(nomes)))

    def test_toda_maquina_cita_a_fonte(self):
        for m in MACHINES:
            with self.subTest(machine=m.name):
                self.assertTrue(m.source_ref, f"{m.name} sem source_ref")

    def test_lookup_por_nome(self):
        self.assertIs(machine("sessao"), SESSION)
        with self.assertRaises(KeyError):
            machine("inexistente")


class SessionMachineTests(unittest.TestCase):
    def test_encerrada_pode_reabrir(self):
        # Regra 7 de compactação: UM id para a vida. Encerrada não é terminal.
        SESSION.transition(SessionState.ENDED, SessionState.ACTIVE)
        self.assertNotIn(SessionState.ENDED, SESSION.terminal)

    def test_suspensa_nao_encerra_direto(self):
        with self.assertRaises(IllegalTransition):
            SESSION.transition(SessionState.SUSPENDED, SessionState.ENDED)

    def test_resume_pending_nao_suspende(self):
        with self.assertRaises(IllegalTransition):
            SESSION.transition(SessionState.RESUME_PENDING, SessionState.SUSPENDED)


class SessionStatusTests(unittest.TestCase):
    def test_assistente_com_tool_call_pendente_e_interrompida(self):
        self.assertEqual(
            classify_session_status("assistant", has_pending_tool_calls=True),
            SessionStatus.INTERRUPTED,
        )

    def test_finish_reason_de_erro(self):
        for fr in ("error", "agent_error", "content_filter"):
            with self.subTest(finish_reason=fr):
                self.assertEqual(
                    classify_session_status("assistant", finish_reason=fr), SessionStatus.ERROR
                )

    def test_usuario_e_tool_por_ultimo_sao_interrompidas(self):
        self.assertEqual(classify_session_status("user"), SessionStatus.INTERRUPTED)
        self.assertEqual(classify_session_status("tool"), SessionStatus.INTERRUPTED)

    def test_forma_desconhecida_tem_default_benigno(self):
        # "pickers must not alarm on unknown shapes"
        self.assertEqual(classify_session_status("forma_nova"), SessionStatus.COMPLETE)

    def test_sessao_vazia(self):
        self.assertEqual(classify_session_status(None), SessionStatus.EMPTY)


class MessageMachineTests(unittest.TestCase):
    def test_arquivada_nao_volta_a_ativa(self):
        # Reativar uma mensagem compactada recriaria contexto que o modelo já
        # não tem — e quebraria o prefixo em cache.
        with self.assertRaises(IllegalTransition):
            MESSAGE.transition(MessageVisibility.ARCHIVED, MessageVisibility.ACTIVE)

    def test_rebobinada_e_terminal(self):
        self.assertIn(MessageVisibility.REWOUND, MESSAGE.terminal)


class SkillMachineTests(unittest.TestCase):
    def test_archived_e_absorbed_sao_terminais(self):
        self.assertEqual(
            SKILL.terminal, frozenset({SkillLifecycle.ARCHIVED, SkillLifecycle.ABSORBED})
        )

    def test_stale_volta_a_active_quando_usada(self):
        SKILL.transition(SkillLifecycle.STALE, SkillLifecycle.ACTIVE)

    def test_active_nao_arquiva_direto(self):
        # Precisa passar por stale: arquivar algo em uso ativo seria perda.
        with self.assertRaises(IllegalTransition):
            SKILL.transition(SkillLifecycle.ACTIVE, SkillLifecycle.ARCHIVED)

    def test_archived_nao_ressuscita(self):
        with self.assertRaises(IllegalTransition):
            SKILL.transition(SkillLifecycle.ARCHIVED, SkillLifecycle.ACTIVE)


class CronExecutionTests(unittest.TestCase):
    def test_inv7_terminais_sao_imutaveis(self):
        for t in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.UNKNOWN):
            with self.subTest(status=t):
                with self.assertRaises(IllegalTransition):
                    CRON_EXECUTION.transition(t, ExecutionStatus.RUNNING)

    def test_claimed_pode_ir_direto_a_unknown(self):
        # O dono pode morrer entre reivindicar e começar.
        CRON_EXECUTION.transition(ExecutionStatus.CLAIMED, ExecutionStatus.UNKNOWN)

    def test_claimed_nao_completa_sem_rodar(self):
        with self.assertRaises(IllegalTransition):
            CRON_EXECUTION.transition(ExecutionStatus.CLAIMED, ExecutionStatus.COMPLETED)

    def test_o_ledger_nao_e_fila_de_retry(self):
        # Nada volta de um terminal para claimed.
        for t in CRON_EXECUTION.terminal:
            with self.subTest(status=t):
                self.assertEqual(CRON_EXECUTION.targets_from(t), frozenset())


class CronJobTests(unittest.TestCase):
    def test_completed_e_terminal(self):
        self.assertEqual(CRON_JOB.terminal, frozenset({CronJobState.COMPLETED}))

    def test_error_pode_ser_retomado(self):
        CRON_JOB.transition(CronJobState.ERROR, CronJobState.SCHEDULED)

    def test_paused_nao_dispara(self):
        with self.assertRaises(IllegalTransition):
            CRON_JOB.transition(CronJobState.PAUSED, CronJobState.COMPLETED)


class MonitorTests(unittest.TestCase):
    def test_erro_nao_e_mudanca_e_nao_toca_o_hash(self):
        self.assertEqual(
            monitor_outcome("novo", "antigo", source_failed=True), MonitorOutcome.ERROR
        )
        self.assertEqual(next_monitor_hash("novo", "antigo", source_failed=True), "antigo")

    def test_fonte_que_recupera_para_a_saida_anterior_ainda_suprime(self):
        # Consequência direta de o hash ficar intocado no erro.
        depois_do_erro = next_monitor_hash("lixo", "antigo", source_failed=True)
        self.assertEqual(monitor_outcome("antigo", depois_do_erro), MonitorOutcome.UNCHANGED)

    def test_primeira_execucao_sempre_notifica(self):
        self.assertEqual(monitor_outcome("x", None), MonitorOutcome.CHANGED)

    def test_comparacao_e_por_bytes_exatos(self):
        # Sem normalização de whitespace: normalizar seria adivinhar o que o
        # usuário considera ruído.
        self.assertEqual(monitor_outcome("a b", "a  b"), MonitorOutcome.CHANGED)

    def test_primeira_execucao_nao_e_alcancada_de_volta(self):
        self.assertEqual(CRON_MONITOR.targets_from(MonitorOutcome.FIRST_RUN),
                         frozenset({MonitorOutcome.CHANGED}))


class DelegationTests(unittest.TestCase):
    def test_os_dois_eixos_sao_ortogonais(self):
        # Uma delegação pode estar completed na execução e pending na entrega
        # — exatamente o gap que o ledger de obrigação cobre.
        self.assertTrue(DELEGATION_EXECUTION.states.isdisjoint(
            {DelegationDelivery.CLAIMED, DelegationDelivery.DELIVERED}
        ))

    def test_claim_expirado_volta_a_pending(self):
        DELEGATION_DELIVERY.transition(DelegationDelivery.CLAIMED, DelegationDelivery.PENDING)

    def test_pending_nao_entrega_sem_claim(self):
        with self.assertRaises(IllegalTransition):
            DELEGATION_DELIVERY.transition(DelegationDelivery.PENDING, DelegationDelivery.DELIVERED)


class HandoffTests(unittest.TestCase):
    def test_falhou_pode_tentar_de_novo(self):
        HANDOFF.transition(HandoffState.FAILED, HandoffState.REQUESTED)

    def test_requested_nao_completa_sem_claim(self):
        with self.assertRaises(IllegalTransition):
            HANDOFF.transition(HandoffState.REQUESTED, HandoffState.COMPLETED)

    def test_completed_e_terminal(self):
        self.assertEqual(HANDOFF.targets_from(HandoffState.COMPLETED), frozenset())


class CommandApprovalTests(unittest.TestCase):
    def test_never_existe_e_e_distinto_de_deny(self):
        """DIVERGÊNCIA D-03.1 — a spec afirma que não há negação permanente."""
        COMMAND_APPROVAL.transition(CommandApproval.PENDING, CommandApproval.NEVER)
        self.assertNotEqual(CommandApproval.NEVER, CommandApproval.DENY)

    def test_todos_os_desfechos_sao_terminais(self):
        self.assertEqual(
            COMMAND_APPROVAL.targets_from(CommandApproval.PENDING),
            frozenset({CommandApproval.ONCE, CommandApproval.SESSION, CommandApproval.ALWAYS,
                       CommandApproval.DENY, CommandApproval.NEVER}),
        )

    def test_um_desfecho_nao_vira_outro(self):
        with self.assertRaises(IllegalTransition):
            COMMAND_APPROVAL.transition(CommandApproval.DENY, CommandApproval.ALWAYS)


class EditApprovalTests(unittest.TestCase):
    def test_o_despacho_e_uma_decisao_explicita(self):
        # A spec desenha dois pontos de entrada; é um só, seguido da pergunta
        # "o ContextVar está ligado?".
        self.assertEqual(
            EDIT_APPROVAL.targets_from(EditApproval.DISPATCH),
            frozenset({EditApproval.BYPASS, EditApproval.EVALUATING}),
        )

    def test_bloqueado_e_terminal_fail_closed(self):
        self.assertEqual(EDIT_APPROVAL.targets_from(EditApproval.BLOCKED), frozenset())

    def test_caminho_sensivel_nao_pode_ser_auto_aprovado(self):
        with self.assertRaises(IllegalTransition):
            EDIT_APPROVAL.transition(EditApproval.ALWAYS_ASK, EditApproval.AUTO_APPROVED)


class PairingTests(unittest.TestCase):
    def test_expirado_e_bloqueado_voltam_ao_desconhecido(self):
        PAIRING.transition(Pairing.EXPIRED, Pairing.UNKNOWN)
        PAIRING.transition(Pairing.LOCKED_OUT, Pairing.UNKNOWN)

    def test_bloqueado_nao_aprova(self):
        with self.assertRaises(IllegalTransition):
            PAIRING.transition(Pairing.LOCKED_OUT, Pairing.APPROVED)

    def test_desconhecido_nao_aprova_sem_codigo(self):
        with self.assertRaises(IllegalTransition):
            PAIRING.transition(Pairing.UNKNOWN, Pairing.APPROVED)


class DrainTests(unittest.TestCase):
    def test_drenando_pode_ser_cancelado(self):
        # Semântica baseada em presença: remover o marcador cancela.
        DRAIN.transition(Drain.DRAINING, Drain.SERVING)

    def test_travado_so_sai_por_sigkill(self):
        # Com o loop asyncio congelado, todo caminho de recuperação baseado
        # em asyncio é estruturalmente incapaz de disparar.
        self.assertEqual(DRAIN.targets_from(Drain.WEDGED), frozenset({Drain.SIGKILL}))

    def test_travado_nao_volta_a_servir(self):
        with self.assertRaises(IllegalTransition):
            DRAIN.transition(Drain.WEDGED, Drain.SERVING)

    def test_parando_nao_cancela(self):
        with self.assertRaises(IllegalTransition):
            DRAIN.transition(Drain.STOPPING, Drain.SERVING)


class CompactionPhaseTests(unittest.TestCase):
    def test_bloqueado_so_sai_relendo_estado_duravel(self):
        # O contador sobrevive a reinícios: o breaker não é zerado por um
        # restart oportuno.
        t = COMPACTION.transition(CompactionPhase.BLOCKED, CompactionPhase.FREE)
        self.assertIn("durável", t.guard or "")

    def test_falhou_passa_obrigatoriamente_por_cooldown(self):
        self.assertEqual(
            COMPACTION.targets_from(CompactionPhase.FAILED), frozenset({CompactionPhase.COOLDOWN})
        )
        with self.assertRaises(IllegalTransition):
            COMPACTION.transition(CompactionPhase.FAILED, CompactionPhase.FREE)

    def test_cooldown_nao_comprime(self):
        with self.assertRaises(IllegalTransition):
            COMPACTION.transition(CompactionPhase.COOLDOWN, CompactionPhase.COMPRESSING)


if __name__ == "__main__":
    unittest.main()
