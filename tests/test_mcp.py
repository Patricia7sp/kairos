"""Tarefa 12 — mcp.

Critério de pronto: "o teto de resultado tem **duas camadas na ordem certa**
— teto de alocação acima do limiar de spillover, nunca abaixo (T-21)."
Mais **T-24**, **T-24b** e **T-24c**.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kairos_mcp.client import (
    MCP_HARD_RESULT_CAP_CHARS,
    MCPServerConfig,
    SchemaCache,
    Transport,
    UnsafeServerConfig,
    mcp_available,
    namespaced_tool_name,
    truncate_mcp_text_result,
    validate_server_config,
)
from kairos_mcp.server import (
    AUTHENTICATION_RATIONALE,
    SERVER_TOOLS,
    UNPUBLISHED_TOOLS,
    EventBridge,
    server_tool_names,
)
from kairos_tools.budget import DEFAULT_RESULT_SIZE_CHARS


class T21TetoTests(unittest.TestCase):
    """As duas camadas, e a ORDEM entre elas é o requisito."""

    def test_o_teto_MCP_fica_ACIMA_do_limiar_de_spillover(self):
        """A propriedade central. Um teto no nível do spillover truncaria
        antes que o spillover pudesse preservar o dado."""
        self.assertGreater(MCP_HARD_RESULT_CAP_CHARS, DEFAULT_RESULT_SIZE_CHARS)
        self.assertGreater(MCP_HARD_RESULT_CAP_CHARS / DEFAULT_RESULT_SIZE_CHARS, 10)

    def test_resultado_grande_NORMAL_passa_intacto_para_o_spillover(self):
        # 100 KB: acima do spillover, muito abaixo do teto rígido.
        texto = "x" * 100_000
        self.assertEqual(truncate_mcp_text_result(texto), texto)

    def test_enxurrada_patologica_e_truncada(self):
        texto = "y" * (MCP_HARD_RESULT_CAP_CHARS + 10_000)
        out = truncate_mcp_text_result(texto)
        self.assertLess(len(out), len(texto))
        self.assertIn("TRUNCADO", out)

    def test_o_corte_e_40_cabeca_60_cauda(self):
        """A cauda pesa mais porque erro e conclusão vivem no fim; cortar só
        a cabeça descartaria a parte que responde."""
        texto = "A" * 500 + "B" * 500
        out = truncate_mcp_text_result(texto, max_chars=100)
        self.assertTrue(out.startswith("A" * 40))
        self.assertTrue(out.endswith("B" * 60))

    def test_o_aviso_diz_quanto_foi_omitido(self):
        out = truncate_mcp_text_result("z" * 1000, max_chars=100)
        self.assertIn("900", out)


class DependenciaOpcionalTests(unittest.TestCase):
    def test_sem_o_pacote_mcp_o_modulo_e_no_op_e_NAO_levanta(self):
        """Um agente que não usa MCP não paga nem em ruído nem em falha."""
        self.assertIsInstance(mcp_available(), bool)  # nunca levanta


class SegurancaDeConfigTests(unittest.TestCase):
    def cfg(self, **kw):
        base = {"name": "s", "transport": Transport.STDIO, "command": "/usr/bin/meu-mcp"}
        return MCPServerConfig(**{**base, **kw})

    def test_config_valida_passa(self):
        validate_server_config(self.cfg())

    def test_comando_que_e_SHELL_e_recusado(self):
        """Senão a entrada de config vira execução de comando arbitrário."""
        for shell in ("sh", "/bin/bash", "powershell", "cmd"):
            with self.subTest(cmd=shell), self.assertRaises(UnsafeServerConfig):
                validate_server_config(self.cfg(command=shell))

    def test_argumento_com_marcador_de_shell_e_recusado(self):
        for arg in ("-c", "a && b", "x | y", "$(whoami)", "`id`"):
            with self.subTest(arg=arg), self.assertRaises(UnsafeServerConfig):
                validate_server_config(self.cfg(args=(arg,)))

    def test_stdio_sem_comando_e_recusado(self):
        with self.assertRaises(UnsafeServerConfig):
            validate_server_config(self.cfg(command=None))

    def test_http_exige_url_valida(self):
        with self.assertRaises(UnsafeServerConfig):
            validate_server_config(MCPServerConfig(name="s", transport=Transport.HTTP))
        with self.assertRaises(UnsafeServerConfig):
            validate_server_config(
                MCPServerConfig(name="s", transport=Transport.HTTP, url="file:///etc/passwd")
            )
        validate_server_config(MCPServerConfig(name="s", transport=Transport.SSE, url="https://ok"))

    def test_nome_hostil_e_recusado(self):
        for ruim in ("../fora", "a/b", ".oculto", ""):
            with self.subTest(name=ruim), self.assertRaises(UnsafeServerConfig):
                validate_server_config(self.cfg(name=ruim))

    def test_os_tres_transportes(self):
        self.assertEqual({t.value for t in Transport}, {"stdio", "http", "sse"})


class NamespaceTests(unittest.TestCase):
    def test_dois_servidores_com_ferramenta_homonima_nao_colidem(self):
        a = namespaced_tool_name("github", "search")
        b = namespaced_tool_name("jira", "search")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("mcp__"))

    def test_caractere_hostil_no_nome_e_neutralizado(self):
        nome = namespaced_tool_name("a/b", "c d")
        self.assertNotIn("/", nome)
        self.assertNotIn(" ", nome)


class SchemaCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = SchemaCache(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_registra_sem_acordar_o_processo_stdio(self):
        """Sem o cache, montar o prompt exigiria spawnar todo servidor MCP a
        cada boot só para perguntar quais ferramentas ele tem."""
        self.assertIsNone(self.cache.load("s"))
        self.cache.store("s", [{"name": "t"}])
        self.assertEqual(self.cache.load("s"), [{"name": "t"}])

    def test_cache_corrompido_conta_como_AUSENTE_nao_como_erro(self):
        self.cache.store("s", [{"name": "t"}])
        (Path(self._tmp.name) / "s.json").write_text("{{{ lixo", encoding="utf-8")
        self.assertIsNone(self.cache.load("s"))  # não levanta

    def test_escrita_e_atomica(self):
        self.cache.store("s", [{"name": "t"}])
        self.assertFalse((Path(self._tmp.name) / "s.tmp").exists())

    def test_invalidacao(self):
        self.cache.store("s", [{"name": "t"}])
        self.assertTrue(self.cache.invalidate("s"))
        self.assertIsNone(self.cache.load("s"))
        self.assertFalse(self.cache.invalidate("s"))


class T24AprovacaoTests(unittest.TestCase):
    """T-24 — decidido: NÃO publicar."""

    def test_as_duas_ferramentas_de_aprovacao_NAO_sao_publicadas(self):
        publicadas = server_tool_names()
        for nome in ("permissions_list_open", "permissions_respond"):
            with self.subTest(tool=nome):
                self.assertNotIn(nome, publicadas)
                self.assertIn(nome, UNPUBLISHED_TOOLS)

    def test_cada_nao_publicada_tem_o_MOTIVO_registrado(self):
        for nome, motivo in UNPUBLISHED_TOOLS.items():
            with self.subTest(tool=nome):
                self.assertGreater(len(motivo), 40, f"{nome} sem motivo real")

    def test_o_motivo_cita_a_regra_do_projeto(self):
        # "Uma ferramenta que reporta sucesso sem efeito é pior que uma
        # ferramenta ausente" — a regra que o projeto estabeleceu em G-28.
        self.assertIn("sem efeito", UNPUBLISHED_TOOLS["permissions_respond"])

    def test_as_publicadas_nao_intersectam_as_nao_publicadas(self):
        self.assertEqual(set(SERVER_TOOLS) & set(UNPUBLISHED_TOOLS), set())


class T24bAutenticacaoTests(unittest.TestCase):
    def test_o_porque_da_ausencia_de_auth_fica_ESCRITO(self):
        self.assertIn("stdio", AUTHENTICATION_RATIONALE)
        self.assertIn("fronteira é o sistema operacional", AUTHENTICATION_RATIONALE)

    def test_a_revisao_futura_esta_condicionada_e_ANTES(self):
        """Se houver transporte remoto, a decisão precisa ser revista ANTES de
        o transporte existir."""
        self.assertIn("ANTES", AUTHENTICATION_RATIONALE)
        self.assertIn("remoto", AUTHENTICATION_RATIONALE)


class T24cBridgeTests(unittest.TestCase):
    """Baseline no start, sem reproduzir histórico (#13414)."""

    def setUp(self):
        self.b = EventBridge()

    def test_sem_baseline_tudo_e_emitido(self):
        self.assertTrue(self.b.should_emit("s1", 1))

    def test_historico_existente_NAO_e_reproduzido(self):
        self.b.baseline_existing({"s1": 100})
        self.assertFalse(self.b.should_emit("s1", 50))
        self.assertFalse(self.b.should_emit("s1", 100))
        self.assertTrue(self.b.should_emit("s1", 101))

    def test_a_PRIMEIRA_mensagem_de_conversa_NOVA_e_entregue(self):
        """A armadilha da correção óbvia: baselinar por marca de tempo perderia
        exatamente o caso que mais importa."""
        self.b.baseline_existing({"s1": 100})
        self.assertTrue(self.b.should_emit("nova", 1))

    def test_o_baseline_avanca_conforme_emite(self):
        self.b.baseline_existing({"s1": 100})
        self.b.note_emitted("s1", 101)
        self.assertFalse(self.b.should_emit("s1", 101))
        self.assertTrue(self.b.should_emit("s1", 102))

    def test_baseline_vazio_no_start_ainda_entrega_tudo(self):
        self.b.baseline_existing({})
        self.assertTrue(self.b.should_emit("qualquer", 1))


if __name__ == "__main__":
    unittest.main()
