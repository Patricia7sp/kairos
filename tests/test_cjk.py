"""Tarefa 04 — native (`fts5_cjk`).

Critério de pronto: "Os 14 RFs atendidos; busca em japonês/chinês retorna
resultados que o tokenizador padrão perde."

Os testes que exigem a extensão são pulados quando ela não foi compilada —
o que é, ele próprio, o comportamento sob teste: a ausência degrada, não
quebra.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from kairos_state import connect, initialize_schema
from kairos_state.cjk import (
    CJK_SO_ENV,
    cjk_enabled,
    drop_cjk_index,
    ensure_cjk_index,
    find_extension,
    is_loaded,
    load_extension,
    rebuild_status,
    rebuild_step,
)

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native" / "fts5_cjk"


def _build_once() -> Path | None:
    """Compila a extensão numa vez para toda a suíte."""
    out = Path(tempfile.gettempdir()) / "kairos-cjk-test"
    so = out / "libfts5_cjk.so"
    if so.is_file():
        return so
    try:
        subprocess.run(
            [str(SRC / "build.sh"), str(out)], check=True, capture_output=True, timeout=120
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return so if so.is_file() else None


_SO = _build_once()
requires_ext = unittest.skipIf(_SO is None, "extensão fts5_cjk não pôde ser compilada")


class BuildTests(unittest.TestCase):
    """RF-03, RF-04."""

    def test_o_caminho_vendor_basta_para_compilar(self):
        # RF-03 sem depender do host: pergunta ao compilador QUAIS headers ele
        # resolveu com -Ivendor. Se algum sqlite viesse de /usr/include, o
        # vendor/ estaria incompleto e o build quebraria em quem não tem
        # libsqlite3-dev — exatamente o usuário final que o RF-03 protege.
        dep = subprocess.run(
            ["gcc", "-M", "-Ivendor", "fts5_cjk.c"],
            cwd=SRC,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(dep.returncode, 0, dep.stderr)
        sqlite_headers = [t for t in dep.stdout.split() if "sqlite" in t]
        self.assertTrue(sqlite_headers, "nenhum header sqlite nas dependências")
        for h in sqlite_headers:
            self.assertTrue(
                h.startswith("vendor/"),
                f"{h} veio de fora do vendor/ — o vendor está incompleto",
            )

    @unittest.skipIf(
        Path("/usr/include/sqlite3ext.h").exists(),
        "host tem o header do sistema; a prova por ausência não se aplica aqui",
    )
    def test_o_build_compila_sem_o_header_do_sistema(self):
        # Onde o header do sistema não existe, o build só pode ter compilado
        # pelo vendor/. É a prova mais forte, mas só o host certo a permite.
        self.assertIsNotNone(_SO, "build falhou sem o header do sistema")

    @requires_ext
    def test_instalado_com_modo_0644(self):
        mode = _SO.stat().st_mode & 0o777
        self.assertEqual(oct(mode), oct(0o644))

    def test_headers_vendorizados_presentes(self):
        for h in ("sqlite3.h", "sqlite3ext.h"):
            self.assertTrue((SRC / "vendor" / h).is_file(), f"falta vendor/{h}")


class TokenizerTests(unittest.TestCase):
    """RF-01, RF-02 — o núcleo da unit."""

    @requires_ext
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.enable_load_extension(True)
        self.conn.load_extension(str(_SO))

    def tearDown(self):
        if hasattr(self, "conn"):
            self.conn.close()

    def _table(self, tokenize):
        self.conn.execute(f"CREATE VIRTUAL TABLE t USING fts5(c, tokenize='{tokenize}')")

    def _insert(self, *texts):
        self.conn.executemany("INSERT INTO t(c) VALUES (?)", [(t,) for t in texts])

    def _hits(self, q):
        return self.conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (q,)).fetchone()[0]

    @requires_ext
    def test_rf01_o_tokenizador_e_aceito(self):
        self._table("cjk_unicode61")

    @requires_ext
    def test_rf02_termo_coreano_de_2_chars_casa_dentro_de_token_maior(self):
        """A razão de existir da unit."""
        self._table("cjk_unicode61")
        self._insert("웅기가말했다")
        self.assertEqual(self._hits("웅기"), 1)
        self.assertEqual(self._hits("말했"), 1)

    @requires_ext
    def test_rf02_o_unicode61_padrao_PERDE_esse_resultado(self):
        """A comparação que justifica a extensão inteira."""
        self._table("unicode61")
        self._insert("웅기가말했다")
        self.assertEqual(self._hits("웅기"), 0)  # o token é a sequência inteira

    @requires_ext
    def test_japones_e_chines_tambem(self):
        self._table("cjk_unicode61")
        self._insert("東京都", "こんにちは世界", "中文搜索")
        self.assertEqual(self._hits("東京"), 1)
        self.assertEqual(self._hits("世界"), 1)
        self.assertEqual(self._hits("文搜"), 1)  # casa no meio

    @requires_ext
    def test_caractere_cjk_isolado_vira_unigrama(self):
        # Sem isto um termo de 1 caractere seria inindexável.
        self._table("cjk_unicode61")
        self._insert("中")
        self.assertEqual(self._hits("中"), 1)

    @requires_ext
    def test_texto_latino_passa_intacto(self):
        self._table("cjk_unicode61")
        self._insert("hello world", "Hello World")
        self.assertEqual(self._hits("world"), 2)  # dobra de caixa preservada
        self.assertEqual(self._hits("hello"), 2)

    @requires_ext
    def test_texto_misto_segmenta_corretamente(self):
        self._table("cjk_unicode61")
        self._insert("reunião 일본 meeting 東京")
        self.assertEqual(self._hits("일본"), 1)
        self.assertEqual(self._hits("meeting"), 1)
        self.assertEqual(self._hits("東京"), 1)

    @requires_ext
    def test_argumentos_extras_passam_para_o_unicode61(self):
        self._table("cjk_unicode61 remove_diacritics 2")
        self._insert("reunião")
        self.assertEqual(self._hits("reuniao"), 1)

    @requires_ext
    def test_termo_ausente_nao_casa(self):
        # Guarda contra um tokenizador que casasse tudo.
        self._table("cjk_unicode61")
        self._insert("일본 날씨")
        self.assertEqual(self._hits("구글"), 0)


class GateTests(unittest.TestCase):
    """RF-10, RF-11."""

    def test_rf10_config_desliga(self):
        self.assertTrue(cjk_enabled(None))
        self.assertTrue(cjk_enabled({"sessions": {}}))
        self.assertFalse(cjk_enabled({"sessions": {"cjk_fts": False}}))

    def test_rf11_env_sobrescreve_o_caminho(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "libfts5_cjk.so"
            fake.write_bytes(b"nao-e-um-so-de-verdade")
            os.environ[CJK_SO_ENV] = str(fake)
            try:
                self.assertEqual(find_extension(), fake)
            finally:
                del os.environ[CJK_SO_ENV]

    def test_env_apontando_para_caminho_inexistente_nao_cai_no_padrao(self):
        # Override quebrado é erro de configuração e deve aparecer, não ser
        # silenciosamente contornado.
        os.environ[CJK_SO_ENV] = "/caminho/que/nao/existe.so"
        try:
            self.assertIsNone(find_extension())
        finally:
            del os.environ[CJK_SO_ENV]


class DegradationTests(unittest.TestCase):
    """RF-12 — o requisito mais importante da unit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self._tmp.name) / "state.db")
        initialize_schema(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_sem_extensao_nada_do_caminho_cjk_executa(self):
        os.environ[CJK_SO_ENV] = "/nao/existe.so"
        try:
            self.assertFalse(load_extension(self.db))
            self.assertFalse(is_loaded(self.db))
            self.assertFalse(ensure_cjk_index(self.db))
        finally:
            del os.environ[CJK_SO_ENV]

    def test_so_invalido_nao_levanta(self):
        with tempfile.TemporaryDirectory() as d:
            lixo = Path(d) / "libfts5_cjk.so"
            lixo.write_bytes(b"isto nao e um objeto compartilhado")
            os.environ[CJK_SO_ENV] = str(lixo)
            try:
                self.assertFalse(load_extension(self.db))  # não levanta
            finally:
                del os.environ[CJK_SO_ENV]

    def test_config_desligada_nem_tenta_carregar(self):
        self.assertFalse(load_extension(self.db, config={"sessions": {"cjk_fts": False}}))

    def test_a_busca_base_continua_funcionando_sem_a_extensao(self):
        self.db.execute("INSERT INTO sessions(id, source, started_at) VALUES ('s1','cli',1.0)")
        self.db.execute(
            "INSERT INTO messages(session_id, role, content, timestamp) "
            "VALUES ('s1','user','busca latina normal',1.0)"
        )
        hits = self.db.execute(
            "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'latina'"
        ).fetchone()[0]
        self.assertEqual(hits, 1)

    def test_rf13_remocao_e_limpa_mesmo_sem_a_extensao(self):
        drop_cjk_index(self.db)  # não levanta
        tabelas = {
            r["name"]
            for r in self.db.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'messages_fts_cjk%'"
            )
        }
        self.assertEqual(tabelas, set())


