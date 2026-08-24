"""Tarefa 21 — evals.

Critério de pronto: "o harness roda e mede." Mais o alerta **G-19**: no legado
ele **nunca roda em CI**, e medir sem nunca medir é o mesmo que não medir.
"""

from __future__ import annotations

import unittest

from kairos_evals.recall import (
    DEFAULT_RECALL_THRESHOLD,
    Fact,
    FactKind,
    RecallReport,
    check_gate,
    score_recall,
)


def ident(literal: str) -> Fact:
    return Fact(FactKind.IDENTIFIER, f"o commit {literal}", literal=literal)


def decisao(texto: str) -> Fact:
    return Fact(FactKind.DECISION, texto)


class MedicaoTests(unittest.TestCase):
    def test_identificador_e_checado_MECANICAMENTE(self):
        """A verificação de que um SHA aparece é determinística; delegá-la a
        um modelo introduziria ruído numa medida que não precisa dele."""
        r = score_recall([ident("a1b2c3d")], "conforme a1b2c3d, seguimos")
        self.assertTrue(r[0].recalled)
        self.assertEqual(r[0].method, "literal")

    def test_identificador_ausente_e_detectado(self):
        r = score_recall([ident("a1b2c3d")], "conforme o commit anterior")
        self.assertFalse(r[0].recalled)

    def test_o_juiz_cobre_SO_o_que_exige_julgamento(self):
        chamadas = []

        def juiz(fato, resumo):
            chamadas.append(fato)
            return True

        score_recall([ident("abc123"), decisao("optamos por SQLite")], "abc123", judge=juiz)
        self.assertEqual(chamadas, ["optamos por SQLite"], "o identificador não pode ir ao juiz")

    def test_juiz_indisponivel_NAO_aprova_por_omissao(self):
        """Um eval que passa porque o juiz caiu é pior que eval nenhum."""

        def juiz_quebrado(fato, resumo):
            raise RuntimeError("modelo fora do ar")

        r = score_recall([decisao("qualquer")], "resumo", judge=juiz_quebrado)
        self.assertFalse(r[0].recalled)

    def test_sem_juiz_a_heuristica_e_CONSERVADORA(self):
        """Melhor o gate reprovar por medida grosseira que aprovar por
        ausência de medida."""
        r = score_recall(
            [decisao("optamos por SQLite com WAL")], "decidimos usar SQLite no modo WAL"
        )
        self.assertTrue(r[0].recalled)
        r = score_recall([decisao("optamos por SQLite com WAL")], "falamos de outra coisa")
        self.assertFalse(r[0].recalled)

    def test_as_quatro_classes_de_fato(self):
        self.assertEqual(len(FactKind), 4)
        self.assertTrue(ident("x").must_survive_literally)
        self.assertFalse(decisao("x").must_survive_literally)


class RelatorioTests(unittest.TestCase):
    def test_score_e_a_fracao_preservada(self):
        rep = RecallReport(
            score_recall(
                [ident("aaa"), ident("bbb"), decisao("uma decisão qualquer")],
                "aaa e uma decisão qualquer",
            )
        )
        self.assertEqual(rep.total, 3)
        self.assertEqual(rep.recalled, 2)
        self.assertAlmostEqual(rep.score, 2 / 3)

    def test_relatorio_vazio_nao_divide_por_zero(self):
        self.assertEqual(RecallReport([]).score, 1.0)

    def test_identificadores_perdidos_sao_reportados_A_PARTE(self):
        rep = RecallReport(score_recall([ident("sumiu"), decisao("ok")], "ok"))
        self.assertEqual([f.literal for f in rep.lost_identifiers], ["sumiu"])


