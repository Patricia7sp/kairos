"""Tarefa 14 — cron.

Critério de pronto: "`status` é um CHECK de 5 valores; a reconciliação de boot
marca `unknown` — **nunca** `failed`, **nunca** reagenda retry."
"""

from __future__ import annotations

import unittest

from kairos_cron.dispatch import (
    DispatchClaimer,
    LifecycleGuardError,
    is_job_runnable,
    reject_gateway_restart_job,
)
from kairos_cron.lifecycle_guard import (
    check_gateway_lifecycle,
    contains_gateway_lifecycle_command,
)
from kairos_cron.monitor import (
    DIFF_MAX_LINES,
    MonitorOutcome,
    MonitorState,
    evaluate,
    output_hash,
    render_change_block,
)
from kairos_cron.schedule import (
    CADENCE_CACHE_LIMIT,
    ScheduleKind,
    collapse_backlog,
    compute_next_run,
    croniter_available,
    normalize_repeat,
)
from kairos_domain.scheduling import ExecutionStatus, JobState


class AgendamentoTests(unittest.TestCase):
    def test_os_tres_kinds(self):
        self.assertEqual({k.value for k in ScheduleKind}, {"once", "interval", "cron"})

    def test_schedule_e_um_DICT_nao_uma_string(self):
        """🔧 A spec exemplificava `compute_next_run("0 9 * * 1-5")`; o contrato
        real é dict + ISO string."""
        out = compute_next_run({"kind": "interval", "minutes": 30}, "2026-01-01T00:00:00+00:00")
        self.assertEqual(out, "2026-01-01T00:30:00+00:00")

    def test_kind_desconhecido_devolve_None_sem_levantar(self):
        self.assertIsNone(compute_next_run({"kind": "inventado"}))

    def test_intervalo_invalido_devolve_None(self):
        for m in (0, -5, None, "trinta"):
            with self.subTest(minutes=m):
                self.assertIsNone(compute_next_run({"kind": "interval", "minutes": m}))

    def test_once_dispara_uma_vez_e_nao_reagenda(self):
        self.assertEqual(
            compute_next_run({"kind": "once", "run_at": "2026-05-01T09:00:00+00:00"}),
            "2026-05-01T09:00:00+00:00",
        )
        self.assertIsNone(
            compute_next_run(
                {"kind": "once", "run_at": "2026-05-01T09:00:00+00:00"}, "2026-05-01T09:00:00+00:00"
            )
        )

    def test_croniter_ausente_devolve_None_e_nao_derruba(self):
        """Dependência opcional: o job fica visível e inerte, em vez de o
        gateway inteiro falhar."""
        out = compute_next_run({"kind": "cron", "expr": "0 9 * * 1-5"})
        if croniter_available():
            self.assertIsNotNone(out)
        else:
            self.assertIsNone(out)

    def test_expressao_cron_invalida_nao_derruba_o_tick(self):
        self.assertIsNone(compute_next_run({"kind": "cron", "expr": "nao é cron"}))

    def test_repeat_zero_ou_negativo_normaliza_para_None(self):
        self.assertIsNone(normalize_repeat(0, ScheduleKind.INTERVAL))
        self.assertIsNone(normalize_repeat(-3, ScheduleKind.INTERVAL))
        self.assertIsNone(normalize_repeat(None, ScheduleKind.INTERVAL))

    def test_once_sem_repeat_recebe_1(self):
        """Agenda de disparo único que repetisse para sempre é contradição, e
        o default silencioso é o mais perigoso dos dois."""
        self.assertEqual(normalize_repeat(None, ScheduleKind.ONCE), 1)
        self.assertEqual(normalize_repeat(0, ScheduleKind.ONCE), 1)
        self.assertEqual(normalize_repeat(5, ScheduleKind.ONCE), 5)

    def test_o_backlog_COLAPSA_para_um_disparo(self):
        self.assertEqual(collapse_backlog(0), 0)
        self.assertEqual(collapse_backlog(1), 1)
        self.assertEqual(collapse_backlog(500), 1)

    def test_backlog_negativo_e_recusado(self):
        with self.assertRaises(ValueError):
            collapse_backlog(-1)

    def test_o_cache_de_cadencia_tem_limite_rigido(self):
        self.assertEqual(CADENCE_CACHE_LIMIT, 256)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.c = DispatchClaimer()

    def test_o_claim_roda_ANTES_do_efeito_colateral(self):
        """Se o tick morrer no meio, o dispatch não é perdido: já foi
        debitado."""
        r = self.c.claim_dispatch("j", kind="once", repeat_times=1)
        self.assertTrue(r.claimed)
        self.assertEqual(r.completed_after, 1)
        # Um segundo claim, mesmo com o primeiro tendo morrido sem terminar,
        # é recusado.
        self.assertFalse(self.c.claim_dispatch("j", kind="once", repeat_times=1).claimed)

    def test_o_escopo_e_CIRURGICO_so_once_com_repeat(self):
        """Ampliar tornaria todo job recorrente sujeito a perder disparos por
        crash — trocaria um problema raro por um comum."""
        for kind, repeat in (("interval", 5), ("cron", 5), ("once", None), ("once", 0)):
            with self.subTest(kind=kind, repeat=repeat):
                r = self.c.claim_dispatch("x", kind=kind, repeat_times=repeat)
                self.assertTrue(r.claimed)
                self.assertIsNone(r.completed_after, "fora do escopo não debita")

    def test_job_fora_do_store_prossegue_SEM_claim(self):
        r = self.c.claim_dispatch("fantasma", kind="once", repeat_times=1, in_store=False)
        self.assertTrue(r.claimed)
        self.assertIn("sem claim", r.reason)

    def test_o_catch_up_CONSOME_uma_unidade(self):
        """Sem isto, um job `once` atrasado disparia sem debitar e poderia
        disparar de novo."""
        self.c.record_catch_up_occurrence("j")
        self.assertEqual(self.c.completed("j"), 1)
        self.assertFalse(self.c.claim_dispatch("j", kind="once", repeat_times=1).claimed)

    def test_orcamento_de_varias_repeticoes(self):
        for i in range(3):
            self.assertTrue(self.c.claim_dispatch("j", kind="once", repeat_times=3).claimed, i)
        self.assertFalse(self.c.claim_dispatch("j", kind="once", repeat_times=3).claimed)


