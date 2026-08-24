"""Tarefa 11 — providers-gateway.

Critério de pronto: "o lease é por **identidade durável**, não por chave de
sessão (ADR 004 — a regra mais transferível do projeto)."
"""

from __future__ import annotations

import dataclasses
import unittest

from kairos_gateway.capability import (
    CapabilityDescriptor,
    MarkdownDialect,
    Operation,
    WakeupMode,
)
from kairos_gateway.delivery import (
    COOLDOWN_LADDER,
    DeadTargets,
    DeliveryLedger,
    DeliveryState,
    FailureKind,
)
from kairos_gateway.stream_events import (
    STREAM_EVENTS,
    Commentary,
    GatewayNotice,
    LongToolHint,
    MessageChunk,
    MessageStop,
    ToolCallChunk,
    ToolCallFinished,
)
from kairos_providers import (
    OMIT_TEMPERATURE,
    DiscoveryLayer,
    ProviderProfile,
    ProviderRegistry,
    build_request_kwargs,
    user_agent,
)


def perfil(**kw) -> ProviderProfile:
    base = {
        "name": "p",
        "display_name": "P",
        "base_url": "https://api",
        "default_model": "m",
    }
    return ProviderProfile(**{**base, **kw})


class ProviderTests(unittest.TestCase):
    def test_OMIT_TEMPERATURE_remove_a_chave_do_corpo(self):
        """Modelos de raciocínio REJEITAM o parâmetro: ele precisa sumir, não
        ir com valor."""
        kw = build_request_kwargs(perfil(default_temperature=OMIT_TEMPERATURE))
        self.assertNotIn("temperature", kw)

    def test_a_sentinela_e_distinguivel_de_None(self):
        """`temperature=None` é valor legítimo para alguns provedores: um
        `is None` não distinguiria 'não definido' de 'explicitamente nulo'."""
        self.assertIsNot(OMIT_TEMPERATURE, None)
        self.assertFalse(bool(OMIT_TEMPERATURE))
        kw = build_request_kwargs(perfil(default_temperature=None))
        self.assertNotIn("temperature", kw)
        kw = build_request_kwargs(perfil(default_temperature=0.7))
        self.assertEqual(kw["temperature"], 0.7)

    def test_override_explicito_vence_o_default_do_perfil(self):
        kw = build_request_kwargs(perfil(default_temperature=0.7), temperature=0.1)
        self.assertEqual(kw["temperature"], 0.1)

    def test_override_com_a_sentinela_tambem_omite(self):
        kw = build_request_kwargs(perfil(default_temperature=0.7), temperature=OMIT_TEMPERATURE)
        self.assertNotIn("temperature", kw)

    def test_user_agent_e_obrigatorio_em_toda_requisicao(self):
        """Não é telemetria: WAFs de provedor devolvem 403 para o UA padrão do
        urllib, e o erro não diz nada sobre o motivo."""
        kw = build_request_kwargs(perfil())
        self.assertEqual(kw["headers"]["User-Agent"], user_agent())
        self.assertTrue(user_agent().startswith("kairos/"))

    def test_headers_do_perfil_sao_preservados(self):
        kw = build_request_kwargs(perfil(headers={"X-Custom": "v"}))
        self.assertEqual(kw["headers"]["X-Custom"], "v")
        self.assertIn("User-Agent", kw["headers"])

    def test_precedencia_de_camada_vence_ordem_de_registro(self):
        """Sem a ordem por camada, a precedência dependeria da ordem de
        importação — que muda com o filesystem e é indepurável."""
        r = ProviderRegistry()
        r.register(perfil(name="x", display_name="usuário", layer=DiscoveryLayer.USER_PLUGIN))
        r.register(perfil(name="x", display_name="builtin", layer=DiscoveryLayer.BUILTIN))
        self.assertEqual(r.get("x").display_name, "usuário", "camada alta não é sobrescrita")

    def test_last_writer_wins_DENTRO_da_mesma_camada(self):
        r = ProviderRegistry()
        r.register(perfil(name="x", display_name="primeiro"))
        r.register(perfil(name="x", display_name="segundo"))
        self.assertEqual(r.get("x").display_name, "segundo")

    def test_namespace_de_plugin_de_usuario_e_isolado(self):
        """Dois perfis com plugin homônimo colidiriam na tabela de módulos, e
        o segundo reusaria o código do primeiro em silêncio."""
        self.assertEqual(ProviderRegistry.user_module_name("meu"), "_kairos_user_provider_meu")

    def test_perfil_e_imutavel(self):
        # Exceção específica: com `Exception` genérico o teste passaria até
        # por AttributeError de nome errado, e deixaria de significar
        # "o perfil é congelado".
        with self.assertRaises(dataclasses.FrozenInstanceError):
            perfil().name = "outro"