class G19GateTests(unittest.TestCase):
    """O gate que o legado não tinha — e que ele nunca rodava."""

    def test_recall_acima_do_limiar_passa(self):
        rep = RecallReport(score_recall([decisao("a"), decisao("b")], "a b"))
        self.assertTrue(check_gate(rep).passed)

    def test_recall_abaixo_do_limiar_REPROVA(self):
        fatos = [decisao(f"decisão número {i} sobre algo") for i in range(10)]
        rep = RecallReport(score_recall(fatos, "decisão número 1 sobre algo"))
        g = check_gate(rep)
        self.assertFalse(g.passed)
        self.assertIn("abaixo do limiar", g.reason)

    def test_QUALQUER_identificador_perdido_reprova_INDEPENDENTE_do_score(self):
        """Um score de 0,9 com um SHA perdido é pior que 0,8 sem nenhum: o
        agregado esconde exatamente a falha mais cara."""
        fatos = [decisao(f"decisão {i} registrada aqui") for i in range(9)] + [ident("sumiu")]
        resumo = " ".join(f"decisão {i} registrada aqui" for i in range(9))
        rep = RecallReport(score_recall(fatos, resumo))
        self.assertGreaterEqual(rep.score, 0.85, "o score sozinho passaria")
        g = check_gate(rep)
        self.assertFalse(g.passed, "mas o identificador perdido reprova")
        self.assertIn("sumiu", g.reason)

    def test_o_gate_reporta_score_e_limiar(self):
        g = check_gate(RecallReport(score_recall([decisao("a")], "a")))
        self.assertEqual(g.threshold, DEFAULT_RECALL_THRESHOLD)
        self.assertEqual(g.score, 1.0)

    def test_o_limiar_e_configuravel(self):
        fatos = [decisao(f"item {i} descrito") for i in range(4)]
        rep = RecallReport(score_recall(fatos, "item 0 descrito item 1 descrito"))
        self.assertFalse(check_gate(rep, threshold=0.9).passed)
        self.assertTrue(check_gate(rep, threshold=0.4).passed)

    def test_a_regra_de_identificador_pode_ser_desligada_explicitamente(self):
        rep = RecallReport(
            score_recall(
                [ident("sumiu")] + [decisao(f"d{i}") for i in range(9)],
                " ".join(f"d{i}" for i in range(9)),
            )
        )
        self.assertFalse(check_gate(rep).passed)
        self.assertTrue(check_gate(rep, fail_on_lost_identifier=False, threshold=0.5).passed)

    def test_o_limiar_default_e_alto_o_bastante_para_significar_algo(self):
        self.assertGreaterEqual(DEFAULT_RECALL_THRESHOLD, 0.8)


if __name__ == "__main__":
    unittest.main()


class HeuristicaTests(unittest.TestCase):
    """A regressão que o teste encontrou: falso positivo por descartar
    tokens de identidade."""

    def test_numero_que_SOME_e_fato_perdido(self):
        """A primeira versão filtrava por comprimento e descartava o número,
        reportando 100% de recall com metade dos fatos perdidos."""
        fatos = [decisao(f"item {i} descrito") for i in range(4)]
        rep = RecallReport(score_recall(fatos, "item 0 descrito item 1 descrito"))
        self.assertEqual(rep.recalled, 2, "só os itens 0 e 1 sobreviveram")
        self.assertAlmostEqual(rep.score, 0.5)

    def test_sigla_que_some_tambem_reprova(self):
        r = score_recall([decisao("usamos SQLite com WAL")], "usamos SQLite normalmente")
        self.assertFalse(r[0].recalled)

    def test_prosa_PARAFRASEADA_ainda_conta(self):
        """Identidade é obrigatória; prosa pode ser reescrita."""
        r = score_recall(
            [decisao("optamos por SQLite com WAL")], "decidimos usar SQLite no modo WAL"
        )
        self.assertTrue(r[0].recalled)

    def test_fato_so_de_identidade_sobrevive_se_ela_sobreviver(self):
        self.assertTrue(score_recall([decisao("PR 4210")], "ver PR 4210")[0].recalled)
        self.assertFalse(score_recall([decisao("PR 4210")], "ver o PR anterior")[0].recalled)
