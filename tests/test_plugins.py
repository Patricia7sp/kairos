"""Tarefa 10 — plugins.

Critério de pronto: "Uma falha de plugin não derruba o host; hook com aridade
variável não é discriminado por aridade (anti-padrão **Won't**)."
Mais o alerta **G-21**.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kairos_plugins.hooks import (
    HOOK_FAMILIES,
    OBSERVE_ONLY,
    VALID_HOOKS,
    HookRegistry,
    UnknownHook,
    redact_provider_error,
)
from kairos_plugins.manifest import (
    VALID_PLUGIN_KINDS,
    ManifestError,
    PluginKind,
    PluginState,
    load_manifest,
    resolve_state,
)
from kairos_plugins.storage import plugin_data_dir, plugin_db, plugin_root


class HooksTests(unittest.TestCase):
    def setUp(self):
        self.r = HookRegistry()

    def test_sao_37_hooks(self):
        self.assertEqual(len(VALID_HOOKS), 37)

    def test_os_hooks_de_compactacao_NAO_existem(self):
        """🔧 A spec anterior listava cinco que não existem — não há hook de
        compactação algum."""
        for inventado in (
            "on_pre_compress",
            "on_post_compress",
            "on_tool_call",
            "on_tool_result",
            "on_turn_finish",
        ):
            with self.subTest(hook=inventado):
                self.assertNotIn(inventado, VALID_HOOKS)

    def test_hook_desconhecido_e_recusado_no_registro(self):
        with self.assertRaises(UnknownHook):
            self.r.register("nao_existe", lambda: None, plugin="p")

    def test_UM_plugin_quebrado_nao_impede_os_outros(self):
        """O contrato central da unit."""

        def explode(**kw):
            raise RuntimeError("plugin ruim")

        self.r.register("pre_tool_call", explode, plugin="ruim")
        self.r.register("pre_tool_call", lambda **kw: "ok", plugin="bom")
        out = self.r.invoke_hook("pre_tool_call", tool="x")
        self.assertEqual(out.results, ["ok"])
        self.assertEqual(len(out.failures), 1)
        self.assertEqual(out.failures[0][0], "ruim")

    def test_run_all_nao_para_no_primeiro(self):
        """Uma resposta cedo nunca impede os callbacks seguintes: muitos hooks
        têm efeito colateral legítimo, e curto-circuitar entregaria
        comportamento dependente da ordem de instalação."""
        chamados = []
        for nome in ("a", "b", "c"):
            self.r.register(
                "post_tool_call", lambda nome=nome, **kw: chamados.append(nome) or nome, plugin=nome
            )
        self.r.invoke_hook("post_tool_call")
        self.assertEqual(chamados, ["a", "b", "c"])

    def test_primeiro_registrado_vence_e_o_perdedor_e_REPORTADO(self):
        self.r.register("transform_llm_output", lambda **kw: "primeiro", plugin="a")
        self.r.register("transform_llm_output", lambda **kw: "segundo", plugin="b")
        out = self.r.invoke_hook("transform_llm_output")
        self.assertEqual(out.first_valid(lambda r: isinstance(r, str)), "primeiro")
        self.assertEqual(out.skipped, ["segundo"], "o perdedor não some")

    def test_resultado_None_nao_conta(self):
        self.r.register("transform_llm_output", lambda **kw: None, plugin="a")
        self.r.register("transform_llm_output", lambda **kw: "vale", plugin="b")
        out = self.r.invoke_hook("transform_llm_output")
        self.assertEqual(out.results, ["vale"])

    def test_hook_sem_callback_registrado_nao_levanta(self):
        out = self.r.invoke_hook("on_session_start", session="s")
        self.assertEqual(out.results, [])

    def test_streaming_e_familia_de_OBSERVACAO(self):
        # Observam, não transformam: disparados fora do caminho do token.
        self.assertEqual(len(OBSERVE_ONLY), 4)
        self.assertIn("on_stream_delta", OBSERVE_ONLY)
        self.assertNotIn("transform_llm_output", OBSERVE_ONLY)

    def test_as_familias_cobrem_exatamente_os_37(self):
        total = sum(len(v) for v in HOOK_FAMILIES.values())
        self.assertEqual(total, 37)
        self.assertEqual(len({h for hs in HOOK_FAMILIES.values() for h in hs}), 37)

    def test_aridade_variavel_NAO_discrimina_o_plugin(self):
        """Anti-padrão **Won't** de `architecture.md`: aridade como
        discriminador. Callbacks com assinaturas diferentes convivem."""
        self.r.register("pre_command", lambda **kw: "kwargs", plugin="a")
        self.r.register("pre_command", lambda command=None, **kw: "nomeado", plugin="b")
        out = self.r.invoke_hook("pre_command", command="ls")
        self.assertEqual(out.results, ["kwargs", "nomeado"])
        self.assertEqual(out.failures, [])


class G21RedacaoTests(unittest.TestCase):
    """G-21 — o dump de erro do provedor."""

    def setUp(self):
        self.r = HookRegistry()
        self.visto = {}

    def espia(self, **kw):
        self.visto.update(kw)

    def test_o_default_e_REDIGIR(self):
        """No legado o contrato existe só como cláusula de docstring, sem
        barreira nenhuma. Aqui a redação é o default."""
        self.r.register("transform_api_error_classification", self.espia, plugin="curioso")
        self.r.invoke_hook(
            "transform_api_error_classification",
            provider="openai",
            error_message="Bearer sk-vazou-aqui",
            error_body={"key": "segredo"},
        )
        self.assertIn("REDIGIDO", self.visto["error_message"])
        self.assertNotIn("sk-vazou-aqui", str(self.visto))

    def test_a_capacidade_DECLARADA_libera_o_dump_cru(self):
        self.r.register("transform_api_error_classification", self.espia, plugin="confiavel")
        self.r.allow_raw_error("confiavel")
        self.r.invoke_hook(
            "transform_api_error_classification", error_message="detalhe cru do provedor"
        )
        self.assertEqual(self.visto["error_message"], "detalhe cru do provedor")

    def test_a_autorizacao_e_POR_PLUGIN_nao_global(self):
        outro = {}
        self.r.register("transform_api_error_classification", self.espia, plugin="confiavel")
        self.r.register(
            "transform_api_error_classification", lambda **kw: outro.update(kw), plugin="curioso"
        )
        self.r.allow_raw_error("confiavel")
        self.r.invoke_hook("transform_api_error_classification", error_message="cru")
        self.assertEqual(self.visto["error_message"], "cru")
        self.assertIn("REDIGIDO", outro["error_message"])

    def test_campos_nao_sensiveis_passam_intactos(self):
        limpo = redact_provider_error(
            {"provider": "openai", "status_code": 500, "error_message": "x"}
        )
        self.assertEqual(limpo["provider"], "openai")
        self.assertEqual(limpo["status_code"], 500)

    def test_a_redacao_nao_muta_o_payload_original(self):
        original = {"error_message": "cru"}
        redact_provider_error(original)
        self.assertEqual(original["error_message"], "cru")


class ManifestoTests(unittest.TestCase):
    def m(self, **kw):
        base = {"name": "meu-plugin", "version": "1.0.0"}
        return load_manifest({**base, **kw})

    def test_os_cinco_kinds(self):
        self.assertEqual(
            VALID_PLUGIN_KINDS,
            {"standalone", "backend", "exclusive", "platform", "model-provider"},
        )

    def test_kind_desconhecido_NAO_rejeita_coage_para_standalone(self):
        """Rejeitar quebraria plugin escrito contra uma versão futura; a
        coerção degrada para o mais restrito."""
        self.assertEqual(self.m(kind="inventado").kind, PluginKind.STANDALONE)

    def test_kinds_da_spec_anterior_nao_existem(self):
        for antigo in ("memory", "web", "tool", "hook", "generic"):
            with self.subTest(kind=antigo):
                self.assertNotIn(antigo, VALID_PLUGIN_KINDS)

    def test_provedor_de_memoria_e_auto_coagido_para_exclusive(self):
        self.assertEqual(self.m(memory_provider=True).kind, PluginKind.EXCLUSIVE)

    def test_manifesto_sem_nome_ou_versao_e_recusado(self):
        with self.assertRaises(ManifestError):
            load_manifest({"version": "1.0.0"})
        with self.assertRaises(ManifestError):
            load_manifest({"name": "x"})

    def test_hook_inexistente_no_manifesto_e_recusado(self):
        """Um hook que nunca dispara é pior que hook nenhum: o plugin parece
        instalado e não faz nada."""
        with self.assertRaises(ManifestError) as ctx:
            self.m(provides_hooks=["on_pre_compress"])
        self.assertIn("nunca dispara", str(ctx.exception))

    def test_falha_SUAVE_de_requisito_de_ambiente(self):
        m = self.m(requires_env=["MINHA_API_KEY"])
        estado, faltando = resolve_state(m, env={})
        self.assertEqual(estado, PluginState.DISABLED_MISSING_ENV)
        self.assertEqual(faltando, ("MINHA_API_KEY",))
        # O dashboard precisa dizer O QUÊ falta, não só omitir o plugin.

    def test_variavel_vazia_conta_como_ausente(self):
        m = self.m(requires_env=["K"])
        self.assertEqual(resolve_state(m, env={"K": "   "})[0], PluginState.DISABLED_MISSING_ENV)

    def test_requisitos_satisfeitos_ativam(self):
        m = self.m(requires_env=["K"])
        self.assertEqual(resolve_state(m, env={"K": "v"}), (PluginState.ACTIVE, ()))

    def test_g21_requires_raw_error_default_falso(self):
        self.assertFalse(self.m().requires_raw_error)
        self.assertTrue(self.m(requires_raw_error=True).requires_raw_error)

    def test_yaml_invalido_levanta_ManifestError_com_contexto(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plugin.yaml"
            p.write_text("name: [nao\n  fecha", encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(p)


class StorageTests(unittest.TestCase):
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

    def test_o_dado_fica_FORA_do_diretorio_de_instalacao(self):
        """`plugins remove` apaga a árvore de instalação e `plugins update`
        faz git-pull nela: dado estacionado ali morre com o código."""
        d = plugin_data_dir("meu")
        self.assertEqual(d, Path(self._tmp.name) / "plugin-data" / "meu")
        self.assertNotIn("plugins/meu", str(d))

    def test_KAIROS_HOME_e_resolvido_A_CADA_CHAMADA(self):
        # O perfil ativo pode mudar no meio da vida do processo; caminho
        # cacheado escreveria no perfil errado.
        primeiro = plugin_root()
        with tempfile.TemporaryDirectory() as outro:
            os.environ["KAIROS_HOME"] = outro
            self.assertNotEqual(plugin_root(), primeiro)

    def test_nome_hostil_e_recusado(self):
        for ruim in ("../fora", "a/b", ".oculto", ""):
            with self.subTest(name=ruim), self.assertRaises(ValueError):
                plugin_data_dir(ruim)

    def test_cada_plugin_tem_seu_banco_isolado(self):
        a = plugin_db("a")
        b = plugin_db("b")
        a.execute("CREATE TABLE t (x)")
        a.execute("INSERT INTO t VALUES (1)")
        a.commit()
        # `b` não enxerga a tabela de `a`. Exceção específica: com
        # `Exception` genérico o teste passaria até por erro de digitação no
        # SQL, e deixaria de significar "os bancos são isolados".
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            b.execute("SELECT * FROM t").fetchall()
        self.assertIn("no such table", str(ctx.exception))
        a.close()
        b.close()

    def test_o_banco_do_plugin_tem_FK_ligada(self):
        conn = plugin_db("c")
        self.assertEqual(int(conn.execute("PRAGMA foreign_keys").fetchone()[0]), 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