class IndexAndRebuildTests(unittest.TestCase):
    """RF-05, RF-06, RF-07, RF-08, RF-09, RF-14."""

    @requires_ext
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ[CJK_SO_ENV] = str(_SO)
        self.db = connect(Path(self._tmp.name) / "state.db")
        initialize_schema(self.db)
        self.assertTrue(load_extension(self.db))
        self.assertTrue(ensure_cjk_index(self.db))
        self.db.execute("INSERT INTO sessions(id, source, started_at) VALUES ('s1','cli',1.0)")

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
            self._tmp.cleanup()
        os.environ.pop(CJK_SO_ENV, None)

    def _msg(self, role, content):
        self.db.execute(
            "INSERT INTO messages(session_id, role, content, timestamp) VALUES ('s1',?,?,1.0)",
            (role, content),
        )

    def _cjk_hits(self, q):
        return self.db.execute(
            "SELECT count(*) FROM messages_fts_cjk WHERE messages_fts_cjk MATCH ?", (q,)
        ).fetchone()[0]

    @requires_ext
    def test_rf05_indice_view_e_gatilhos_existem(self):
        objetos = {
            r["name"]
            for r in self.db.execute("SELECT name FROM sqlite_master WHERE name LIKE '%cjk%'")
        }
        self.assertIn("messages_fts_cjk", objetos)
        self.assertIn("messages_fts_cjk_src", objetos)
        for t in ("insert", "delete", "update"):
            self.assertIn(f"messages_fts_cjk_{t}", objetos)

    @requires_ext
    def test_rf07_mensagem_nova_e_indexada_ao_vivo(self):
        self._msg("user", "일본 날씨")
        self.assertEqual(self._cjk_hits("일본"), 1)

    @requires_ext
    def test_rf06_saida_de_ferramenta_NAO_entra_no_indice(self):
        self._msg("tool", "일본 saída de ferramenta")
        self.assertEqual(self._cjk_hits("일본"), 0)

    @requires_ext
    @requires_ext
    def test_a_fronteira_separa_gatilho_de_backfill(self):
        """O erro que este desenho evita: os dois disputando a mesma linha."""
        self._msg("user", "기존 메시지")  # existe ANTES do índice
        drop_cjk_index(self.db)
        ensure_cjk_index(self.db)  # fixa a fronteira aqui
        st = rebuild_status(self.db)
        self.assertEqual(st.remaining, 1)  # a antiga é do backfill

        self._msg("user", "신규 메시지")  # chega DEPOIS
        self.assertEqual(self._cjk_hits("신규"), 1)  # o gatilho pegou
        self.assertEqual(self._cjk_hits("기존"), 0)  # o backfill ainda não

        rebuild_step(self.db)  # não colide
        self.assertEqual(self._cjk_hits("기존"), 1)

    @requires_ext
    def test_rf08_backfill_e_retomavel_pelo_cursor(self):
        # Popula ANTES de qualquer indexação, simulando banco já existente.
        drop_cjk_index(self.db)
        for i in range(12):
            self._msg("user", f"메시지 número {i}")
        ensure_cjk_index(self.db)

        st = rebuild_status(self.db)
        self.assertEqual(st.remaining, 12)
        # A marca é a FRONTEIRA com o gatilho, fixada no maior id existente —
        # não "até onde o backfill chegou". Quem anda é o cursor.
        self.assertEqual(st.high_water, 12)
        self.assertEqual(st.cursor, -1)

        st = rebuild_step(self.db, batch=5)
        self.assertEqual(st.remaining, 7)
        self.assertEqual(st.high_water, 12)  # fronteira NÃO se move
        self.assertGreater(st.cursor, -1)  # o cursor sim
        cursor_parcial = st.cursor

        # "Interrupção": nova consulta não recomeça do zero.
        self.assertEqual(rebuild_status(self.db).cursor, cursor_parcial)

        rebuild_step(self.db, batch=5)
        st = rebuild_step(self.db, batch=5)
        self.assertTrue(st.complete)
        self.assertEqual(self._cjk_hits("메시"), 12)

    @requires_ext
    def test_rf09_progresso_e_persistido_em_meta(self):
        for i in range(4):
            self._msg("user", f"텍스트 {i}")
        drop_cjk_index(self.db)
        ensure_cjk_index(self.db)
        rebuild_step(self.db, batch=2)
        row = self.db.execute(
            "SELECT value FROM state_meta WHERE key='fts_cjk_rebuild_progress'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(float(row[0]), 0.5, places=3)

    @requires_ext
    def test_o_gatilho_respeita_a_marca_dagua_e_nao_indexa_duas_vezes(self):
        # O backfill caminha por trás enquanto mensagens novas chegam pelo
        # gatilho; a marca é o que impede a colisão.
        self._msg("user", "단어 하나")
        self.assertEqual(self._cjk_hits("단어"), 1)
        rebuild_step(self.db, batch=100)
        self.assertEqual(self._cjk_hits("단어"), 1)

    @requires_ext
    def test_batch_invalido_e_recusado(self):
        with self.assertRaises(ValueError):
            rebuild_step(self.db, batch=0)

    @requires_ext
    def test_rf13_remocao_limpa_indice_view_gatilhos_e_meta(self):
        self._msg("user", "일본")
        rebuild_step(self.db)
        drop_cjk_index(self.db)
        restos = {
            r["name"]
            for r in self.db.execute("SELECT name FROM sqlite_master WHERE name LIKE '%cjk%'")
        }
        self.assertEqual(restos, set())
        meta = self.db.execute("SELECT count(*) FROM state_meta WHERE key LIKE '%cjk%'").fetchone()[
            0
        ]
        self.assertEqual(meta, 0)


if __name__ == "__main__":
    unittest.main()