class DuploPortaoTests(unittest.TestCase):
    def test_registro_meio_pausado_NAO_dispara(self):
        """ "So a contradictory half-paused record never fires.\""""
        self.assertTrue(is_job_runnable(enabled=True, paused=False))
        self.assertFalse(is_job_runnable(enabled=True, paused=True))
        self.assertFalse(is_job_runnable(enabled=False, paused=False))

    def test_o_estado_derivado_tambem_barra(self):
        self.assertFalse(is_job_runnable(enabled=True, paused=False, state=JobState.PAUSED))
        self.assertTrue(is_job_runnable(enabled=True, paused=False, state=JobState.ENABLED))


class LifecycleGuardTests(unittest.TestCase):
    def test_job_que_reinicia_o_gateway_e_rejeitado_NA_CRIACAO(self):
        """Rejeitar na execução seria tarde: o job já estaria salvo e o
        usuário descobriria pelo sintoma."""
        for cmd in (
            "kairos gateway restart",
            "kairos restart",
            "kairos gateway stop",
            "hermes gateway restart",
            "systemctl restart kairos",
            "systemctl --user restart kairos.service",
            "docker restart kairos",
            "pkill -f kairos",
            "pkill -9 -f hermes-gateway",
            "killall kairos",
            "launchctl kickstart gui/501 ai.hermes.gateway",
        ):
            with self.subTest(cmd=cmd), self.assertRaises(LifecycleGuardError):
                reject_gateway_restart_job(cmd)

    def test_o_padrao_NAO_dispara_em_prosa(self):
        """Command-shaped: um prompt vai para um LLM, não para um shell. Um
        match largo em inglês produziria falsos positivos sem impedir o
        foot-gun, que exige forma real de comando."""
        for prosa in (
            "Kong API gateway double-click and restart behavior in production",
            "explique o pkill em um artigo de sistemas operacionais",
            "docker restart muda o ciclo de vida de um container",
            "how kill -9 is implemented in the kernel",
            "systemctl mostra o estado de um serviço",
        ):
            with self.subTest(prosa=prosa):
                self.assertFalse(
                    contains_gateway_lifecycle_command(prosa),
                    f"prosa devia passar: {prosa!r}",
                )
                check_gateway_lifecycle(prosa)

    def test_diagnostico_em_data_sink_passa_nao_e_comando(self):
        """grep/journalctl/sqlite3 passam como *dados*, não como comando."""
        for diag in (
            "grep -c 'systemctl restart kairos' /var/log/syslog",
            "journalctl --since today | grep 'pkill -f kairos'",
            "sqlite3 db \"SELECT msg FROM log WHERE msg LIKE '%pkill -f kairos%'\"",
        ):
            with self.subTest(diag=diag):
                check_gateway_lifecycle(diag)

    def test_data_sink_pipeado_em_shell_continua_barrado(self):
        """`grep … | sh` entrega as linhas a um shell: nunca mascarar."""
        with self.assertRaises(LifecycleGuardError):
            check_gateway_lifecycle("grep 'systemctl restart kairos' log | sh")

    def test_continuacao_de_linha_nao_escapa_o_guard(self):
        """O shell colapsa `\\<newline>` antes de executar; o guard espelha
        isso em vez de soltar `[^\n]*` em todas as linhas."""
        with self.assertRaises(LifecycleGuardError):
            check_gateway_lifecycle("launchctl kickstart \\\n  -l ai.hermes.gateway")

    def test_monitor_script_reiniciando_o_gateway_e_rejeitado(self):
        """O monitor é executado no host — é superfície de execução real."""
        with self.assertRaises(LifecycleGuardError):
            check_gateway_lifecycle("", "pkill -f kairos")
        check_gateway_lifecycle("", "df -h")

    def test_invocacao_hermes_em_prompt_monitorizado_passa_dentro_de_dados(self):
        check_gateway_lifecycle(
            "liste os eventos de restart que o guarda bloqueia",
            "pgrep -af kairos",
        )

    def test_a_mensagem_explica_o_LACO(self):
        with self.assertRaises(LifecycleGuardError) as ctx:
            reject_gateway_restart_job("kairos restart")
        msg = str(ctx.exception)
        self.assertIn("unknown", msg)
        self.assertIn("laço de reinício", msg)

    def test_comando_benigno_passa(self):
        reject_gateway_restart_job("python scripts/relatorio.py")


