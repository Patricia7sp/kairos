"""Testes da arquitetura de aprendizado operacional por experiência.

Fecha o laço do ciclo de 9 passos em nível determinístico: captura,
validação, injeção e feedback. A parte "Executar" real depende de um
provider (external); aqui simulamos o ciclo via store + context builder.
"""

import tempfile
import unittest
from pathlib import Path

from kairos_memory import (
    EXPERIENCE_BLOCK_HEADER,
    ExperienceStatus,
    ExperienceStore,
    build_experience_context,
)


class StoreBasicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_and_list(self):
        store = ExperienceStore(self.home)
        exp = store.add(
            trigger="docker build 500",
            observation="image push falhou com timeout",
            correction="aumentar --timeout para 300",
            source="usuario",
            scope="global",
            confidence=0.8,
            status=ExperienceStatus.ATIVA,
        )
        self.assertEqual(exp.status, ExperienceStatus.ATIVA)
        self.assertEqual(exp.confidence, 0.8)
        items = store.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, exp.id)

    def test_add_default_candidata(self):
        store = ExperienceStore(self.home)
        exp = store.add(
            trigger="erro de conexao",
            observation="gateway caiu",
            correction="(pendente)",
        )
        self.assertEqual(exp.status, ExperienceStatus.CANDIDATA)

    def test_get_by_id(self):
        store = ExperienceStore(self.home)
        exp = store.add(trigger="t", observation="o", correction="c")
        found = store.get(exp.id)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, exp.id)
        self.assertIsNone(store.get("naoexiste"))

    def test_confirm_candidata_vira_ativa(self):
        store = ExperienceStore(self.home)
        exp = store.add(trigger="t", observation="o", correction="c")
        self.assertEqual(exp.status, ExperienceStatus.CANDIDATA)
        confirmed = store.confirm(exp.id)
        self.assertIsNotNone(confirmed)
        assert confirmed is not None
        self.assertEqual(confirmed.status, ExperienceStatus.ATIVA)
        self.assertGreaterEqual(confirmed.confidence, 0.6)

    def test_confirm_idempotent_na_ativa(self):
        store = ExperienceStore(self.home)
        exp = store.add(trigger="t", observation="o", correction="c", status=ExperienceStatus.ATIVA)
        result = store.confirm(exp.id)
        self.assertIsNone(result)  # already ativa

    def test_reject_remove(self):
        store = ExperienceStore(self.home)
        exp = store.add(trigger="t", observation="o", correction="c")
        self.assertTrue(store.reject(exp.id))
        self.assertIsNone(store.get(exp.id))
        self.assertFalse(store.reject("naoexiste"))

    def test_invalidate_muda_status(self):
        store = ExperienceStore(self.home)
        exp = store.add(trigger="t", observation="o", correction="c", status=ExperienceStatus.ATIVA)
        invalidated = store.invalidate(exp.id)
        self.assertIsNotNone(invalidated)
        assert invalidated is not None
        self.assertEqual(invalidated.status, ExperienceStatus.INVALIDA)

    def test_invalidate_idempotent(self):
        store = ExperienceStore(self.home)
        exp = store.add(
            trigger="t", observation="o", correction="c", status=ExperienceStatus.INVALIDA
        )
        self.assertIsNone(store.invalidate(exp.id))

    def test_record_outcome_sucesso_aumenta_confianca(self):
        store = ExperienceStore(self.home)
        exp = store.add(
            trigger="t",
            observation="o",
            correction="c",
            confidence=0.5,
            status=ExperienceStatus.ATIVA,
        )
        store.record_outcome(exp.id, success=True)
        updated = store.get(exp.id)
        assert updated is not None
        self.assertEqual(updated.hits, 1)
        self.assertEqual(updated.successes, 1)
        self.assertGreater(updated.confidence, 0.5)

    def test_record_outcome_falha_diminui_confianca(self):
        store = ExperienceStore(self.home)
        exp = store.add(
            trigger="t",
            observation="o",
            correction="c",
            confidence=0.5,
            status=ExperienceStatus.ATIVA,
        )
        store.record_outcome(exp.id, success=False)
        updated = store.get(exp.id)
        assert updated is not None
        self.assertEqual(updated.hits, 1)
        self.assertEqual(updated.successes, 0)
        self.assertLess(updated.confidence, 0.5)

    def test_auto_invalida_com_muitas_falhas(self):
        store = ExperienceStore(self.home)
        exp = store.add(
            trigger="t",
            observation="o",
            correction="c",
            confidence=0.12,
            status=ExperienceStatus.ATIVA,
        )
        for _ in range(3):
            store.record_outcome(exp.id, success=False)
        updated = store.get(exp.id)
        # Se confidence ficou < 0.15 e hits >= 3, virou invalida
        if updated is not None:
            self.assertEqual(updated.status, ExperienceStatus.INVALIDA)

    def test_eviction_remove_invalidas_antigas(self):
        store = ExperienceStore(self.home, max_entries=5)
        for i in range(8):
            status = ExperienceStatus.INVALIDA if i < 4 else ExperienceStatus.ATIVA
            store.add(
                trigger=f"trigger {i}",
                observation=f"obs {i}",
                correction=f"corr {i}",
                status=status,
            )
        self.assertLessEqual(len(store.list()), 5)

    def test_history_limit(self):
        store = ExperienceStore(self.home, max_history=3)
        exp = store.add(trigger="t", observation="o", correction="c", status=ExperienceStatus.ATIVA)
        for _ in range(10):
            store.record_outcome(exp.id, success=True)
        updated = store.get(exp.id)
        assert updated is not None
        self.assertLessEqual(len(updated.history), 3)


class FindTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.store = ExperienceStore(self.home)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_find_vazio_quando_sem_ativas(self):
        self.assertEqual(self.store.find("qualquer coisa"), [])

    def test_find_candidata_nao_aparece(self):
        self.store.add(
            trigger="docker timeout",
            observation="timeout",
            correction="aumentar timeout",
            status=ExperienceStatus.CANDIDATA,
        )
        self.assertEqual(self.store.find("docker timeout"), [])

    def test_find_match_por_palavras(self):
        self.store.add(
            trigger="docker build erro 500",
            observation="image build falhou com 500",
            correction="usar --no-cache",
            status=ExperienceStatus.ATIVA,
            confidence=0.8,
        )
        results = self.store.find("docker build 500 erro")
        self.assertEqual(len(results), 1)
        self.assertIn("no-cache", results[0].correction)

    def test_find_limit(self):
        for i in range(5):
            self.store.add(
                trigger=f"problema X{i}",
                observation="obs",
                correction=f"solucao {i}",
                status=ExperienceStatus.ATIVA,
                confidence=0.9,
            )
        results = self.store.find("problema", limit=3)
        self.assertEqual(len(results), 3)

    def test_find_min_confidence(self):
        self.store.add(
            trigger="low confidence",
            observation="obs",
            correction="corr",
            status=ExperienceStatus.ATIVA,
            confidence=0.2,
        )
        self.assertEqual(self.store.find("low confidence", min_confidence=0.5), [])

    def test_find_sem_overlap_retorna_nada(self):
        self.store.add(
            trigger="bananas laranjas",
            observation="obs",
            correction="corr",
            status=ExperienceStatus.ATIVA,
            confidence=0.9,
        )
        self.assertEqual(self.store.find("computadores"), [])


class SuggestFromFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.store = ExperienceStore(self.home)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cria_candidata_quando_nada(self):
        exp = self.store.suggest_from_failure("docker build", "timeout 500")
        self.assertIsNotNone(exp)
        assert exp is not None
        self.assertEqual(exp.status, ExperienceStatus.CANDIDATA)
        self.assertIn("timeout", exp.observation)

    def test_nao_duplica_se_ja_existe_ativa(self):
        self.store.add(
            trigger="docker build timeout",
            observation="timeout",
            correction="aumentar timeout",
            status=ExperienceStatus.ATIVA,
        )
        result = self.store.suggest_from_failure("docker build", "timeout 500")
        self.assertIsNone(result)

    def test_nao_duplica_se_existe_candidata(self):
        self.store.add(
            trigger="docker build",
            observation="timeout",
            correction="pendente",
            status=ExperienceStatus.CANDIDATA,
        )
        result = self.store.suggest_from_failure("docker build", "timeout 500")
        self.assertIsNone(result)


class BuildExperienceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.store = ExperienceStore(self.home)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_none_when_disabled(self):
        self.store.add(
            trigger="test",
            observation="obs",
            correction="fix",
            status=ExperienceStatus.ATIVA,
        )
        self.assertIsNone(build_experience_context("test", self.home, enabled=False))

    def test_none_when_vazio(self):
        self.assertIsNone(build_experience_context("test", self.home))

    def test_none_when_somente_candidatas(self):
        self.store.add(
            trigger="test", observation="obs", correction="fix", status=ExperienceStatus.CANDIDATA
        )
        self.assertIsNone(build_experience_context("test", self.home))

    def test_inclui_correcao_para_ativa(self):
        self.store.add(
            trigger="telegram token invalido",
            observation="bot responde 401",
            correction="regenerar token no BotFather",
            status=ExperienceStatus.ATIVA,
            confidence=0.9,
        )
        ctx = build_experience_context("telegram token invalido", self.home)
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertIn(EXPERIENCE_BLOCK_HEADER, ctx)
        self.assertIn("regenerar token", ctx)
        self.assertIn("conf:0.9", ctx)

    def test_truncado_quando_muito_grande(self):
        self.store.add(
            trigger="loadtest pull image",
            observation="o",
            correction="c " * 500,
            status=ExperienceStatus.ATIVA,
        )
        ctx = build_experience_context("loadtest pull image", self.home, char_cap=100)
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertLessEqual(len(ctx), 120)  # header + truncation notice


class SecondRunImprovementTests(unittest.TestCase):
    """Prova central: 1ª execução falha → correção registrada → 2ª execução melhora."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_correcao_da_1a_ve_z_aparece_na_2a(self):
        store = ExperienceStore(self.home)
        query = "kairos telegram token"
        error = "Token do Telegram não configurado no cofre"

        # Passo 1-3: 1ª execução falha → captura candidata
        candidata = store.suggest_from_failure(query, error)
        self.assertIsNotNone(candidata)
        assert candidata is not None
        self.assertEqual(candidata.status, ExperienceStatus.CANDIDATA)

        # Sem correção: 2ª execução não tem contexto
        ctx_vazio = build_experience_context(query, self.home)
        self.assertIsNone(ctx_vazio)

        # Passo 4: humano registra a correção e confirma
        store.add(
            trigger=query,
            observation=error,
            correction="configurar o token em Integracoes (web) via credential endpoint",
            source="usuario",
            status=ExperienceStatus.ATIVA,
            confidence=0.9,
        )

        # Passo 7: 2ª execução com experiências habilitadas
        ctx_rebuild = build_experience_context(query, self.home)
        self.assertIsNotNone(ctx_rebuild)
        assert ctx_rebuild is not None
        self.assertIn("Integracoes", ctx_rebuild)
        self.assertIn("credential", ctx_rebuild)

        # Passo 8-9: resultado positivo → confiança sobe
        items = store.list(status=ExperienceStatus.ATIVA)
        self.assertGreaterEqual(len(items), 1)
        store.record_outcome(items[0].id, success=True)
        updated = store.get(items[0].id)
        assert updated is not None
        self.assertGreater(updated.confidence, 0.85)

    def test_sugestao_automatica_gera_candidata(self):
        store = ExperienceStore(self.home)
        self.assertEqual(store.list(), [])

        # Simula falha automática do canal de entrada
        candidata = store.suggest_from_failure("docker build", "connection refused")
        self.assertIsNotNone(candidata)
        assert candidata is not None
        self.assertEqual(candidata.status, ExperienceStatus.CANDIDATA)
        self.assertEqual(candidata.source, "aprendizado")

        # Sem correção, 2ª execução ainda sem contexto útil
        ctx = build_experience_context("docker build", self.home)
        self.assertIsNone(ctx)

        # Humano confirma
        store.confirm(candidata.id)
        # Agora com correção registrada separadamente
        store.add(
            trigger="docker build connection refused",
            observation="docker daemon não está rodando",
            correction="systemctl start docker",
            source="usuario",
            status=ExperienceStatus.ATIVA,
        )

        ctx_final = build_experience_context("docker build", self.home)
        self.assertIsNotNone(ctx_final)
        assert ctx_final is not None
        self.assertIn("systemctl start docker", ctx_final)

    def test_ciclo_completo_e_prevencao_de_regras(self):
        """A injeção é limitada e não altera o system prompt."""
        store = ExperienceStore(self.home)
        store.add(
            trigger="slack webhook",
            observation="webhook 403",
            correction="regenerar webhook URL",
            status=ExperienceStatus.ATIVA,
            confidence=0.85,
        )
        ctx = build_experience_context("slack webhook", self.home)
        self.assertIsNotNone(ctx)
        assert ctx is not None
        # Conteúdo é evidência de não-modificação: header explicita que é
        # referência e nunca altera regras; nada menciona system prompt.
        self.assertIn(EXPERIENCE_BLOCK_HEADER, ctx)
        self.assertIn("nao altere regras", ctx)
        self.assertNotIn("system prompt", ctx)


if __name__ == "__main__":
    unittest.main()
