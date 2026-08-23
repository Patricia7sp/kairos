"""Tarefa 02 — Entidades de Domínio.

Critério de pronto: "Todas as entidades implementadas com as regras e
invariantes descritas, cada uma com teste que a viola e falha."

Cada teste abaixo **viola** deliberadamente uma regra e exige que o domínio
recuse. Um teste que só exercita o caminho feliz não provaria nada: a regra
existe justamente para o caminho infeliz.
"""

from __future__ import annotations

import unittest

from kairos_domain.compaction import (
    COOLDOWN_LADDER,
    CompactionPolicy,
    SummaryProvenanceError,
    effective_protect_first_n,
    next_cooldown_seconds,
    plan_boundary,
    validate_summary_provenance,
)
from kairos_domain.credentials import (
    NotACredentialError,
    assert_env_is_for_secrets,
    has_usable_secret,
    should_reauthenticate,
)
from kairos_domain.identity import BUILTIN_PLATFORM_COUNT, Platform, SessionSource
from kairos_domain.invariants import INVARIANTS, Enforcement, unenforced
from kairos_domain.message import (
    Message,
    Role,
    RoleAlternationError,
    SyntheticUserMessageError,
    ToolPairError,
    Visibility,
    check_no_synthetic_user_message,
    check_role_alternation,
    check_tool_pairs_intact,
)
from kairos_domain.ownership import (
    Actor,
    CronReferenceIndex,
    HardDeleteByAutonomousActor,
    ProtectedByCronReference,
    Provenance,
    Skill,
    UserSkillAutoCurated,
    assert_can_hard_delete,
    can_archive,
)
from kairos_domain.rules import RULES, Kind, process_rules, runtime_rules
from kairos_domain.scheduling import (
    DeadSubprocessReportedSuccess,
    ExecutionStatus,
    JobState,
    MonitorState,
    TerminalStateMutated,
    assert_not_reporting_success_when_dead,
    assert_terminal_immutable,
    collapse_backlog,
    effective_job_state,
    finish_status_for_dead_owner,
    monitor_hash_after_source_failure,
)
from kairos_domain.session import ChildKind, EndReason, Lineage, Session


def user(text="oi", **kw):
    return Message(role=Role.USER, content=text, **kw)


def assistant(text="ok", **kw):
    return Message(role=Role.ASSISTANT, content=text, **kw)


def tool(call_id, text="resultado"):
    return Message(role=Role.TOOL, content=text, tool_call_id=call_id)