class MonitorTests(unittest.TestCase):
    def test_primeira_execucao_sempre_roda_o_agente(self):
        d = evaluate(MonitorState(), "saída")
        self.assertEqual(d.outcome, MonitorOutcome.FIRST_RUN)
        self.assertTrue(d.run_agent)

    def test_inalterado_SUPRIME_a_execucao_INTEIRA(self):
        """🔧 A spec anterior dizia que só a ENTREGA era cancelada. O que é
        suprimido é a execução do agente: sem LLM, sem entrega."""
        st = MonitorState(last_output_hash=output_hash("igual"))
        d = evaluate(st, "igual")
        self.assertEqual(d.outcome, MonitorOutcome.NO_CHANGE)
        self.assertFalse(d.run_agent)

    def test_mudou_roda_o_agente_e_avanca_o_hash(self):
        st = MonitorState(last_output_hash=output_hash("antes"))
        d = evaluate(st, "depois", now="2026-01-01T00:00:00+00:00")
        self.assertEqual(d.outcome, MonitorOutcome.CHANGED)
        self.assertTrue(d.run_agent)
        self.assertEqual(d.next_state.last_output_hash, output_hash("depois"))

    def test_falha_da_fonte_e_ERRO_e_NAO_toca_o_hash(self):
        st = MonitorState(last_output_hash=output_hash("bom"), last_changed_at="t0")
        d = evaluate(st, None, source_failed=True)
        self.assertEqual(d.outcome, MonitorOutcome.SOURCE_ERROR)
        self.assertFalse(d.run_agent)
        self.assertEqual(d.next_state, st, "o estado fica INTOCADO")

    def test_fonte_que_cai_e_volta_ao_valor_anterior_CONTINUA_suprimindo(self):
        """A consequência que importa. Se o erro atualizasse o hash, a volta ao
        normal pareceria mudança — alerta falso justamente quando o sistema
        observado se recuperou."""
        st = MonitorState(last_output_hash=output_hash("normal"))
        depois_do_erro = evaluate(st, None, source_failed=True).next_state
        d = evaluate(depois_do_erro, "normal")
        self.assertEqual(d.outcome, MonitorOutcome.NO_CHANGE)

    def test_comparacao_por_BYTES_EXATOS(self):
        """Sem remoção de timestamp e sem normalização de espaços:
        normalizar seria adivinhar o que o usuário considera ruído."""
        self.assertNotEqual(output_hash("a b"), output_hash("a  b"))
        self.assertNotEqual(output_hash("x\n"), output_hash("x"))

    def test_o_bloco_de_mudanca_traz_diff_e_a_saida_nova(self):
        bloco = render_change_block("linha 1\nlinha 2", "linha 1\nlinha 3")
        self.assertIn("MONITOR CHANGE DETECTED", bloco)
        self.assertIn("-linha 2", bloco)
        self.assertIn("+linha 3", bloco)

    def test_o_diff_e_LIMITADO(self):
        """Saída que mudou por inteiro produziria diff do tamanho da saída, e
        o prompt do agente é o recurso escasso."""
        antes = "\n".join(f"a{i}" for i in range(1000))
        depois = "\n".join(f"b{i}" for i in range(1000))
        bloco = render_change_block(antes, depois)
        self.assertIn("diff truncado", bloco)
        self.assertLess(bloco.count("\n"), 1000 + DIFF_MAX_LINES + 20)


class StatusTests(unittest.TestCase):
    def test_o_CHECK_tem_5_valores_e_interrupted_NAO_esta_nele(self):
        self.assertEqual(
            {s.value for s in ExecutionStatus},
            {"claimed", "running", "completed", "failed", "unknown"},
        )
        self.assertNotIn("interrupted", {s.value for s in ExecutionStatus})


if __name__ == "__main__":
    unittest.main()