class StreamEventTests(unittest.TestCase):
    def test_sao_exatamente_7_eventos(self):
        self.assertEqual(len(STREAM_EVENTS), 7)

    def test_os_nomes_da_spec_ANTERIOR_nao_existem(self):
        nomes = {c.__name__ for c in STREAM_EVENTS}
        for inventado in (
            "StreamDelta",
            "ToolCallComplete",
            "TurnFinished",
            "ThoughtDelta",
            "StatusPhrase",
            "ToolCallStart",
            "ToolCallProgress",
        ):
            with self.subTest(evento=inventado):
                self.assertNotIn(inventado, nomes)

    def test_os_nomes_verdadeiros(self):
        self.assertEqual(
            {c.__name__ for c in STREAM_EVENTS},
            {
                "MessageChunk",
                "MessageStop",
                "Commentary",
                "ToolCallChunk",
                "ToolCallFinished",
                "LongToolHint",
                "GatewayNotice",
            },
        )

    def test_todo_evento_e_IMUTAVEL(self):
        """Evento mutável depois de emitido produz corrida entre consumidor e
        produtor."""
        for cls, args in (
            (MessageChunk, {"text": "x"}),
            (MessageStop, {}),
            (Commentary, {"text": "x"}),
            (ToolCallChunk, {"tool_name": "t"}),
            (ToolCallFinished, {"tool_name": "t"}),
            (LongToolHint, {"tool_name": "t", "duration": 1.0}),
            (GatewayNotice, {"kind": "k", "text": "t"}),
        ):
            with self.subTest(evento=cls.__name__):
                ev = cls(**args)
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    ev.novo_campo = 1

    def test_MessageStop_distingue_bloco_de_turno(self):
        self.assertFalse(MessageStop().final)
        self.assertTrue(MessageStop(final=True).final)


class CapabilityTests(unittest.TestCase):
    def test_adapter_declara_em_vez_de_sobrescrever(self):
        d = CapabilityDescriptor(
            supported_ops=frozenset({Operation.SEND, Operation.EDIT}),
            supports_edit=True,
            markdown_dialect=MarkdownDialect.TELEGRAM_MARKDOWN_V2,
        )
        self.assertTrue(d.supports(Operation.EDIT))
        self.assertFalse(d.supports(Operation.THREAD))

    def test_streaming_progressivo_EXIGE_saber_editar(self):
        """Prometer edição progressiva sem saber editar produz streaming que
        nunca atualiza nada."""
        with self.assertRaises(ValueError):
            CapabilityDescriptor(supports_draft_streaming=True, supports_edit=False)

    def test_comprimento_maximo_invalido_e_recusado(self):
        with self.assertRaises(ValueError):
            CapabilityDescriptor(max_message_length=0)

    def test_os_dois_modos_de_wakeup(self):
        self.assertEqual(len(WakeupMode), 2)
        self.assertEqual(CapabilityDescriptor().wakeup_mode, WakeupMode.ASYNC_DELIVERY)

    def test_contract_version_permite_geracoes_diferentes(self):
        self.assertEqual(CapabilityDescriptor().contract_version, 1)


class DeliveryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.led = DeliveryLedger()
        self.led.record("o1", "chat:1", "resposta do cron")

    def test_so_o_adapter_CONFIRMA(self):
        """Marcar entregue antes da confirmação seria fingir a entrega."""
        self.assertFalse(self.led.confirm("o1"), "não dá para confirmar sem reivindicar")
        self.assertTrue(self.led.claim("o1", pid=1, started_at=1))
        self.assertTrue(self.led.confirm("o1"))
        self.assertEqual(self.led._obligations["o1"].state, DeliveryState.DELIVERED)

    def test_reivindicacao_dupla_e_recusada(self):
        self.assertTrue(self.led.claim("o1", pid=1, started_at=1))
        self.assertFalse(self.led.claim("o1", pid=2, started_at=2))

    def test_release_devolve_a_fila(self):
        self.led.claim("o1", pid=1, started_at=1)
        self.assertTrue(self.led.release("o1"))
        self.assertEqual(len(self.led.pending()), 1)

    def test_dono_PROVADO_morto_devolve_a_obrigacao(self):
        """Prova é pid + started_at: o PID sozinho é reciclado pelo SO e daria
        um processo alheio como dono."""
        self.led.claim("o1", pid=999, started_at=123)
        self.assertEqual(self.led.reclaim_dead(live_pids={1, 2}), ["o1"])
        self.assertEqual(len(self.led.pending()), 1)

    def test_dono_vivo_nao_e_recuperado(self):
        self.led.claim("o1", pid=42, started_at=1)
        self.assertEqual(self.led.reclaim_dead(live_pids={42}), [])

    def test_tentativas_sao_contadas(self):
        self.led.claim("o1", pid=1, started_at=1)
        self.led.release("o1")
        self.led.claim("o1", pid=2, started_at=2)
        self.assertEqual(self.led._obligations["o1"].attempts, 2)


class DeadTargetsTests(unittest.TestCase):
    def setUp(self):
        self.dt = DeadTargets()

    def test_falha_transitoria_escala_o_cooldown(self):
        t0 = 1000.0
        for i, esperado in enumerate(COOLDOWN_LADDER, start=1):
            ate = self.dt.record_failure("alvo", FailureKind.TRANSIENT, now=t0)
            self.assertEqual(ate, t0 + esperado, f"falha {i}")

    def test_o_cooldown_satura_no_ultimo_degrau(self):
        for _ in range(20):
            ate = self.dt.record_failure("alvo", FailureKind.TRANSIENT, now=1000.0)
        self.assertEqual(ate, 1000.0 + COOLDOWN_LADDER[-1])

    def test_falha_PERMANENTE_nao_tem_cooldown(self):
        """Não há espera que conserte um canal deletado."""
        self.dt.record_failure("alvo", FailureKind.PERMANENT, now=1000.0)
        self.assertTrue(self.dt.is_permanently_dead("alvo"))
        self.assertTrue(self.dt.is_suspended("alvo", now=1e18))

    def test_sucesso_ZERA_o_contador(self):
        """A escada mede falha CONSECUTIVA: um destino recuperado não carrega
        a punição."""
        self.dt.record_failure("alvo", FailureKind.TRANSIENT, now=1000.0)
        self.dt.record_failure("alvo", FailureKind.TRANSIENT, now=1000.0)
        self.dt.record_success("alvo")
        ate = self.dt.record_failure("alvo", FailureKind.TRANSIENT, now=2000.0)
        self.assertEqual(ate, 2000.0 + COOLDOWN_LADDER[0])

    def test_alvo_nunca_visto_nao_esta_suspenso(self):
        self.assertFalse(self.dt.is_suspended("novo"))

    def test_a_suspensao_expira(self):
        self.dt.record_failure("alvo", FailureKind.TRANSIENT, now=1000.0)
        self.assertTrue(self.dt.is_suspended("alvo", now=1010.0))
        self.assertFalse(self.dt.is_suspended("alvo", now=1000.0 + COOLDOWN_LADDER[0] + 1))


if __name__ == "__main__":
    unittest.main()