# ---------------------------------------------------------------------------
class IdentityTests(unittest.TestCase):
    def test_as_plataformas_embutidas(self):
        # A spec (data-dictionary §2.1) diz "23 valores" e lista 24. A
        # contagem está errada; verificado em gateway/config.py:317-341.
        self.assertEqual(BUILTIN_PLATFORM_COUNT, 24)

    def test_plataforma_de_plugin_resolve_dinamicamente(self):
        """Lei 2 — capacidade nova chega pela borda, não pelo núcleo."""
        irc = Platform("irc")
        self.assertEqual(irc.value, "irc")
        self.assertFalse(irc.is_builtin)
        self.assertTrue(Platform.SLACK.is_builtin)
        # Identidade estável: duas resoluções dão o MESMO objeto, senão
        # comparações por `is` quebrariam para plataformas de plugin.
        self.assertIs(Platform("irc"), irc)

    def test_plataforma_vazia_nao_vira_membro(self):
        with self.assertRaises(ValueError):
            Platform("")

    def test_chat_id_vazio_e_recusado(self):
        with self.assertRaises(ValueError):
            SessionSource(platform=Platform.TELEGRAM, chat_id="")

    def test_plataforma_como_string_crua_e_recusada(self):
        with self.assertRaises(TypeError):
            SessionSource(platform="telegram", chat_id="c1")  # type: ignore[arg-type]

    def test_rota_rejeitada_fica_fora_da_identidade(self):
        a = SessionSource(platform=Platform.SLACK, chat_id="c1")
        b = SessionSource(platform=Platform.SLACK, chat_id="c1", profile_route_rejected=True)
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
class MessageTests(unittest.TestCase):
    def test_estado_impossivel_ativa_e_compactada(self):
        with self.assertRaises(ValueError):
            Message(role=Role.USER, active=True, compacted=True)

    def test_papel_tool_sem_tool_call_id_e_recusado(self):
        with self.assertRaises(ValueError):
            Message(role=Role.TOOL, content="x")

    def test_as_tres_visibilidades(self):
        ativa = Message(role=Role.USER, active=True, compacted=False)
        arq = Message(role=Role.USER, active=False, compacted=True)
        reb = Message(role=Role.USER, active=False, compacted=False)

        self.assertEqual(ativa.visibility, Visibility.ACTIVE)
        self.assertEqual(arq.visibility, Visibility.ARCHIVED)
        self.assertEqual(reb.visibility, Visibility.REWOUND)

        # Arquivada some do contexto mas continua buscável — é a diferença
        # entre compactar e apagar.
        self.assertFalse(arq.visibility.in_model_context)
        self.assertTrue(arq.visibility.searchable)
        self.assertFalse(reb.visibility.searchable)

    def test_sidecar_prevalece_sobre_content(self):
        m = Message(role=Role.ASSISTANT, content="exibido", api_content="enviado")
        self.assertEqual(m.for_api, "enviado")
        self.assertEqual(m.content, "exibido")


# ---------------------------------------------------------------------------
class InvariantesDeSequenciaTests(unittest.TestCase):
    """Invariantes 2, 3 e 4."""

    def test_inv2_dois_usuarios_seguidos_violam(self):
        with self.assertRaises(RoleAlternationError):
            check_role_alternation([user(), user()])

    def test_inv2_dois_assistentes_seguidos_violam(self):
        with self.assertRaises(RoleAlternationError):
            check_role_alternation([user(), assistant(), assistant()])

    def test_inv2_varios_tool_results_seguidos_sao_validos(self):
        # Uma chamada do assistente pode gerar vários resultados; isso é a
        # forma normal do protocolo, não violação.
        check_role_alternation([
            user(),
            assistant(tool_calls=("a", "b")),
            tool("a"), tool("b"),
            assistant(),
        ])

    def test_inv3_usuario_sintetico_viola(self):
        with self.assertRaises(SyntheticUserMessageError):
            check_no_synthetic_user_message([user(), assistant(), user(synthetic=True)])

    def test_inv3_assistente_sintetico_e_permitido(self):
        check_no_synthetic_user_message([user(), Message(role=Role.ASSISTANT, synthetic=True)])

    def test_inv4_tool_call_sem_resultado_viola(self):
        with self.assertRaises(ToolPairError):
            check_tool_pairs_intact([user(), assistant(tool_calls=("a",))])

    def test_inv4_tool_result_orfao_viola(self):
        with self.assertRaises(ToolPairError):
            check_tool_pairs_intact([user(), assistant(), tool("fantasma")])


