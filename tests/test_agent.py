"""Tarefa 13 — agent.

Critério de pronto: "os **4 gatilhos** de compactação existem e **nenhum
automático dispara** com `compression.enabled: false` — inclusive os
reativos, com isenção explícita para erro de teto de saída."
"""

from __future__ import annotations

import unittest

from kairos_agent.auxiliary import (
    TEXT_CHAIN,
    VISION_CHAIN,
    AuxiliaryConfig,
    AuxiliaryTask,
    Backend,
    resolve_backend,
)
from kairos_agent.compaction import (
    OVERFLOW_REASONS,
    CompactionTrigger,
    FailoverReason,
    compaction_trigger,
    is_output_cap_error,
)
from kairos_agent.loop import (
    ExitReason,
    IterationBudget,
    LoopLimits,
    TurnOutcome,
    should_continue,
)
from kairos_agent.micro_compaction import MicroCompactionConfig, should_micro_compact


class LacoTests(unittest.TestCase):
    def test_sao_12_razoes_de_saida_ANTECIPADA(self):
        self.assertEqual(len(ExitReason), 12)

    def test_a_saida_normal_NAO_recebe_rotulo(self):
        """O campo existe para explicar por que um turno terminou ANTES do
        esperado; rotular o caminho feliz diluiria o sinal."""
        normal = TurnOutcome(completed=True, api_calls=3)
        self.assertIsNone(normal.exit_reason)
        self.assertFalse(normal.ended_early)

    def test_os_DOIS_limites_sao_independentes(self):
        # Teto de iterações esgota primeiro.
        lim = LoopLimits(max_iterations=2, budget=IterationBudget(max_total=99))
        self.assertTrue(should_continue(lim, 1))
        self.assertFalse(should_continue(lim, 2))

        # Orçamento esgota primeiro.
        lim = LoopLimits(max_iterations=99, budget=IterationBudget(max_total=2, used=2))
        self.assertFalse(should_continue(lim, 0))

    def test_o_grace_call_da_UMA_ultima_chance(self):
        lim = LoopLimits(budget=IterationBudget(max_total=1, used=1))
        self.assertFalse(should_continue(lim, 0))
        lim.offer_grace()
        self.assertTrue(should_continue(lim, 0))
        # A flag é consumida no início da iteração: o laço sai depois dela.
        lim.grace_call = False
        self.assertFalse(should_continue(lim, 0))

    def test_o_estorno_existe_para_caminho_que_nao_tocou_o_provedor(self):
        """Preflight abortado ou compressão adiada gastariam orçamento sem
        gastar chamada, e o turno morreria antes de fazer o trabalho."""
        b = IterationBudget(max_total=3)
        self.assertTrue(b.consume())
        self.assertEqual(b.remaining, 2)
        b.refund()
        self.assertEqual(b.remaining, 3)

    def test_o_estorno_nao_fica_negativo(self):
        b = IterationBudget(max_total=3)
        b.refund()
        b.refund()
        self.assertEqual(b.used, 0)

    def test_orcamento_esgotado_recusa_consumo(self):
        b = IterationBudget(max_total=1)
        self.assertTrue(b.consume())
        self.assertFalse(b.consume())


class CompactacaoTests(unittest.TestCase):
    """A invariante que amarra os quatro gatilhos."""

    def test_os_quatro_gatilhos(self):
        self.assertEqual(
            {t.value for t in CompactionTrigger} - {"none"},
            {"proactive_preflight", "post_response", "reactive_overflow", "forced"},
        )

    def test_ligada_o_limiar_proativo_dispara(self):
        d = compaction_trigger(compression_enabled=True, over_threshold=True)
        self.assertTrue(d.allowed)
        self.assertEqual(d.trigger, CompactionTrigger.PROACTIVE_PREFLIGHT)

    def test_ligada_o_reativo_dispara_nos_TRES_estouros(self):
        for reason in OVERFLOW_REASONS:
            with self.subTest(reason=reason):
                d = compaction_trigger(compression_enabled=True, failover=reason)
                self.assertTrue(d.allowed)
                self.assertEqual(d.trigger, CompactionTrigger.REACTIVE_OVERFLOW)

    def test_DESLIGADA_nem_o_reativo_comprime(self):
        """A regressão de anomalyco/opencode#30749: antes, um erro de estouro
        do provedor comprimia e rotacionava a sessão EM SILÊNCIO, por cima da
        escolha explícita do usuário."""
        for reason in OVERFLOW_REASONS:
            with self.subTest(reason=reason):
                d = compaction_trigger(compression_enabled=False, failover=reason)
                self.assertFalse(d.allowed)
                self.assertIn("/compress", d.reason, "a mensagem tem de dar a saída ao usuário")

    def test_desligada_o_proativo_tambem_nao_dispara(self):
        d = compaction_trigger(compression_enabled=False, over_threshold=True)
        self.assertFalse(d.allowed)

    def test_a_forcada_NAO_e_limitada_pela_configuracao(self):
        """`/compress` é ação do usuário: a configuração não a limita."""
        d = compaction_trigger(compression_enabled=False, forced=True)
        self.assertTrue(d.allowed)
        self.assertEqual(d.trigger, CompactionTrigger.FORCED)

    def test_erro_de_teto_de_SAIDA_e_isento(self):
        """Não é estouro de entrada: a recuperação é retry de max_tokens, que
        não exige compressão — e dispara mesmo com a compressão desligada."""
        self.assertTrue(is_output_cap_error(FailoverReason.OUTPUT_CAP))
        self.assertNotIn(FailoverReason.OUTPUT_CAP, OVERFLOW_REASONS)
        d = compaction_trigger(compression_enabled=False, failover=FailoverReason.OUTPUT_CAP)
        self.assertIn("retry de max_tokens", d.reason)

    def test_erro_sem_relacao_nao_dispara_compactacao(self):
        d = compaction_trigger(compression_enabled=True, failover=FailoverReason.AUTH)
        self.assertFalse(d.allowed)


