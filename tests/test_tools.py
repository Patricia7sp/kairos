"""Tarefa 08 — tools.

Critério de pronto: "Resultado acima do teto é **preservado**; a arbitragem
reader/writer por sobreposição de caminho decide paralelizabilidade."

Mais o **T-27**: a cadeia de 7 camadas herdada como está, com a propriedade
que não pode se perder — `approvals.deny` acima do bypass de yolo.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from kairos_tools.approval import (
    ApprovalContext,
    Layer,
    Verdict,
    detection_variants,
    match_user_deny_rule,
    resolve,
)
from kairos_tools.budget import (
    DEFAULT_RESULT_SIZE_CHARS,
    ResultBudget,
    generate_preview,
    maybe_spill,
    prune_spillover,
    spillover_dir,
)
from kairos_tools.parallel import (
    NEVER_PARALLEL_TOOLS,
    PARALLEL_SAFE_TOOLS,
    SegmentKind,
    ToolCall,
    segment_batch,
)
from kairos_tools.policy import (
    DELEGATE_BLOCK_REASONS,
    DELEGATE_BLOCKED_TOOLS,
    SANDBOX_ALLOWED_TOOLS,
    delegate_block_reason,
    sandbox_allows,
)
from kairos_tools.registry import ToolRegistry, ToolsUnavailableUnderTest, tool_error

SCHEMA = {"name": "x", "parameters": {"type": "object", "properties": {}}}


# ===========================================================================
# T-27 — a cadeia de 7 camadas
# ===========================================================================
class CadeiaDeAprovacaoTests(unittest.TestCase):
    def ctx(self, command="ls", **kw):
        return ApprovalContext(command=command, **kw)

    # -- a propriedade central --------------------------------------------

    def test_T27_deny_do_usuario_fica_ACIMA_do_bypass_de_yolo(self):
        """A trava de regressão da ordem das camadas 4 e 5.

        Se alguém trocar a ordem numa refatoração, este teste reprova. Sem
        ele, a regra do usuário viraria sugestão em silêncio.
        """
        d = resolve(self.ctx("rm -rf build/", user_deny=("rm *",), yolo=True))
        self.assertEqual(d.verdict, Verdict.DENY)
        self.assertEqual(d.layer, Layer.USER_DENY)
        self.assertLess(Layer.USER_DENY, Layer.YOLO_BYPASS)

    def test_T27_yolo_amplia_o_permitido(self):
        # O mesmo comando, sem regra do usuário: yolo passa.
        d = resolve(self.ctx("rm -rf build/", yolo=True))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertEqual(d.layer, Layer.YOLO_BYPASS)

    def test_T27_approvals_mode_off_e_equivalente_a_yolo_e_tambem_nao_alcanca(self):
        d = resolve(self.ctx("rm -rf x", user_deny=("rm *",), approvals_mode="off"))
        self.assertEqual(d.layer, Layer.USER_DENY)

    def test_T27_a_allowlist_fica_ABAIXO_do_bypass(self):
        self.assertGreater(Layer.ALLOWLIST, Layer.YOLO_BYPASS)

    def test_T27_a_negacao_do_usuario_nao_e_contornavel(self):
        d = resolve(self.ctx("rm -rf x", user_deny=("rm *",)))
        self.assertFalse(d.bypassable)

    def test_a_negacao_por_padrao_perigoso_E_contornavel(self):
        d = resolve(self.ctx("sudo apt update"))
        self.assertEqual(d.verdict, Verdict.ASK)
        self.assertTrue(d.bypassable)

    # -- as 7 camadas, uma a uma ------------------------------------------

    def test_camada1_backend_isolado_passa_por_cima_de_tudo(self):
        d = resolve(self.ctx("rm -rf /", isolated_backend=True, user_deny=("*",)))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertEqual(d.layer, Layer.CONTAINER_SKIP)

    def test_camada2_hardline_nao_e_contornavel_nem_por_yolo(self):
        for cmd in (
            "rm -rf /",
            "mkfs.ext4 /dev/sda1",
            ":(){ :|:& };:",
            "dd if=/dev/zero of=/dev/sda",
            "chmod -R 777 /",
            "shutdown -h now",
        ):
            with self.subTest(cmd=cmd):
                d = resolve(self.ctx(cmd, yolo=True, allowlist=("*",)))
                self.assertEqual(d.verdict, Verdict.DENY)
                self.assertEqual(d.layer, Layer.HARDLINE)

    def test_camada3_sudo_por_stdin_e_incondicional(self):
        d = resolve(self.ctx("sudo -S apt update", sudo_stdin=True, yolo=True))
        self.assertEqual(d.layer, Layer.SUDO_STDIN)
        self.assertEqual(d.verdict, Verdict.DENY)

    def test_camada6_allowlist_permite_o_que_o_padrao_perguntaria(self):
        sem = resolve(self.ctx("git push --force origin main"))
        self.assertEqual(sem.verdict, Verdict.ASK)
        com = resolve(
            self.ctx("git push --force origin main", allowlist=("git push --force origin main",))
        )
        self.assertEqual(com.verdict, Verdict.ALLOW)
        self.assertEqual(com.layer, Layer.ALLOWLIST)

    def test_camada7_comando_benigno_passa(self):
        d = resolve(self.ctx("ls -la"))
        self.assertEqual(d.verdict, Verdict.ALLOW)

    def test_a_ordem_declarada_e_a_ordem_numerica(self):
        self.assertEqual(
            [camada.name for camada in sorted(Layer)],
            [
                "CONTAINER_SKIP",
                "HARDLINE",
                "SUDO_STDIN",
                "USER_DENY",
                "YOLO_BYPASS",
                "ALLOWLIST",
                "PATTERN_DETECTION",
            ],
        )

    # -- desofuscação ------------------------------------------------------

    def test_a_ofuscacao_nao_escapa_da_regra_do_usuario(self):
        # r\m -rf / recebe o mesmo veredito da forma simples.
        for cmd in (r"r\m -rf build", 'r"m" -rf build', "rm${IFS}-rf${IFS}build"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(match_user_deny_rule(cmd, ("rm *",)))

    def test_as_duas_camadas_veem_as_MESMAS_variantes(self):
        """Se divergissem, a ofuscação contornaria uma e não a outra — a pior
        combinação possível."""
        obfuscado = r'g\i\t st""atus'
        self.assertIn("git status", detection_variants(obfuscado))

    def test_hardline_tambem_desofusca(self):
        d = resolve(self.ctx(r"r\m -rf /", yolo=True))
        self.assertEqual(d.layer, Layer.HARDLINE)

    # -- a mensagem --------------------------------------------------------

    def test_a_mensagem_manda_o_modelo_NAO_reformular(self):
        """A recusa precisa encerrar a tentativa, não iniciar uma busca por
        sinônimo."""
        d = resolve(self.ctx("rm -rf x", user_deny=("rm *",)))
        self.assertIn("NÃO tente de novo nem reformule", d.reason)
        self.assertIn("approvals.deny", d.reason)
        self.assertIn("--yolo", d.reason)

    def test_a_regra_que_casou_e_reportada(self):
        d = resolve(self.ctx("rm -rf x", user_deny=("git *", "rm *")))
        self.assertEqual(d.matched, "rm *")

    def test_deny_vazio_ou_so_espacos_e_no_op(self):
        for patterns in ((), ("",), ("   ",)):
            with self.subTest(patterns=patterns):
                self.assertIsNone(match_user_deny_rule("rm -rf x", patterns))


# ===========================================================================
# Política imutável
# ===========================================================================
class PoliticaTests(unittest.TestCase):
    def test_ambas_as_listas_sao_frozenset(self):
        # Decisão deliberada, não detalhe de tipo: política mutável em runtime
        # não é política, é sugestão.
        self.assertIsInstance(SANDBOX_ALLOWED_TOOLS, frozenset)
        self.assertIsInstance(DELEGATE_BLOCKED_TOOLS, frozenset)
        with self.assertRaises(AttributeError):
            SANDBOX_ALLOWED_TOOLS.add("qualquer")  # type: ignore[attr-defined]

    def test_sandbox_opera_por_allowlist_de_7(self):
        self.assertEqual(len(SANDBOX_ALLOWED_TOOLS), 7)
        self.assertTrue(sandbox_allows("terminal"))
        # Ferramenta nova nasce INVISÍVEL no sandbox — é a diferença entre
        # allowlist e blocklist.
        self.assertFalse(sandbox_allows("ferramenta_inventada_amanha"))

    def test_delegacao_bloqueia_5_cada_uma_com_motivo(self):
        self.assertEqual(len(DELEGATE_BLOCKED_TOOLS), 5)
        self.assertEqual(
            DELEGATE_BLOCKED_TOOLS,
            {"delegate_task", "clarify", "memory", "send_message", "cronjob"},
        )
        for nome in DELEGATE_BLOCKED_TOOLS:
            with self.subTest(tool=nome):
                self.assertTrue(DELEGATE_BLOCK_REASONS[nome].strip())

    def test_o_motivo_e_devolvido_nao_um_booleano(self):
        # Sem o motivo, o modelo tenta por outro caminho achando que foi
        # acidente.
        self.assertIn("recursiva", delegate_block_reason("delegate_task"))
        self.assertIsNone(delegate_block_reason("read_file"))


# ===========================================================================
# Registro
# ===========================================================================
class RegistroTests(unittest.TestCase):
    def setUp(self):
        self.r = ToolRegistry()
        os.environ["KAIROS_ALLOW_TOOLS_IN_TESTS"] = "1"

    def tearDown(self):
        os.environ.pop("KAIROS_ALLOW_TOOLS_IN_TESTS", None)

    def test_rf01_registro_expoe_nome_e_schema(self):
        self.r.register("eco", lambda texto: texto, {**SCHEMA, "name": "eco"})
        self.assertIn("eco", self.r.get_all_tool_names())
        self.assertEqual([d["name"] for d in self.r.get_definitions()], ["eco"])

    def test_rf02_despacho_por_nome(self):
        self.r.register("eco", lambda texto: f"eco:{texto}", SCHEMA)
        self.assertEqual(self.r.dispatch("eco", {"texto": "oi"}), "eco:oi")

    def test_rf03_excecao_de_handler_vira_erro_estruturado(self):
        def explode():
            raise ValueError("estourei")

        self.r.register("bomba", explode, SCHEMA)
        out = self.r.dispatch("bomba")
        self.assertEqual(out, {"error": "ValueError: estourei"})  # o turno prossegue

    def test_rf04_ferramenta_desconhecida_e_erro_NOMEADO_nao_excecao(self):
        # O modelo alucina nomes com frequência: é ocorrência normal, não
        # falha do sistema.
        self.assertEqual(self.r.dispatch("inexistente"), tool_error("Unknown tool: inexistente"))

    def test_terminal_resolve_para_bash_sem_duplicar_o_schema(self):
        # D-08.3: `terminal` é nome da allowlist de sandbox, não ferramenta.
        # O despacho o resolve para `bash` — quem fala com a sandbox funciona,
        # e o catálogo continua com um único schema.
        self.r.register("bash", lambda command: f"bash:{command}", SCHEMA)
        self.assertEqual(self.r.dispatch("terminal", {"command": "ls"}), "bash:ls")
        self.assertIn("bash", self.r.get_all_tool_names())
        self.assertNotIn("terminal", self.r.get_all_tool_names())
        self.assertNotIn("terminal", [d["name"] for d in self.r.get_definitions()])
        # Ferramenta nomeada inexistente continua erro nomeado pelo nome dado.
        self.assertEqual(self.r.dispatch("zoeira"), tool_error("Unknown tool: zoeira"))

    def test_rf05_handler_async_e_bridgeado(self):
        async def lento(x):
            await asyncio.sleep(0)
            return x * 2

        self.r.register("lento", lento, SCHEMA)
        self.assertTrue(self.r._tools["lento"].is_async)
        self.assertEqual(self.r.dispatch("lento", {"x": 21}), 42)

    def test_rf06_requisito_nao_atendido_OMITE_a_ferramenta(self):
        # Schema que o modelo pode chamar e que sempre erra é pior que
        # ferramenta ausente: ele tenta, falha, e tenta de novo.
        self.r.register_toolset("ha", requirement=lambda: False)
        self.r.register("ha_get_state", lambda: "x", SCHEMA, toolset="ha")
        self.r.register("read_file", lambda: "y", SCHEMA)
        nomes = [d["name"] for d in self.r.get_definitions()]
        self.assertNotIn(
            "ha_get_state",
            [
                self.r._tools[n].name
                for n in self.r.get_all_tool_names()
                if self.r.check_tool_availability(n)
            ],
        )
        self.assertEqual(len(nomes), 1)

    def test_requisito_que_LEVANTA_conta_como_indisponivel(self):
        def sonda():
            raise OSError("sem rede")

        self.r.register_toolset("remoto", requirement=sonda)
        self.r.register("remota", lambda: "x", SCHEMA, toolset="remoto")
        self.assertFalse(self.r.is_toolset_available("remoto"))
        self.assertEqual(self.r.get_definitions(), [])

    def test_rf11_alias_de_toolset(self):
        self.r.register_toolset("homeassistant")
        self.r.register_toolset_alias("ha", "homeassistant")
        self.assertEqual(self.r.get_toolset_alias_target("ha"), "homeassistant")
        self.assertTrue(self.r.is_toolset_available("ha"))

    def test_alias_para_toolset_inexistente_e_recusado(self):
        with self.assertRaises(KeyError):
            self.r.register_toolset_alias("x", "nao_existe")

    def test_rf13_snapshot_e_restauracao(self):
        self.r.register("a", lambda: 1, SCHEMA)
        snap = self.r.snapshot_registration()
        self.r.register("b", lambda: 2, SCHEMA)
        self.assertEqual(self.r.get_all_tool_names(), ["a", "b"])
        self.r.restore_registration(snap)
        self.assertEqual(self.r.get_all_tool_names(), ["a"])

    def test_rf12_override_de_plugin_carrega_identidade_e_geracao(self):
        # Override anônimo é irreversível na prática: não se sabe quem o fez
        # nem a qual versão do plugin pertence.
        self.r.register("busca", lambda: "base", SCHEMA)
        e = self.r.apply_plugin_override(
            "busca", lambda: "override", plugin="meu-plugin", generation=3
        )
        self.assertEqual(e.override_of, "meu-plugin")
        self.assertEqual(e.plugin_generation, 3)
        self.assertEqual(self.r.dispatch("busca"), "override")

    def test_override_de_ferramenta_inexistente_e_recusado(self):
        with self.assertRaises(KeyError):
            self.r.apply_plugin_override("fantasma", lambda: 1, plugin="p", generation=1)

    def test_cascata_de_3_niveis_do_teto_de_resultado(self):
        self.r.register("pequena", lambda: "", SCHEMA, max_result_size_chars=100)
        self.r.register("padrao", lambda: "", SCHEMA)
        self.assertEqual(self.r.get_max_result_size("pequena"), 100)  # ferramenta
        self.assertEqual(self.r.get_max_result_size("padrao", 7000), 7000)  # chamador
        self.assertEqual(self.r.get_max_result_size("padrao"), DEFAULT_RESULT_SIZE_CHARS)

    def test_rf14_ferramentas_bloqueadas_sob_pytest(self):
        os.environ.pop("KAIROS_ALLOW_TOOLS_IN_TESTS", None)
        self.r.register("perigosa", lambda: "efeito colateral", SCHEMA)
        with self.assertRaises(ToolsUnavailableUnderTest):
            self.r.dispatch("perigosa")


# ===========================================================================
# Orçamento e spillover
# ===========================================================================
class SpilloverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = self._saved
        self._tmp.cleanup()

    def test_resultado_pequeno_passa_intacto(self):
        r = maybe_spill("curto", "id1", ResultBudget(tool_limit=100))
        self.assertEqual(r.in_context, "curto")
        self.assertFalse(r.spilled)
        self.assertIsNone(r.path)

    def test_resultado_grande_e_PRESERVADO_nao_truncado(self):
        conteudo = "x" * 10_000
        r = maybe_spill(conteudo, "id2", ResultBudget(tool_limit=100))
        self.assertTrue(r.spilled)
        self.assertFalse(r.truncated_lossy)
        # O dado inteiro está em disco.
        self.assertEqual(r.path.read_text(), conteudo)
        # E o contexto recebeu preview + caminho.
        self.assertIn("PRESERVADO", r.in_context)
        self.assertIn(str(r.path), r.in_context)
        self.assertLess(len(r.in_context), len(conteudo))

    def test_falha_de_gravacao_degrada_para_truncamento_COM_perda(self):
        # Diretório inexistente e não criável.
        os.environ["KAIROS_HOME"] = "/proc/impossivel"
        r = maybe_spill("y" * 10_000, "id3", ResultBudget(tool_limit=10))
        self.assertTrue(r.truncated_lossy)
        self.assertFalse(r.spilled)
        self.assertIn("COM PERDA", r.in_context)

    def test_o_lar_e_sempre_host_side(self):
        # Não no tempdir do SO e não dentro do sandbox: sessões que nunca
        # rodaram terminal (MCP-only, cron, gateway) precisam funcionar.
        self.assertEqual(spillover_dir(), Path(self._tmp.name) / "cache" / "spillover")

    def test_tool_use_id_hostil_nao_escapa_do_diretorio(self):
        r = maybe_spill("z" * 500, "../../../etc/passwd", ResultBudget(tool_limit=10))
        self.assertEqual(r.path.parent, spillover_dir())

    def test_preview_reporta_se_cortou(self):
        self.assertEqual(generate_preview("abc", 10), ("abc", False))
        self.assertEqual(generate_preview("abcdef", 3), ("abc", True))

    def test_poda_remove_so_o_velho(self):
        maybe_spill("a" * 500, "velho", ResultBudget(tool_limit=10))
        maybe_spill("b" * 500, "novo", ResultBudget(tool_limit=10))
        velho = spillover_dir() / "velho.txt"
        os.utime(velho, (0, 0))
        self.assertEqual(prune_spillover(max_age_hours=1), 1)
        self.assertFalse(velho.exists())
        self.assertTrue((spillover_dir() / "novo.txt").exists())


# ===========================================================================
# Segmentação de lote
# ===========================================================================
class ParalelismoTests(unittest.TestCase):
    def call(self, nome, **args):
        return ToolCall(nome, args)

    def kinds(self, segs):
        return [(s.kind, [c.name for c in s.calls]) for s in segs]

    def assert_ordenados(self, segs, primeiro, segundo):
        """Afirma que `segundo` NÃO corre em paralelo com `primeiro`.

        A propriedade que importa não é o número de segmentos: dois trechos
        sequenciais adjacentes são fundidos, e a fusão **preserva a ordem**.
        O que não pode acontecer é os dois caírem na mesma corrida paralela.
        """
        for seg in segs:
            nomes = [c.name for c in seg.calls]
            if seg.kind is SegmentKind.PARALLEL and primeiro in nomes and segundo in nomes:
                self.fail(f"{primeiro} e {segundo} correm em paralelo: {self.kinds(segs)}")
        ordem = [c.name for seg in segs for c in seg.calls]
        self.assertLess(ordem.index(primeiro), ordem.index(segundo))

    def test_leituras_independentes_correm_em_paralelo(self):
        segs = segment_batch(
            [
                self.call("web_search", query="a"),
                self.call("web_search", query="b"),
            ]
        )
        self.assertEqual(self.kinds(segs), [(SegmentKind.PARALLEL, ["web_search", "web_search"])])

    def test_a_corrida_escrita_LEITURA_do_mesmo_bloco_e_ordenada(self):
        """A razão de existir de toda a arbitragem por caminho.

        Sem ela, o `read_file` agrupado junto do `write_file` de que depende
        observaria o estado PRÉ-mutação.
        """
        segs = segment_batch(
            [
                self.call("write_file", path="/tmp/a.txt"),
                self.call("read_file", path="/tmp/a.txt"),
            ]
        )
        self.assert_ordenados(segs, "write_file", "read_file")

    def test_leitores_do_mesmo_arquivo_comutam_e_ficam_juntos(self):
        segs = segment_batch(
            [
                self.call("read_file", path="/tmp/a.txt"),
                self.call("read_file", path="/tmp/a.txt"),
            ]
        )
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].kind, SegmentKind.PARALLEL)

    def test_escritores_em_caminhos_independentes_correm_juntos(self):
        segs = segment_batch(
            [
                self.call("write_file", path="/tmp/a.txt"),
                self.call("write_file", path="/tmp/b.txt"),
            ]
        )
        self.assertEqual(len(segs), 1)

    def test_search_files_reserva_a_raiz_como_leitor(self):
        # Uma busca agrupada depois de uma escrita na subárvore buscada fica
        # ordenada atrás dela.
        segs = segment_batch(
            [
                self.call("write_file", path="/tmp/proj/a.txt"),
                self.call("search_files", path="/tmp/proj"),
            ]
        )
        self.assert_ordenados(segs, "write_file", "search_files")

    def test_patch_V4A_usa_os_CABECALHOS_do_corpo_nao_o_path(self):
        """O `path=` pode estar obsoleto; reservar o errado dá ilusão de
        ordenação sem a ordenação."""
        # Cabeçalhos de patch são relativos à raiz do repositório, então o
        # leitor usa a mesma forma relativa — é o cenário real.
        segs = segment_batch(
            [
                self.call(
                    "patch",
                    mode="patch",
                    path="src/obsoleto.py",
                    patch="--- a/src/real.py\n+++ b/src/real.py\n",
                ),
                self.call("read_file", path="src/real.py"),
            ]
        )
        self.assert_ordenados(segs, "patch", "read_file")

    def test_patch_V4A_NAO_reserva_o_path_obsoleto(self):
        """Se reservasse o `path=`, um leitor daquele arquivo seria ordenado
        atrás por engano — e o do arquivo real, não."""
        segs = segment_batch(
            [
                self.call(
                    "patch",
                    mode="patch",
                    path="src/obsoleto.py",
                    patch="--- a/src/real.py\n+++ b/src/real.py\n",
                ),
                self.call("read_file", path="src/obsoleto.py"),
            ]
        )
        # O leitor do arquivo obsoleto NÃO conflita: o patch não o toca.
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].kind, SegmentKind.PARALLEL)

    def test_clarify_derruba_o_lote_para_sequencial(self):
        segs = segment_batch(
            [
                self.call("web_search", query="a"),
                self.call("clarify", question="?"),
                self.call("web_search", query="b"),
            ]
        )
        self.assertIn(SegmentKind.SEQUENTIAL, [s.kind for s in segs])
        self.assertIn("clarify", NEVER_PARALLEL_TOOLS)

    def test_ferramenta_nao_classificada_e_barreira(self):
        segs = segment_batch(
            [
                self.call("web_search", query="a"),
                self.call("terminal", command="ls"),
                self.call("web_search", query="b"),
            ]
        )
        meio = next(s for s in segs if "terminal" in [c.name for c in s.calls])
        self.assertEqual(meio.kind, SegmentKind.SEQUENTIAL)

    def test_corrida_de_uma_chamada_so_e_rebaixada_a_sequencial(self):
        segs = segment_batch([self.call("web_search", query="a")])
        self.assertEqual(segs[0].kind, SegmentKind.SEQUENTIAL)

    def test_segmentos_sequenciais_adjacentes_sao_fundidos(self):
        segs = segment_batch(
            [
                self.call("terminal", command="a"),
                self.call("terminal", command="b"),
            ]
        )
        self.assertEqual(len(segs), 1)
        self.assertEqual(len(segs[0].calls), 2)

    def test_as_12_ferramentas_paralelizaveis(self):
        self.assertEqual(len(PARALLEL_SAFE_TOOLS), 12)
        self.assertIn("read_file", PARALLEL_SAFE_TOOLS)
        self.assertNotIn("write_file", PARALLEL_SAFE_TOOLS)

    def test_lote_vazio(self):
        self.assertEqual(segment_batch([]), [])


if __name__ == "__main__":
    unittest.main()