# ---------------------------------------------------------------------------
class CompactionTests(unittest.TestCase):
    def test_regra1_fronteira_nao_corta_par_de_ferramenta(self):
        msgs = [
            user(),                          # 0
            assistant(tool_calls=("a",)),    # 1  <- cortar aqui deixaria 'a' órfão
            tool("a"),                       # 2
            assistant(),                     # 3
            user(),                          # 4
            assistant(),                     # 5
        ]
        boundary = plan_boundary(msgs, CompactionPolicy(protect_first_n=2, protect_last_n=2))
        # Empurrada de 2 para 3: a cabeça preservada não pode conter um
        # tool_call cujo resultado ficou fora dela.
        self.assertEqual(boundary, 3)
        check_tool_pairs_intact(msgs[:boundary])

    def test_regra2_cauda_real_sobrevive_a_pressao(self):
        msgs = [user(), assistant(), user(), assistant()]
        # protect_first_n gigantesco não pode invadir a cauda protegida.
        boundary = plan_boundary(msgs, CompactionPolicy(protect_first_n=99, protect_last_n=2))
        self.assertLessEqual(boundary, 2)

    def test_regra3_protecao_da_cabeca_decai(self):
        self.assertEqual(effective_protect_first_n(5, 0), 5)
        self.assertEqual(effective_protect_first_n(5, 3), 2)
        # Chega a zero: sem isso, a compactação travaria para sempre.
        self.assertEqual(effective_protect_first_n(5, 99), 0)

    def test_regra4_sumario_com_turno_de_usuario_viola(self):
        with self.assertRaises(SummaryProvenanceError):
            validate_summary_provenance([assistant("resumo"), user("eu nunca disse isso")])

    def test_regra6_escada_de_cooldown_satura(self):
        self.assertEqual(next_cooldown_seconds(0), 0)
        self.assertEqual(next_cooldown_seconds(1), COOLDOWN_LADDER[0])
        self.assertEqual(next_cooldown_seconds(3), COOLDOWN_LADDER[-1])
        self.assertEqual(next_cooldown_seconds(99), COOLDOWN_LADDER[-1])

    def test_limiar_invalido_e_recusado(self):
        with self.assertRaises(ValueError):
            CompactionPolicy(threshold_percent=1.5)
        with self.assertRaises(ValueError):
            CompactionPolicy(threshold_percent=0.0)

    def test_piso_de_contexto_pequeno_e_raise_only(self):
        p = CompactionPolicy(threshold_percent=0.75, small_context_floor_percent=0.60)
        self.assertEqual(p.effective_threshold(), 0.75)  # nunca abaixa
        p2 = CompactionPolicy(threshold_percent=0.75, small_context_floor_percent=0.90)
        self.assertEqual(p2.effective_threshold(), 0.90)

    def test_reserva_de_saida_reduz_o_gatilho(self):
        sem = CompactionPolicy(reserved_output_tokens=0).trigger_tokens(100_000)
        com = CompactionPolicy(reserved_output_tokens=20_000).trigger_tokens(100_000)
        self.assertLess(com, sem)


# ---------------------------------------------------------------------------
class LineageTests(unittest.TestCase):
    def setUp(self):
        self.lin = Lineage()

    def _s(self, sid, parent=None, end=None, ended_at=None, started=1.0, **kw):
        s = Session(id=sid, source="cli", started_at=started,
                    parent_session_id=parent, end_reason=end, ended_at=ended_at, **kw)
        self.lin.add(s)
        return s

    def test_sessao_pai_de_si_mesma_e_recusada(self):
        with self.assertRaises(ValueError):
            Session(id="s1", source="cli", started_at=1.0, parent_session_id="s1")

    def test_filho_de_compressao_nao_aparece_na_listagem(self):
        pai = self._s("p", end=EndReason.COMPRESSION, ended_at=5.0)
        filho = self._s("f", parent="p", started=6.0)
        self.assertEqual(self.lin.child_kind(filho), ChildKind.COMPRESSION)
        self.assertFalse(self.lin.is_listable(filho))
        self.assertTrue(self.lin.is_listable(pai))

    def test_branch_e_reset_aparecem_na_listagem(self):
        self._s("p", end=EndReason.BRANCHED, ended_at=5.0)
        b = self._s("b", parent="p", started=6.0)
        self.assertEqual(self.lin.child_kind(b), ChildKind.BRANCH)
        self.assertTrue(self.lin.is_listable(b))

        self._s("p2", end=EndReason.IDLE, ended_at=5.0)
        r = self._s("r", parent="p2", started=6.0)
        self.assertEqual(self.lin.child_kind(r), ChildKind.RESET)
        self.assertTrue(self.lin.is_listable(r))

    def test_raiz_sobrevive_a_cadeia_de_compressao(self):
        self._s("raiz", end=EndReason.COMPRESSION, ended_at=1.0)
        self._s("c1", parent="raiz", end=EndReason.COMPRESSION, ended_at=2.0, started=1.0)
        c2 = self._s("c2", parent="c1", started=2.0)
        self.assertEqual(self.lin.root_of(c2).id, "raiz")
        self.assertEqual([s.id for s in self.lin.chain(c2)], ["raiz", "c1", "c2"])

    def test_ciclo_na_linhagem_e_detectado(self):
        a = Session(id="a", source="cli", started_at=1.0, parent_session_id="b")
        b = Session(id="b", source="cli", started_at=1.0, parent_session_id="a")
        self.lin.add(a)
        self.lin.add(b)
        with self.assertRaises(ValueError):
            self.lin.root_of(a)


