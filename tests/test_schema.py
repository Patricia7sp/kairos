"""Tarefa 01 — o critério de pronto do plano de reconstrução.

"As 10 tabelas do ERD existem com tipos, CHECK constraints e foreign keys
corretos."

Escritos em ``unittest`` (stdlib) para rodarem sem dependência nenhuma;
``pytest`` executa esta suíte sem alteração.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kairos_state import connect, initialize_schema, read_schema_version
from kairos_state.schema import LEGACY_SHAPE_VERSION, SCHEMA_VERSION

# ERD §1: 10 tabelas + 3 índices FTS.
EXPECTED_TABLES = {
    "sessions",
    "messages",
    "system_prompts",
    "session_model_usage",
    "compression_locks",
    "session_turn_leases",
    "async_delegations",
    "gateway_routing",
    "gateway_hygiene_state",
    "state_meta",
}


class SchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self._tmp.name) / "state.db")
        initialize_schema(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def tables(self, conn=None) -> set[str]:
        conn = conn or self.db
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {r["name"] for r in rows}

    def columns(self, table) -> dict[str, sqlite3.Row]:
        return {r["name"]: r for r in self.db.execute(f"PRAGMA table_info({table})")}

    # --- forma ------------------------------------------------------------

    def test_as_dez_tabelas_do_erd_existem(self):
        self.assertLessEqual(EXPECTED_TABLES, self.tables())

    def test_sessions_tem_56_colunas(self):
        self.assertEqual(len(self.columns("sessions")), 56)

    def test_messages_tem_23_colunas(self):
        self.assertEqual(len(self.columns("messages")), 23)

    def test_session_model_usage_tem_pk_composta_de_6_colunas(self):
        pk = [r["name"] for r in self.db.execute("PRAGMA table_info(session_model_usage)") if r["pk"]]
        self.assertEqual(pk, [
            "session_id", "model", "billing_provider",
            "billing_base_url", "billing_mode", "task",
        ])

    def test_os_tres_indices_fts_existem(self):
        # messages_fts_cjk depende da extensão nativa (Tarefa 04) e é opcional.
        self.assertLessEqual({"messages_fts", "messages_fts_trigram"}, self.tables())

    def test_versao_do_schema_e_propria_do_kairos(self):
        # O Kairos nasce na versão 1 com a FORMA do v26 do legado; a versão
        # do legado fica registrada para rastreabilidade, não como a nossa.
        self.assertEqual(read_schema_version(self.db), SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 1)
        self.assertEqual(LEGACY_SHAPE_VERSION, 26)

    def test_inicializacao_e_idempotente(self):
        before = self.tables()
        initialize_schema(self.db)  # segunda passada não deve levantar
        self.assertEqual(self.tables(), before)
        self.assertEqual(read_schema_version(self.db), SCHEMA_VERSION)

    # --- pragmas ----------------------------------------------------------

    def test_wal_e_synchronous_full(self):
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(int(self.db.execute("PRAGMA synchronous").fetchone()[0]), 2)  # FULL

    def test_foreign_keys_ligado(self):
        # Sem este PRAGMA toda FK do schema seria decorativa: o SQLite o
        # mantém OFF por padrão, e é por conexão.
        self.assertEqual(int(self.db.execute("PRAGMA foreign_keys").fetchone()[0]), 1)

    # --- integridade referencial ------------------------------------------

    def test_fk_de_message_para_sessao_e_aplicada(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO messages(session_id, role, timestamp) "
                "VALUES ('inexistente','user',1.0)"
            )

    def test_fk_de_system_prompt_hash_e_aplicada(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO sessions(id, source, started_at, system_prompt_hash) "
                "VALUES ('s1','cli',1.0,'hash-que-nao-existe')"
            )

    def test_cascade_de_uso_por_sessao(self):
        self.db.execute("INSERT INTO sessions(id, source, started_at) VALUES ('s1','cli',1.0)")
        self.db.execute(
            "INSERT INTO session_model_usage(session_id, model, billing_provider, "
            "billing_base_url, billing_mode, task) VALUES ('s1','m','p','u','mode','t')"
        )
        self.db.execute("DELETE FROM sessions WHERE id='s1'")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM session_model_usage").fetchone()[0], 0
        )

    def test_gateway_routing_tem_fk_para_sessions(self):
        """A divergência deliberada da Tarefa 01.

        No legado o ``session_id`` ficava dentro de ``entry_json``, sem FK,
        e ``session_key`` órfão era possível — a invisibilidade que produziu
        o #64934. Aqui o banco recusa a órfã.
        """
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO gateway_routing(scope, session_key, session_id) "
                "VALUES ('global','chave','sessao-inexistente')"
            )

    def test_lease_de_turno_e_chaveado_pela_identidade_duravel(self):
        """ADR 004: o lock fica do lado do DADO, não da chave de coordenação."""
        fks = list(self.db.execute("PRAGMA foreign_key_list(session_turn_leases)"))
        self.assertEqual([(f["table"], f["to"]) for f in fks], [("sessions", "id")])

        self.db.execute("INSERT INTO sessions(id, source, started_at) VALUES ('s1','cli',1.0)")
        self.db.execute("INSERT INTO session_turn_leases(conversation_id, holder) VALUES ('s1','h1')")
        with self.assertRaises(sqlite3.IntegrityError):
            # Um segundo lease para a mesma conversa é impossível por construção.
            self.db.execute(
                "INSERT INTO session_turn_leases(conversation_id, holder) VALUES ('s1','h2')"
            )

    # --- visibilidades e sidecar ------------------------------------------

    def test_mensagem_nasce_ativa(self):
        self.db.execute("INSERT INTO sessions(id, source, started_at) VALUES ('s1','cli',1.0)")
        self.db.execute("INSERT INTO messages(session_id, role, timestamp) VALUES ('s1','user',1.0)")
        row = self.db.execute("SELECT active, compacted FROM messages").fetchone()
        self.assertEqual((row["active"], row["compacted"]), (1, 0))

    def test_sidecar_api_content_e_coluna_separada(self):
        """Lei 1: o que é exibido e o que vai para a API são campos distintos."""
        cols = self.columns("messages")
        self.assertIn("content", cols)
        self.assertIn("api_content", cols)

    # --- FTS ---------------------------------------------------------------

    def test_trigger_fts_indexa_mensagem_nova(self):
        self.db.execute("INSERT INTO sessions(id, source, started_at) VALUES ('s1','cli',1.0)")
        self.db.execute(
            "INSERT INTO messages(session_id, role, content, timestamp) "
            "VALUES ('s1','user','pesquisa por peixe-espada',1.0)"
        )
        hits = self.db.execute(
            "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'peixe'"
        ).fetchone()[0]
        self.assertEqual(hits, 1)

    def test_fts_trigram_ignora_saida_de_ferramenta(self):
        """As views excluem ``role='tool'``: saída de ferramenta domina o
        volume e polui o resultado de busca do usuário."""
        self.db.execute("INSERT INTO sessions(id, source, started_at) VALUES ('s1','cli',1.0)")
        self.db.execute(
            "INSERT INTO messages(session_id, role, content, timestamp) "
            "VALUES ('s1','tool','ruidoruidoruido',1.0)"
        )
        hits = self.db.execute(
            "SELECT COUNT(*) FROM messages_fts_trigram WHERE messages_fts_trigram MATCH 'ruido'"
        ).fetchone()[0]
        self.assertEqual(hits, 0)


if __name__ == "__main__":
    unittest.main()
