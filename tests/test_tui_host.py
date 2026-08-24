"""Tarefa 16 — ui-tui (lado Python: host de compute e RPC).

Critério de pronto: "o compute host isola contra *GIL starvation* (ADR 012)."
O frontend TypeScript tem suíte própria em `ui-tui/` (vitest).
"""

from __future__ import annotations

import unittest

from kairos_tui_host.rpc import DuplicateMethod, HandlerRegistry, profile_scoped
from kairos_tui_host.supervisor import (
    GIL_STARVATION_RATIONALE,
    HostState,
    HostSupervisor,
    RestartPolicy,
)


class RegistroDeferidoTests(unittest.TestCase):
    def setUp(self):
        self.reg = HandlerRegistry()

    def test_o_decorador_apenas_ENFILEIRA(self):
        @self.reg.method("session.list")
        def handler():
            return "x"

        self.assertEqual(self.reg.pending_names, ("session.list",))

    def test_install_reconstroi_com_os_GLOBAIS_do_servidor(self):
        """O motivo mecânico do registro deferido: permitiu quebrar um
        server.py gigante em módulos SEM trocar o modelo de escopo por
        globais — os handlers continuam enxergando os globais do servidor."""

        @self.reg.method("usa.global")
        def handler():
            return VALOR_DO_SERVIDOR  # noqa: F821 — vem dos globais do servidor

        class Servidor:
            pass

        servidor = Servidor()
        servidor.VALOR_DO_SERVIDOR = 42

        instalados = self.reg.install(servidor)
        self.assertEqual(instalados["usa.global"](), 42)

    def test_defaults_e_doc_sao_PRESERVADOS(self):
        """Perder qualquer um mudaria o comportamento do handler em
        silêncio."""

        @self.reg.method("com.default")
        def handler(a=1, *, b=2):
            """documentação importante"""
            return (a, b)

        class Servidor:
            pass

        inst = self.reg.install(Servidor())["com.default"]
        self.assertEqual(inst(), (1, 2))
        self.assertEqual(inst.__doc__, "documentação importante")

    def test_atributos_do_decorador_sobrevivem_ao_install(self):
        @self.reg.method("escopado")
        @profile_scoped
        def handler():
            return None

        class Servidor:
            pass

        inst = self.reg.install(Servidor())["escopado"]
        self.assertTrue(getattr(inst, "_kairos_profile_scoped", False))

    def test_metodo_duplicado_e_recusado(self):
        @self.reg.method("dup")
        def a():
            return None

        with self.assertRaises(DuplicateMethod):

            @self.reg.method("dup")
            def b():
                return None

    def test_install_nao_muta_o_original(self):
        @self.reg.method("m")
        def handler():
            return 1

        class Servidor:
            pass

        self.reg.install(Servidor())
        self.assertEqual(handler(), 1)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.sup = HostSupervisor(policy=RestartPolicy(max_restarts=2, window_seconds=100))

    def test_o_porque_do_PROCESSO_separado_fica_escrito(self):
        """Uma thread não bastaria: ferramenta em C que não solta o GIL
        monopoliza o interpretador e a TUI congela."""
        self.assertIn("GIL", GIL_STARVATION_RATIONALE)
        self.assertIn("processo FILHO", GIL_STARVATION_RATIONALE)
        self.assertIn("60 FPS", GIL_STARVATION_RATIONALE)

    def test_reinicia_dentro_do_orcamento(self):
        self.assertTrue(self.sup.note_crash(now=1000))
        self.assertEqual(self.sup.state, HostState.STARTING)
        self.assertTrue(self.sup.note_crash(now=1010))

    def test_DESISTE_depois_de_estourar(self):
        """Reiniciar em laço consome CPU e esconde o erro real numa enxurrada
        de logs iguais."""
        self.sup.note_crash(now=1000)
        self.sup.note_crash(now=1010)
        self.assertFalse(self.sup.note_crash(now=1020))
        self.assertTrue(self.sup.gave_up)

    def test_a_janela_ZERA_o_contador(self):
        """Um processo que roda bem por horas e cai uma vez não é um processo
        que falha em laço."""
        self.sup.note_crash(now=1000)
        self.sup.note_crash(now=1010)
        # Muito depois: fora da janela.
        self.assertTrue(self.sup.note_crash(now=5000))
        self.assertFalse(self.sup.gave_up)

    def test_sem_heartbeat_NAO_e_o_mesmo_que_morto(self):
        """Um host ocupado num laço de CPU está vivo e travado, e a resposta
        é diferente: morto se reinicia, travado se interrompe."""
        self.sup.note_ready()
        self.sup.note_heartbeat_missed()
        self.assertEqual(self.sup.state, HostState.UNRESPONSIVE)
        self.assertNotEqual(self.sup.state, HostState.CRASHED)

    def test_heartbeat_perdido_fora_do_estado_READY_nao_muda_nada(self):
        self.sup.note_crash(now=1000)
        self.sup.note_heartbeat_missed()
        self.assertEqual(self.sup.state, HostState.STARTING)

    def test_os_estados_do_host(self):
        self.assertEqual(
            {s.value for s in HostState},
            {"stopped", "starting", "ready", "unresponsive", "crashed"},
        )


if __name__ == "__main__":
    unittest.main()