# ---------------------------------------------------------------------------
class OwnershipTests(unittest.TestCase):
    """Invariantes 9 e 10."""

    def test_inv10_curador_nao_toca_skill_do_usuario(self):
        s = Skill(name="minha", provenance=Provenance.USER)
        with self.assertRaises(UserSkillAutoCurated):
            can_archive(s, Actor.CURATOR)

    def test_inv10_usuario_pode_arquivar_a_propria(self):
        can_archive(Skill(name="minha", provenance=Provenance.USER), Actor.USER_FOREGROUND)

    def test_curador_pode_arquivar_o_proprio_sedimento(self):
        can_archive(Skill(name="sed", provenance=Provenance.SEDIMENT), Actor.CURATOR)

    def test_skill_referenciada_por_cron_pausado_e_protegida(self):
        idx = CronReferenceIndex(referenced={"sed"})
        with self.assertRaises(ProtectedByCronReference):
            can_archive(Skill(name="sed", provenance=Provenance.SEDIMENT), Actor.CURATOR, idx)

    def test_inv9_ator_autonomo_nao_faz_hard_delete(self):
        for actor in (Actor.CURATOR, Actor.BACKGROUND_REVIEW):
            with self.subTest(actor=actor):
                with self.assertRaises(HardDeleteByAutonomousActor):
                    assert_can_hard_delete(actor)

    def test_usuario_em_foreground_pode(self):
        assert_can_hard_delete(Actor.USER_FOREGROUND)


# ---------------------------------------------------------------------------
class CredentialTests(unittest.TestCase):
    def test_placeholders_nao_sao_segredo(self):
        for bad in ("changeme", "  ***  ", "your_api_key", "", None, "TODO"):
            with self.subTest(value=bad):
                self.assertFalse(has_usable_secret(bad))
        self.assertTrue(has_usable_secret("sk-real-1234"))

    def test_config_nao_credencial_no_env_e_recusada(self):
        with self.assertRaises(NotACredentialError):
            assert_env_is_for_secrets("HERMES_LOG_LEVEL", is_credential=False)
        assert_env_is_for_secrets("OPENAI_API_KEY", is_credential=True)

    def test_so_401_403_confirmado_reautentica(self):
        self.assertTrue(should_reauthenticate(401))
        self.assertTrue(should_reauthenticate(403))
        # Timeout e rede são conectividade — reautenticar aqui derrubaria a
        # sessão do usuário por um blip.
        self.assertFalse(should_reauthenticate(None, timed_out=True))
        self.assertFalse(should_reauthenticate(None, network_error=True))
        self.assertFalse(should_reauthenticate(500))
        self.assertFalse(should_reauthenticate(401, timed_out=True))


