"""Tarefa 20 — Integração das superfícies.

Critério de pronto: "o núcleo é uma **biblioteca, não um serviço** — cada
superfície importa o agente no próprio processo, e os 7 processos escrevem no
mesmo SQLite sem corrupção. As duas leis valem em **todas** as superfícies."
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from kairos_integration.surfaces import (
    PROCESS_TOPOLOGIES,
    SURFACE_PROFILES,
    SURFACES,
    CoreIsALibrary,
    LawViolation,
    ProcessMap,
    Surface,
    assert_narrow_waist,
    assert_prompt_cache_intact,
)
from kairos_state import connect, initialize_schema
from kairos_state.contention import get_write_contention_stats, reset_stats
from kairos_state.repositories import MessageRepository, SessionRepository


class SuperficiesTests(unittest.TestCase):
    def test_sao_seis_superficies(self):
        self.assertEqual(len(SURFACES), 6)
        self.assertEqual(
            {s.value for s in SURFACES},
            {"cli", "gateway", "tui", "acp", "desktop", "dashboard"},
        )

    def test_cinco_topologias_de_processo(self):
        self.assertEqual(len(PROCESS_TOPOLOGIES), 5)

    def test_SO_o_ACP_liga_o_guard_de_aprovacao(self):
        """Torna auditável uma frase que, solta, não é verificável."""
        ligam = [s for s, p in SURFACE_PROFILES.items() if p.binds_edit_approval]
        self.assertEqual(ligam, [Surface.ACP])

    def test_toda_superficie_tem_perfil(self):
        for s in SURFACES:
            with self.subTest(surface=s):
                self.assertIn(s, SURFACE_PROFILES)


class NucleoBibliotecaTests(unittest.TestCase):
    def test_muitos_escritores_e_a_FORMA_do_sistema(self):
        """Não há 'agent server'. Sete processos escrevem no mesmo SQLite, e é
        isso que torna a contenção uma preocupação de primeira ordem."""
        pm = ProcessMap()
        for nome in ("cli", "gateway", "tui", "acp", "desktop", "cron", "subagente"):
            pm.register_writer(nome)
        self.assertEqual(len(pm.writers), 7)
        pm.assert_no_central_server()  # não levanta

    def test_UM_escritor_so_denuncia_um_servidor_no_meio(self):
        """Não é ilegal — é mudança de arquitetura que precisa ser decisão, e
        não deriva."""
        pm = ProcessMap()
        pm.register_writer("servidor-central")
        with self.assertRaises(CoreIsALibrary):
            pm.assert_no_central_server()


class Lei1Tests(unittest.TestCase):
    """O cache de prompt por conversa é sagrado."""

    def test_o_caminho_normal_passa(self):
        assert_prompt_cache_intact()

    def test_alterar_contexto_passado_VIOLA(self):
        with self.assertRaises(LawViolation) as ctx:
            assert_prompt_cache_intact(mutated_past_context=True)
        self.assertIn("custo do usuário", str(ctx.exception))

    def test_trocar_toolset_VIOLA(self):
        with self.assertRaises(LawViolation):
            assert_prompt_cache_intact(swapped_toolset=True)

    def test_reconstruir_o_system_prompt_VIOLA(self):
        with self.assertRaises(LawViolation):
            assert_prompt_cache_intact(rebuilt_system_prompt=True)

    def test_a_compactacao_e_a_UNICA_excecao(self):
        assert_prompt_cache_intact(mutated_past_context=True, is_compaction=True)

    def test_a_mensagem_nomeia_TODAS_as_violacoes(self):
        with self.assertRaises(LawViolation) as ctx:
            assert_prompt_cache_intact(
                mutated_past_context=True, swapped_toolset=True, rebuilt_system_prompt=True
            )
        msg = str(ctx.exception)
        for esperado in ("contexto passado", "toolset", "system prompt"):
            self.assertIn(esperado, msg)


class Lei2Tests(unittest.TestCase):
    """O núcleo é uma cintura estreita."""

    def test_ferramenta_core_que_terminal_e_file_resolvem_VIOLA(self):
        with self.assertRaises(LawViolation) as ctx:
            assert_narrow_waist(
                new_core_tool=True,
                solvable_by_terminal_and_file=True,
                solvable_by_skill=False,
            )
        self.assertIn("TODA chamada de API", str(ctx.exception))

    def test_ferramenta_core_que_uma_SKILL_resolve_VIOLA(self):
        with self.assertRaises(LawViolation):
            assert_narrow_waist(
                new_core_tool=True,
                solvable_by_terminal_and_file=False,
                solvable_by_skill=True,
            )

    def test_ferramenta_core_genuinamente_necessaria_passa(self):
        assert_narrow_waist(
            new_core_tool=True, solvable_by_terminal_and_file=False, solvable_by_skill=False
        )

    def test_o_que_nao_e_core_nao_e_avaliado(self):
        assert_narrow_waist(
            new_core_tool=False, solvable_by_terminal_and_file=True, solvable_by_skill=True
        )


class SeteProcessosTests(unittest.TestCase):
    """A verificação de ponta a ponta: 7 escritores no mesmo SQLite."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state.db"
        db = connect(self.path)
        initialize_schema(db)
        SessionRepository(db).create("s1", "cli")
        db.close()
        reset_stats()

    def tearDown(self):
        self._tmp.cleanup()

    def test_sete_processos_escrevem_sem_perda_nem_corrupcao(self):
        POR_ESCRITOR = 12
        superficies = ["cli", "gateway", "tui", "acp", "desktop", "cron", "subagente"]
        erros: list[Exception] = []

        def escreve(nome: str):
            conn = connect(self.path)
            repo = MessageRepository(conn)
            try:
                for i in range(POR_ESCRITOR):
                    repo.append("s1", "user", content=f"{nome}-{i}", timestamp=float(i))
            except Exception as exc:  # noqa: BLE001
                erros.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=escreve, args=(n,)) for n in superficies]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(erros, [])

        db = connect(self.path)
        try:
            total = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self.assertEqual(total, len(superficies) * POR_ESCRITOR)
            # Integridade estrutural, não só contagem.
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # Cada superfície teve TODAS as suas mensagens gravadas.
            for nome in superficies:
                n = db.execute(
                    "SELECT COUNT(*) FROM messages WHERE content LIKE ?", (f"{nome}-%",)
                ).fetchone()[0]
                self.assertEqual(n, POR_ESCRITOR, f"{nome} perdeu mensagens")
        finally:
            db.close()

    def test_a_contencao_do_cenario_acima_e_OBSERVAVEL(self):
        """Não afirma que houve contenção — afirma que, havendo, ela aparece.
        É o que a decisão de G-20 comprou."""
        st = get_write_contention_stats()
        self.assertIn("transcript", st["by_budget"])
        self.assertIn("gaveup_total", st)


if __name__ == "__main__":
    unittest.main()
