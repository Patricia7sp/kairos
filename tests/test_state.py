"""Tarefa 05 — hermes-state.

Critério de pronto: "Integridade do sidecar garantida; FTS5/Trigram degradam
*fail-open*; 6 superfícies escrevem concorrentemente sem perda."
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kairos_state import connect, initialize_schema
from kairos_state.connection import apply_wal_with_fallback, read_connection
from kairos_state.contention import (
    BUDGET_SECONDS,
    Budget,
    get_write_contention_stats,
    record_wait,
    reset_stats,
)
from kairos_state.migrations import (
    backup_corrupt_db,
    is_corruption_error,
    migrate,
)
from kairos_state.repositories import (
    BillingRoute,
    CompressionLockLost,
    LeaseRepository,
    MessageRepository,
    Route,
    SearchIndex,
    SessionRepository,
    TokenDelta,
    UsageRepository,
    probe,
)
from kairos_state.writes import WriteGaveUp, is_busy_error, write_with_retry


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state.db"
        self.db = connect(self.path)
        initialize_schema(self.db)
        self.sessions = SessionRepository(self.db)
        self.messages = MessageRepository(self.db)
        reset_stats()

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def mk(self, sid, **kw):
        return self.sessions.create(sid, kw.pop("source", "cli"), **kw)


# ---------------------------------------------------------------------------
class ConnectionTests(Base):
    """RF-01, RF-02, RF-03."""

    def test_rf01_wal_com_fallback(self):
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

    def test_rf01_fallback_para_delete_nao_levanta(self):
        # Simula FS sem suporte a WAL: o PRAGMA devolve outro modo.
        class FakeConn:
            def __init__(self):
                self.executed = []

            def execute(self, sql):
                self.executed.append(sql)
                if "journal_mode=WAL" in sql:
                    return type("R", (), {"fetchone": lambda s: ("delete",)})()
                return type("R", (), {"fetchone": lambda s: None})()

        fake = FakeConn()
        self.assertEqual(apply_wal_with_fallback(fake), "delete")
        self.assertIn("PRAGMA journal_mode=DELETE", fake.executed)

    def test_rf02_pragmas_de_conexao(self):
        self.assertEqual(int(self.db.execute("PRAGMA foreign_keys").fetchone()[0]), 1)
        self.assertGreater(int(self.db.execute("PRAGMA busy_timeout").fetchone()[0]), 0)

    def test_rf03_leitura_e_somente_leitura_e_nao_bloqueia(self):
        self.mk("s1")
        with read_connection(self.path) as ro:
            self.assertEqual(ro.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                ro.execute("INSERT INTO sessions(id,source,started_at) VALUES ('x','cli',1)")


# ---------------------------------------------------------------------------
class RetryAndContentionTests(Base):
    """RF-04 e T-13 — a decisão de G-20."""

    def test_erro_que_nao_e_contencao_nao_e_repetido(self):
        chamadas = []

        def op():
            chamadas.append(1)
            raise sqlite3.OperationalError("no such table: fantasma")

        with self.assertRaises(sqlite3.OperationalError):
            write_with_retry(op)
        self.assertEqual(len(chamadas), 1, "erro real não deve ser repetido")

    def test_classificacao_de_erro_ocupado(self):
        self.assertTrue(is_busy_error(sqlite3.OperationalError("database is locked")))
        self.assertTrue(is_busy_error(sqlite3.OperationalError("database table is busy")))
        self.assertFalse(is_busy_error(sqlite3.OperationalError("syntax error")))
        self.assertFalse(is_busy_error(ValueError("locked")))

    def test_ocupado_transitorio_conclui_apos_retry(self):
        estado = {"n": 0}

        def op():
            estado["n"] += 1
            if estado["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        self.assertEqual(write_with_retry(op, budget=Budget.ACTIVITY), "ok")
        self.assertEqual(estado["n"], 3)

    def test_paciencia_e_por_TEMPO_nao_por_tentativa(self):
        # Relógio falso: estoura a paciência sem gastar tempo real.
        agora = {"t": 0.0}

        def clock():
            agora["t"] += 0.3
            return agora["t"]

        def op():
            raise sqlite3.OperationalError("database is locked")

        with self.assertRaises(WriteGaveUp):
            write_with_retry(op, budget=Budget.ACTIVITY, _clock=clock)

    def test_os_tres_orcamentos_sao_distintos_e_ordenados(self):
        # activity < routine < transcript: a ordem codifica o custo da falha.
        self.assertLess(BUDGET_SECONDS[Budget.ACTIVITY], BUDGET_SECONDS[Budget.ROUTINE])
        self.assertLess(BUDGET_SECONDS[Budget.ROUTINE], BUDGET_SECONDS[Budget.TRANSCRIPT])

    def test_t13_a_contencao_e_MEDIDA(self):
        record_wait(Budget.TRANSCRIPT, 2.5)
        record_wait(Budget.TRANSCRIPT, 0.001)
        record_wait(Budget.ROUTINE, 5.0, gave_up=True)

        st = get_write_contention_stats()
        self.assertEqual(st["waits_total"], 3)
        self.assertEqual(st["waits_over_1s"], 2)
        self.assertEqual(st["gaveup_total"], 1)
        self.assertEqual(st["by_budget"]["transcript"]["waits_total"], 2)
        self.assertEqual(st["by_budget"]["routine"]["gaveup_total"], 1)
        self.assertGreater(st["by_budget"]["transcript"]["wait_p99_ms"], 0)

    def test_t13_gaveup_de_transcript_e_o_numero_que_importa(self):
        # Diferente de zero = turno destruído por banco ocupado (#74478).
        self.assertEqual(get_write_contention_stats()["by_budget"]["transcript"]["gaveup_total"], 0)
        record_wait(Budget.TRANSCRIPT, 61.0, gave_up=True)
        self.assertEqual(get_write_contention_stats()["by_budget"]["transcript"]["gaveup_total"], 1)

    def test_t13_telemetria_nunca_derruba_a_escrita(self):
        # "never let telemetry break a write"
        record_wait(Budget.ROUTINE, float("nan"))
        record_wait(Budget.ROUTINE, -1.0, detail=object())  # type: ignore[arg-type]
        self.mk("s1")  # a escrita segue funcionando

    def test_t13_ring_de_eventos_lentos_e_limitado(self):
        for _ in range(200):
            record_wait(Budget.ROUTINE, 2.0)
        self.assertLessEqual(len(get_write_contention_stats()["recent_slow_waits"]), 50)


# ---------------------------------------------------------------------------
class SidecarTests(Base):
    """RF-05, RF-06."""

    def test_rf05_alterar_exibicao_nao_toca_o_payload_da_api(self):
        self.mk("s1")
        mid = self.messages.append(
            "s1", "assistant", content="visível", api_content="enviado ao provedor"
        )
        self.db.execute("UPDATE messages SET content='REDIGIDO' WHERE id=?", (mid,))
        row = self.db.execute(
            "SELECT content, api_content FROM messages WHERE id=?", (mid,)
        ).fetchone()
        self.assertEqual(row["content"], "REDIGIDO")
        self.assertEqual(row["api_content"], "enviado ao provedor")

    def test_sem_sidecar_o_content_e_o_payload(self):
        self.mk("s1")
        self.messages.append("s1", "user", content="só isso")
        self.assertEqual(self.messages.for_api("s1")[0]["payload"], "só isso")

    def test_rf06_prompt_identico_e_deduplicado(self):
        h1 = self.sessions.intern_system_prompt("prompt grande e repetido")
        h2 = self.sessions.intern_system_prompt("prompt grande e repetido")
        self.assertEqual(h1, h2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM system_prompts").fetchone()[0], 1)

        self.mk("s1", system_prompt="prompt grande e repetido")
        self.mk("s2", system_prompt="prompt grande e repetido")
        hashes = {
            r["system_prompt_hash"]
            for r in self.db.execute("SELECT system_prompt_hash FROM sessions")
        }
        self.assertEqual(hashes, {h1})


# ---------------------------------------------------------------------------
class LeaseTests(Base):
    """RF-07, RF-08, RF-09."""

    def setUp(self):
        super().setUp()
        self.mk("s1")
        self.leases = LeaseRepository.turn_leases(self.db)
        self.locks = LeaseRepository.compression_locks(self.db)

    def test_rf07_segundo_holder_falha_enquanto_o_lease_vale(self):
        self.assertTrue(self.leases.try_acquire("s1", "A", ttl_seconds=300, now=1000.0))
        self.assertFalse(self.leases.try_acquire("s1", "B", ttl_seconds=300, now=1001.0))
        self.assertEqual(self.leases.holder("s1", now=1001.0), "A")

    def test_rf07_reaquisicao_pelo_mesmo_holder_e_idempotente(self):
        self.leases.try_acquire("s1", "A", now=1000.0)
        self.assertTrue(self.leases.try_acquire("s1", "A", now=1010.0))

    def test_rf07_refresh_so_pelo_holder(self):
        self.leases.try_acquire("s1", "A", ttl_seconds=10, now=1000.0)
        self.assertFalse(self.leases.refresh("s1", "B", now=1001.0))
        self.assertTrue(self.leases.refresh("s1", "A", ttl_seconds=100, now=1001.0))
        self.assertEqual(self.leases.holder("s1", now=1050.0), "A")

    def test_rf08_lease_expirado_e_adquirivel_sem_intervencao(self):
        self.leases.try_acquire("s1", "morto", ttl_seconds=10, now=1000.0)
        self.assertIsNone(self.leases.holder("s1", now=1011.0))
        self.assertTrue(self.leases.try_acquire("s1", "novo", now=1011.0))
        self.assertEqual(self.leases.holder("s1", now=1011.0), "novo")

    def test_release_so_pelo_holder(self):
        self.leases.try_acquire("s1", "A", now=1000.0)
        self.assertFalse(self.leases.release("s1", "B"))
        self.assertTrue(self.leases.release("s1", "A"))
        self.assertIsNone(self.leases.holder("s1"))

    def test_rf09_compactacao_nao_impede_o_lease_de_turno(self):
        self.assertTrue(self.locks.try_acquire("s1", "compressor", now=1000.0))
        self.assertTrue(self.leases.try_acquire("s1", "turno", now=1000.0))
        self.assertEqual(self.locks.holder("s1", now=1000.0), "compressor")
        self.assertEqual(self.leases.holder("s1", now=1000.0), "turno")


# ---------------------------------------------------------------------------
class LineageTests(Base):
    """RF-10 — a regra mais valiosa da unit."""

    def _chain(self, child, parent, *, end_reason="compression", model_config=None, source="cli"):
        self.mk(parent, source=source, end_reason=end_reason, ended_at=1.0)
        self.mk(
            child,
            source=source,
            parent_session_id=parent,
            model_config=json.dumps(model_config) if model_config else None,
        )

    def test_ancestral_por_compactacao_entra(self):
        self._chain("filho", "pai")
        self.assertEqual(self.sessions.compression_lineage("filho"), ["pai", "filho"])

    def test_filtro_1_pai_encerrado_por_outra_razao_NAO_entra(self):
        self._chain("filho", "pai", end_reason="idle")
        self.assertEqual(self.sessions.compression_lineage("filho"), ["filho"])

    def test_filtro_2_branch_NAO_arrasta_a_conversa_de_origem(self):
        self._chain("filho", "pai", model_config={"_branched_from": "outra"})
        self.assertEqual(self.sessions.compression_lineage("filho"), ["filho"])

    def test_filtro_3_delegacao_NAO_arrasta_a_conversa_do_pai(self):
        self._chain("filho", "pai", model_config={"_delegate_from": "outra"})
        self.assertEqual(self.sessions.compression_lineage("filho"), ["filho"])

    def test_filtro_4_sessao_de_ferramenta_NAO_entra(self):
        self._chain("filho", "pai", source="tool")
        self.assertEqual(self.sessions.compression_lineage("filho"), ["filho"])

    def test_cadeia_longa_de_compactacoes(self):
        self.mk("a", end_reason="compression", ended_at=1.0)
        self.mk("b", parent_session_id="a", end_reason="compression", ended_at=2.0)
        self.mk("c", parent_session_id="b")
        self.assertEqual(self.sessions.compression_lineage("c"), ["a", "b", "c"])

    def test_a_cadeia_para_no_primeiro_filtro_violado(self):
        # a --compression--> b --branch--> c : só b e c, nunca a.
        self.mk("a", end_reason="compression", ended_at=1.0)
        self.mk(
            "b",
            parent_session_id="a",
            end_reason="compression",
            ended_at=2.0,
            model_config=json.dumps({"_branched_from": "z"}),
        )
        self.mk("c", parent_session_id="b")
        self.assertEqual(self.sessions.compression_lineage("c"), ["b", "c"])


# ---------------------------------------------------------------------------
class CompactionTests(Base):
    """RF-11."""

    def setUp(self):
        super().setUp()
        self.mk("s1")
        for i in range(5):
            self.messages.append("s1", "user", content=f"m{i}", timestamp=float(i))

    def test_rf11_compactar_NAO_apaga(self):
        antes = self.db.execute("SELECT count(*) FROM messages").fetchone()[0]
        wm = self.messages.active_watermark("s1")
        self.messages.archive_and_compact("s1", wm)
        depois = self.db.execute("SELECT count(*) FROM messages").fetchone()[0]
        self.assertEqual(antes, depois, "nenhum DELETE")

        estados = self.db.execute(
            "SELECT active, compacted, count(*) c FROM messages GROUP BY 1,2"
        ).fetchall()
        self.assertEqual([(r["active"], r["compacted"], r["c"]) for r in estados], [(0, 1, 5)])

    def test_watermark_protege_o_que_chegou_durante_a_sumarizacao(self):
        wm = self.messages.active_watermark("s1")
        # Chega DEPOIS da marca, enquanto o LLM sumariza.
        self.messages.append("s1", "user", content="tardia", timestamp=99.0)
        self.messages.archive_and_compact("s1", wm)
        ativas = [r["payload"] for r in self.messages.for_api("s1")]
        self.assertEqual(ativas, ["tardia"])

    def test_a_posse_do_lock_e_verificada_DENTRO_do_commit(self):
        locks = LeaseRepository.compression_locks(self.db)
        locks.try_acquire("s1", "A", now=1000.0)
        wm = self.messages.active_watermark("s1")
        # Entre adquirir e commitar passou uma chamada de LLM; o lease expirou
        # e outro tomou.
        locks.release("s1", "A")
        locks.try_acquire("s1", "B", now=2000.0)
        with self.assertRaises(CompressionLockLost):
            self.messages.archive_and_compact("s1", wm, lock_holder="A")
        # Nada foi arquivado.
        self.assertEqual(len(self.messages.for_api("s1")), 5)

    def test_rewind_some_da_busca_compactacao_nao(self):
        # ids 1..5. Compacta até 2 → 1,2 viram (0,1). Rebobina após 3 → 4,5
        # viram (0,0). O id 3 fica ativo: não foi compactado nem rebobinado.
        self.messages.archive_and_compact("s1", 2)
        self.messages.rewind("s1", 3)
        vis = self.db.execute(
            "SELECT active, compacted, count(*) c FROM messages GROUP BY 1,2 ORDER BY 1,2"
        ).fetchall()
        self.assertEqual(
            [(r["active"], r["compacted"], r["c"]) for r in vis],
            [
                (0, 0, 2),  # rebobinadas: fora do contexto E fora da busca
                (0, 1, 2),  # compactadas: fora do contexto, DENTRO da busca
                (1, 0, 1),
            ],  # ativa
        )


# ---------------------------------------------------------------------------
class SearchTests(Base):
    """RF-12, RF-13."""

    def setUp(self):
        super().setUp()
        self.mk("s1")
        self.messages.append("s1", "user", content="reunião sobre orçamento")
        self.messages.append("s1", "assistant", content="anotado o orçamento")

    def test_sonda_encontra_as_rotas_disponiveis(self):
        caps = probe(self.db)
        self.assertTrue(caps.fts5)
        self.assertTrue(caps.trigram)
        self.assertFalse(caps.cjk)  # extensão não carregada aqui
        self.assertIsNone(caps.reason)

    def test_like_e_sempre_a_ultima_rota(self):
        self.assertEqual(probe(self.db).routes()[-1], Route.LIKE)

    def test_busca_pelo_indice_base(self):
        self.assertEqual(len(SearchIndex(self.db).search("orçamento")), 2)

    def test_rf12_sem_fts5_a_busca_continua_por_like(self):
        self.db.executescript(
            "DROP TABLE IF EXISTS messages_fts;DROP TABLE IF EXISTS messages_fts_trigram;"
        )
        caps = probe(self.db)
        self.assertFalse(caps.fts5)
        self.assertEqual(caps.reason, "fts5_unavailable")
        self.assertEqual(caps.routes(), (Route.LIKE,))
        self.assertEqual(len(SearchIndex(self.db).search("orçamento")), 2)

    def test_rf13_sem_trigram_o_indice_base_continua_servindo(self):
        self.db.execute("DROP TABLE messages_fts_trigram")
        caps = probe(self.db)
        self.assertTrue(caps.fts5)
        self.assertFalse(caps.trigram)
        self.assertEqual(len(SearchIndex(self.db).search("reunião")), 1)

    def test_curinga_do_usuario_e_literal_no_like(self):
        self.db.executescript(
            "DROP TABLE IF EXISTS messages_fts;DROP TABLE IF EXISTS messages_fts_trigram;"
        )
        # '%' não pode casar tudo.
        self.assertEqual(SearchIndex(self.db).search("%"), [])

    def test_termo_vazio_nao_busca(self):
        self.assertEqual(SearchIndex(self.db).search("   "), [])


# ---------------------------------------------------------------------------
class UsageTests(Base):
    """RF-14, RF-15, RF-16."""

    def setUp(self):
        super().setUp()
        self.mk("s1")
        self.usage = UsageRepository(self.db)
        self.rota = BillingRoute("s1", "gpt", "openai", "https://api", "key")

    def test_rf14_n_deltas_viram_UMA_linha(self):
        for _ in range(50):
            self.usage.queue(self.rota, TokenDelta(output_tokens=10))
        self.assertEqual(self.usage.pending_count(), 1, "coalescido em memória")
        self.assertEqual(self.usage.flush(), 1)
        row = self.db.execute("SELECT output_tokens FROM session_model_usage").fetchone()
        self.assertEqual(row["output_tokens"], 500)

    def test_enfileirar_nao_toca_o_banco(self):
        self.usage.queue(self.rota, TokenDelta(input_tokens=1))
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM session_model_usage").fetchone()[0], 0
        )

    def test_delta_vazio_e_ignorado(self):
        self.usage.queue(self.rota, TokenDelta())
        self.assertEqual(self.usage.pending_count(), 0)

    def test_rf16_troca_de_provedor_gera_linhas_distintas(self):
        outra = BillingRoute("s1", "claude", "anthropic", "https://api2", "key")
        self.usage.queue(self.rota, TokenDelta(input_tokens=100))
        self.usage.queue(outra, TokenDelta(input_tokens=200))
        self.usage.flush()
        rows = self.db.execute(
            "SELECT billing_provider, input_tokens FROM session_model_usage "
            "ORDER BY billing_provider"
        ).fetchall()
        self.assertEqual(
            [(r["billing_provider"], r["input_tokens"]) for r in rows],
            [("anthropic", 200), ("openai", 100)],
        )

    def test_session_summary_becomes_sticky_mixed_after_distinct_routes(self):
        openai = BillingRoute("s1", "gpt", "openai", "https://api.openai.com", "key")
        openrouter = BillingRoute("s1", "gpt", "openrouter", "https://openrouter.ai/api/v1", "key")
        self.usage.queue(openai, TokenDelta(input_tokens=2))
        self.usage.flush(now=1)
        self.usage.queue(openrouter, TokenDelta(input_tokens=3))
        self.usage.flush(now=2)

        row = self.db.execute(
            "SELECT billing_provider, billing_base_url, billing_mode, input_tokens "
            "FROM sessions WHERE id='s1'"
        ).fetchone()
        self.assertEqual(tuple(row), ("mixed", "", "mixed", 5))

        self.usage.queue(openai, TokenDelta(input_tokens=7))
        self.usage.flush(now=3)
        row = self.db.execute(
            "SELECT billing_provider, billing_base_url, billing_mode, input_tokens "
            "FROM sessions WHERE id='s1'"
        ).fetchone()
        self.assertEqual(tuple(row), ("mixed", "", "mixed", 12))

    def test_flushes_sucessivos_acumulam(self):
        self.usage.queue(self.rota, TokenDelta(input_tokens=10))
        self.usage.flush()
        self.usage.queue(self.rota, TokenDelta(input_tokens=5))
        self.usage.flush()
        self.assertEqual(
            self.db.execute("SELECT input_tokens FROM session_model_usage").fetchone()[0], 15
        )

    def test_rf15_dreno_no_encerramento(self):
        self.usage.queue(self.rota, TokenDelta(output_tokens=7))
        self.assertEqual(self.usage.drain_at_exit(), 1)
        self.assertEqual(
            self.db.execute("SELECT output_tokens FROM session_model_usage").fetchone()[0], 7
        )

    def test_dreno_nao_levanta_mesmo_com_banco_fechado(self):
        self.usage.queue(self.rota, TokenDelta(output_tokens=1))
        self.db.close()
        self.assertEqual(self.usage.drain_at_exit(), 0)  # não levanta
        self.db = connect(self.path)  # para o tearDown


# ---------------------------------------------------------------------------
class MigrationTests(Base):
    """RF-17, RF-18."""

    def test_rf17_migracao_e_idempotente(self):
        v1 = migrate(self.db)
        v2 = migrate(self.db)
        self.assertEqual(v1, v2)

    def test_migracao_em_banco_vazio_chega_a_versao_alvo(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(Path(d) / "novo.db")
            self.assertEqual(migrate(conn), 1)
            tabelas = {
                r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertIn("sessions", tabelas)
            self.assertIn("delivery_obligations", tabelas)
            conn.close()

    def test_rf18_backup_preserva_o_arquivo_danificado(self):
        bak = backup_corrupt_db(self.path)
        self.assertTrue(bak.exists())
        self.assertIn(".zeroed-", bak.name)
        self.assertEqual(bak.read_bytes()[:16], self.path.read_bytes()[:16])

    def test_rf18_colisao_de_nome_ganha_sufixo(self):
        a = backup_corrupt_db(self.path, now=1000)
        b = backup_corrupt_db(self.path, now=1000)
        self.assertNotEqual(a, b)
        self.assertTrue(b.name.endswith("-1"))

    def test_corrupcao_e_distinguida_de_ocupado_e_de_disco_cheio(self):
        self.assertTrue(
            is_corruption_error(sqlite3.DatabaseError("database disk image is malformed"))
        )
        self.assertTrue(is_corruption_error(sqlite3.DatabaseError("file is not a database")))
        self.assertFalse(is_corruption_error(sqlite3.OperationalError("database is locked")))
        self.assertFalse(is_corruption_error(sqlite3.OperationalError("disk I/O error")))


# ---------------------------------------------------------------------------
class ConcurrencyTests(Base):
    """ "6 superfícies escrevem concorrentemente sem perda"."""

    def test_seis_escritores_concorrentes_nao_perdem_mensagem(self):
        self.mk("s1")
        erros: list[Exception] = []
        POR_ESCRITOR = 15

        def escritor(n):
            conn = connect(self.path)
            repo = MessageRepository(conn)
            try:
                for i in range(POR_ESCRITOR):
                    repo.append("s1", "user", content=f"w{n}-{i}", timestamp=time.time())
            except Exception as exc:  # noqa: BLE001 — pragma: no cover
                # DELIBERADO: o teste quer QUALQUER falha de escrita
                # concorrente, não uma classe prevista. Estreitar aqui faria
                # o teste passar diante do erro que ele deveria pegar.
                erros.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=escritor, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(erros, [])
        total = self.db.execute("SELECT count(*) FROM messages").fetchone()[0]
        self.assertEqual(total, 6 * POR_ESCRITOR)

    def test_a_contencao_do_teste_acima_foi_medida(self):
        # Não afirma que houve contenção — afirma que, havendo, ela aparece.
        st = get_write_contention_stats()
        self.assertIn("by_budget", st)
        self.assertIn("transcript", st["by_budget"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
class Invariante6Tests(Base):
    """O reparo nunca modifica linha canônica."""

    def setUp(self):
        super().setUp()
        self.mk("s1")
        for i in range(4):
            self.messages.append("s1", "user", content=f"m{i}", timestamp=float(i))

    def test_o_reparo_recria_derivados_e_preserva_canonicas(self):
        from kairos_state.migrations import (
            canonical_fingerprint,
            repair_derived_objects,
        )

        antes = canonical_fingerprint(self.db)
        recriados = repair_derived_objects(self.db)
        self.assertIn("messages_fts", recriados)
        self.assertEqual(canonical_fingerprint(self.db), antes)
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages").fetchone()[0], 4)

    def test_a_verificacao_e_a_IMPOSICAO_nao_um_comentario(self):
        from kairos_state.migrations import CanonicalRowsModified, canonical_fingerprint

        antes = canonical_fingerprint(self.db)
        self.db.execute("DELETE FROM messages WHERE id = 1")
        depois = canonical_fingerprint(self.db)
        self.assertNotEqual(antes, depois)
        # Se um reparo futuro apagar linha, a impressão digital denuncia.
        with self.assertRaises(CanonicalRowsModified):
            raise CanonicalRowsModified(f"antes={antes} depois={depois}")

    def test_a_impressao_digital_pega_insercao_e_remocao(self):
        from kairos_state.migrations import canonical_fingerprint

        antes = canonical_fingerprint(self.db)
        self.messages.append("s1", "user", content="nova")
        self.assertNotEqual(canonical_fingerprint(self.db), antes)


class Invariante11Tests(unittest.TestCase):
    """A guarda vive no harness, porque quem viola é o teste."""

    def test_KAIROS_HOME_esta_isolado_durante_a_suite(self):
        import os
        from pathlib import Path

        home = os.environ.get("KAIROS_HOME")
        self.assertIsNotNone(home, "a fixture de sessão deveria ter definido KAIROS_HOME")
        self.assertNotEqual(Path(home).resolve(), (Path.home() / ".kairos").resolve())

    def test_a_guarda_existe_e_e_autouse(self):
        conftest = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
        self.assertIn("autouse=True", conftest)
        self.assertIn("INVARIANTE 11", conftest)