# ---------------------------------------------------------------------------
class SchedulingTests(unittest.TestCase):
    """Invariantes 7, 8 e 15."""

    def test_job_habilitado_nunca_aparece_pausado(self):
        # "The 07-30 outage failure mode".
        self.assertEqual(effective_job_state(enabled=True, paused=True), JobState.ENABLED)
        self.assertEqual(effective_job_state(enabled=False, paused=True), JobState.PAUSED)
        self.assertEqual(effective_job_state(enabled=False, paused=False), JobState.DISABLED)

    def test_backlog_colapsa_para_um_disparo(self):
        self.assertEqual(collapse_backlog(0), 0)
        self.assertEqual(collapse_backlog(1), 1)
        self.assertEqual(collapse_backlog(500), 1)

    def test_inv7_estado_terminal_e_imutavel(self):
        for terminal in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.UNKNOWN):
            with self.subTest(status=terminal):
                with self.assertRaises(TerminalStateMutated):
                    assert_terminal_immutable(terminal, ExecutionStatus.RUNNING)
        # Não-terminal pode avançar.
        assert_terminal_immutable(ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED)

    def test_inv8_reconciliacao_exige_prova_de_morte(self):
        self.assertEqual(finish_status_for_dead_owner(owner_is_live=False), ExecutionStatus.UNKNOWN)
        with self.assertRaises(Exception):
            finish_status_for_dead_owner(owner_is_live=True)

    def test_inv15_morto_nao_reporta_sucesso(self):
        with self.assertRaises(DeadSubprocessReportedSuccess):
            assert_not_reporting_success_when_dead(
                owner_is_live=False, status=ExecutionStatus.COMPLETED
            )
        # 'unknown' é exatamente o que ele PODE reportar.
        assert_not_reporting_success_when_dead(
            owner_is_live=False, status=ExecutionStatus.UNKNOWN
        )

    def test_falha_da_fonte_nao_muda_o_hash(self):
        antes = MonitorState(last_output_hash="abc", last_changed_at=1.0)
        self.assertEqual(monitor_hash_after_source_failure(antes), antes)


# ---------------------------------------------------------------------------
class RegistryTests(unittest.TestCase):
    def test_os_15_invariantes_estao_registrados(self):
        self.assertEqual(len(INVARIANTS), 15)
        self.assertEqual([i.number for i in INVARIANTS], list(range(1, 16)))

    def test_todo_invariante_do_dominio_aponta_para_codigo_existente(self):
        import kairos_domain
        for inv in INVARIANTS:
            if inv.enforcement is not Enforcement.DOMAIN:
                continue
            with self.subTest(invariant=inv.number):
                módulo, _, resto = inv.enforced_at.partition(".")
                função = resto.split(" ")[0]
                self.assertTrue(
                    hasattr(getattr(kairos_domain, módulo, None) or
                            __import__(f"kairos_domain.{módulo}", fromlist=[função]), função),
                    f"invariante {inv.number} aponta para {inv.enforced_at}, que não existe",
                )

    def test_invariantes_diferidos_sao_os_esperados(self):
        # Deve ENCOLHER a cada tarefa. Se este número subir, algo regrediu.
        self.assertEqual({i.number for i in unenforced()}, {1, 6, 11, 12, 13, 14})

    def test_as_regras_dos_sete_grupos(self):
        grupos = {r.group for r in RULES}
        self.assertEqual(
            grupos,
            {"cache", "compaction", "ownership", "credentials", "scheduling", "surface", "rejected"},
        )

    def test_as_dez_regras_de_compactacao(self):
        from kairos_domain.rules import by_group
        self.assertEqual(len(by_group("compaction")), 10)

    def test_regras_de_processo_nao_viram_codigo_falso(self):
        # A classificação existe para que regra de code review não seja
        # encenada como função. Ambos os conjuntos devem ser não-vazios.
        self.assertGreater(len(runtime_rules()), 0)
        self.assertGreater(len(process_rules()), 0)
        self.assertEqual(len(runtime_rules()) + len(process_rules()), len(RULES))
        for r in process_rules():
            self.assertIn("rubrica", r.enforced_at)


if __name__ == "__main__":
    unittest.main()