class AuxiliarTests(unittest.TestCase):
    def test_a_cadeia_de_texto_tem_7_degraus(self):
        self.assertEqual(len(TEXT_CHAIN), 7)

    def test_a_cadeia_de_visao_tem_6_degraus(self):
        self.assertEqual(len(VISION_CHAIN), 6)

    def test_o_endpoint_custom_vem_DEPOIS_do_anthropic_na_visao(self):
        """Reservado a modelos locais (Qwen-VL, LLaVA, Pixtral)."""
        self.assertGreater(
            VISION_CHAIN.index(Backend.CUSTOM_ENDPOINT),
            VISION_CHAIN.index(Backend.NATIVE_ANTHROPIC),
        )
        # Na cadeia de texto a ordem é a inversa.
        self.assertLess(
            TEXT_CHAIN.index(Backend.CUSTOM_ENDPOINT),
            TEXT_CHAIN.index(Backend.NATIVE_ANTHROPIC),
        )

    def test_resolve_o_primeiro_disponivel(self):
        cfg = AuxiliaryConfig(available={Backend.OPENROUTER, Backend.NOUS_PORTAL})
        self.assertEqual(resolve_backend(AuxiliaryTask.SESSION_SEARCH, cfg), Backend.OPENROUTER)

    def test_o_principal_do_usuario_vem_primeiro(self):
        cfg = AuxiliaryConfig(available={Backend.MAIN, Backend.OPENROUTER})
        self.assertEqual(resolve_backend(AuxiliaryTask.WEB_EXTRACTION, cfg), Backend.MAIN)

    def test_sem_backend_nenhum_devolve_NONE_e_nao_levanta(self):
        self.assertEqual(
            resolve_backend(AuxiliaryTask.SESSION_SEARCH, AuxiliaryConfig()), Backend.NONE
        )

    def test_override_por_tarefa_vence_a_cadeia(self):
        cfg = AuxiliaryConfig(
            available={Backend.MAIN},
            per_task={AuxiliaryTask.VISION_ANALYSIS: Backend.CUSTOM_ENDPOINT},
        )
        self.assertEqual(
            resolve_backend(AuxiliaryTask.VISION_ANALYSIS, cfg), Backend.CUSTOM_ENDPOINT
        )

    def test_tarefa_de_visao_usa_a_cadeia_de_visao(self):
        cfg = AuxiliaryConfig(available={Backend.NATIVE_ANTHROPIC, Backend.CUSTOM_ENDPOINT})
        self.assertEqual(
            resolve_backend(AuxiliaryTask.BROWSER_VISION, cfg), Backend.NATIVE_ANTHROPIC
        )

    def test_os_cinco_consumidores(self):
        self.assertEqual(len(AuxiliaryTask), 5)

    def test_a_guarda_de_custo_existe_e_o_default_e_seguro(self):
        """Fallback silencioso para modelo pago é a falha que mais surpreende:
        o usuário não pediu, não viu, e paga."""
        self.assertFalse(AuxiliaryConfig().free_only)
        self.assertTrue(AuxiliaryConfig(free_only=True).free_only)


class MicroCompactacaoTests(unittest.TestCase):
    def test_o_default_e_DESLIGADO(self):
        """A passada reescreve histórico já enviado e quebra o prompt-cache a
        cada turno, em vez de numa fronteira episódica."""
        self.assertFalse(MicroCompactionConfig().enabled)

    def test_os_defaults_dos_dials(self):
        c = MicroCompactionConfig()
        self.assertEqual((c.every_n_turns, c.defrag_threshold_tokens), (1, 2000))

    def test_cadencia_zero_e_recusada(self):
        with self.assertRaises(ValueError) as ctx:
            MicroCompactionConfig(every_n_turns=0)
        self.assertIn("continuamente", str(ctx.exception))

    def test_limiar_invalido_e_recusado(self):
        with self.assertRaises(ValueError):
            MicroCompactionConfig(defrag_threshold_tokens=0)

    def test_desligada_nunca_compacta(self):
        c = MicroCompactionConfig(enabled=False)
        self.assertFalse(should_micro_compact(c, 100))

    def test_a_cadencia_e_respeitada(self):
        c = MicroCompactionConfig(enabled=True, every_n_turns=5)
        self.assertFalse(should_micro_compact(c, 4))
        self.assertTrue(should_micro_compact(c, 5))
        self.assertTrue(should_micro_compact(c, 10))

    def test_turno_zero_nao_dispara(self):
        self.assertFalse(should_micro_compact(MicroCompactionConfig(enabled=True), 0))


if __name__ == "__main__":
    unittest.main()
