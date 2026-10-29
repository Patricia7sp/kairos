"""Testes do toolset `memory` — store em arquivo, guardas fail-closed e registro."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kairos_tools.memory import MemoryStore, memory_tool, register_memory_tool
from kairos_tools.registry import ToolRegistry


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.store = MemoryStore(home=self.home)

    def test_add_list_e_persistencia_no_arquivo(self):
        result = self.store.add("memory", "ambiente do usuário é Linux")
        self.assertTrue(result["success"])
        self.assertTrue((self.home / "memories" / "MEMORY.md").exists())

        listed = self.store.list("memory")
        self.assertEqual(listed["entries"], ["ambiente do usuário é Linux"])
        self.assertEqual(listed["count"], 1)

        reloaded = MemoryStore(home=self.home).list("memory")
        self.assertEqual(reloaded["entries"], ["ambiente do usuário é Linux"])

    def test_duplicata_exata_e_recusada_sem_duplicar(self):
        self.store.add("memory", "prefiro respostas curtas")
        result = self.store.add("memory", "prefiro respostas curtas")
        self.assertTrue(result["success"])
        self.assertIn("já existe", result["message"])
        self.assertEqual(self.store.list("memory")["count"], 1)

    def test_conteudo_vazio_e_recusado(self):
        result = self.store.add("memory", "   ")
        self.assertFalse(result["success"])
        self.assertIn("vazio", result["error"])

    def test_replace_por_substring_unica(self):
        self.store.add("memory", "prefiro respostas curtas")
        result = self.store.replace("memory", "respostas curtas", "prefiro verbosidade média")
        self.assertTrue(result["success"])
        self.assertEqual(self.store.list("memory")["entries"], ["prefiro verbosidade média"])

    def test_replace_sem_correspondencia_traz_current_entries(self):
        self.store.add("memory", "gosto de maçã")
        result = self.store.replace("memory", "banana", "outro texto")
        self.assertFalse(result["success"])
        self.assertIn("Nenhuma entrada casou", result["error"])
        self.assertEqual(result["current_entries"], ["gosto de maçã"])

    def test_remove_por_substring_unica(self):
        self.store.add("memory", "anotação número um")
        self.store.add("memory", "anotação número dois")
        result = self.store.remove("memory", "número um")
        self.assertTrue(result["success"])
        self.assertEqual(self.store.list("memory")["entries"], ["anotação número dois"])

    def test_limit_de_caracteres_recusa_e_nao_persiste(self):
        store = MemoryStore(home=self.home, memory_char_limit=24)
        store.add("memory", "primeira entrada")
        result = store.add("memory", "segunda entrada bem mais longa que o orçamento")
        self.assertFalse(result["success"])
        self.assertIn("estouraria o limite", result["error"])
        self.assertEqual(store.list("memory")["count"], 1)
        self.assertEqual(MemoryStore(home=self.home).list("memory")["count"], 1)

    def test_deriva_externa_recusa_a_mutacao_e_arquiva_backup(self):
        self.store.add("memory", "escrita pela ferramenta")
        raw = (self.home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
        # Terceiro acrescenta à mão um marcador que não round-trip.
        (self.home / "memories" / "MEMORY.md").write_text(raw + "\n§\n", encoding="utf-8")

        result = self.store.replace("memory", "escrita pela ferramenta", "novo texto")
        self.assertFalse(result["success"])
        self.assertIn("Recusa gravar", result["error"])
        self.assertIn("drift_backup", result)
        backups = list((self.home / "memories").glob("MEMORY.md.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertIn("§", backups[0].read_text(encoding="utf-8"))
        # Nada foi alterado no arquivo original.
        self.assertIn(
            "escrita pela ferramenta",
            (self.home / "memories" / "MEMORY.md").read_text(encoding="utf-8"),
        )

    def test_deriva_nao_segue_symlink_no_nome_previsivel_do_backup(self):
        self.store.add("memory", "entrada original")
        path = self.home / "memories" / "MEMORY.md"
        raw = path.read_text(encoding="utf-8") + "\n§\n"
        path.write_text(raw, encoding="utf-8")
        outside = self.home / "outside-preserved"
        outside.write_text("conteúdo externo preservado", encoding="utf-8")
        link = path.with_suffix(path.suffix + ".bak.1234567890")
        link.symlink_to(outside)

        with patch("kairos_tools.memory.time.time", return_value=1234567890):
            result = self.store.replace("memory", "entrada original", "substituta")

        self.assertFalse(result["success"])
        self.assertEqual(outside.read_text(encoding="utf-8"), "conteúdo externo preservado")
        self.assertTrue(link.is_symlink())
        backup = Path(result["drift_backup"])
        self.assertNotEqual(backup, link)
        self.assertEqual(backup.read_text(encoding="utf-8"), raw)
        self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_derivas_no_mesmo_segundo_preservam_cada_backup(self):
        self.store.add("memory", "entrada original")
        path = self.home / "memories" / "MEMORY.md"
        original = path.read_text(encoding="utf-8")
        first_raw = original + "\n§\n"
        second_raw = original + "\n§\n segunda deriva "

        with patch("kairos_tools.memory.time.time", return_value=1234567890):
            path.write_text(first_raw, encoding="utf-8")
            first = self.store.replace("memory", "entrada original", "substituta")
            path.write_text(second_raw, encoding="utf-8")
            second = self.store.remove("memory", "entrada original")

        first_backup = Path(first["drift_backup"])
        second_backup = Path(second["drift_backup"])
        self.assertNotEqual(first_backup, second_backup)
        self.assertEqual(first_backup.read_text(encoding="utf-8"), first_raw)
        self.assertEqual(second_backup.read_text(encoding="utf-8"), second_raw)
        self.assertEqual(path.read_text(encoding="utf-8"), second_raw)

    def test_backup_de_deriva_tem_permissao_privada(self):
        self.store.add("memory", "entrada original")
        path = self.home / "memories" / "MEMORY.md"
        raw = path.read_text(encoding="utf-8") + "\n§\n"
        path.write_text(raw, encoding="utf-8")

        result = self.store.remove("memory", "entrada original")

        self.assertFalse(result["success"])
        backup = Path(result["drift_backup"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.read_text(encoding="utf-8"), raw)

    def test_falha_ao_criar_backup_recusa_sem_inventar_caminho(self):
        self.store.add("memory", "entrada original")
        path = self.home / "memories" / "MEMORY.md"
        raw = path.read_text(encoding="utf-8") + "\n§\n"
        path.write_text(raw, encoding="utf-8")

        with patch("tempfile.NamedTemporaryFile", side_effect=OSError("sem espaço")):
            result = self.store.replace("memory", "entrada original", "substituta")

        self.assertFalse(result["success"])
        self.assertIn("backup", result["error"].lower())
        self.assertNotIn("drift_backup", result)
        self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_falha_de_escrita_preserva_original_sem_expor_erro_interno(self):
        self.store.add("memory", "entrada original")
        path = self.home / "memories" / "MEMORY.md"
        raw = path.read_text(encoding="utf-8") + "\n§\n"
        path.write_text(raw, encoding="utf-8")
        outside = self.home / "outside-preserved"
        outside.write_bytes(b"bytes externos preservados")
        sentinel = f"private-sentinel:{outside}"
        real_named_temporary_file = tempfile.NamedTemporaryFile

        class FailingWriteContext:
            def __enter__(self):
                self.handle = real_named_temporary_file(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix="MEMORY.md.bak.",
                    delete=False,
                )
                self.handle.__enter__()
                return self

            @property
            def name(self):
                return self.handle.name

            def write(self, content):
                self.handle.write(content[:4])
                raise OSError(sentinel)

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

        with (
            patch(
                "tempfile.NamedTemporaryFile",
                side_effect=lambda **kwargs: FailingWriteContext(),
            ),
            self.assertLogs("kairos_tools.memory", level="WARNING") as captured,
        ):
            result = self.store.replace("memory", "entrada original", "substituta")

        logged = captured.records[0]
        self.assertEqual(logged.error_class, "OSError")
        self.assertEqual(logged.kind, "memory_drift_backup")
        self.assertEqual(logged.phase, "write")
        self.assertNotIn(sentinel, captured.output[0])
        self.assertFalse(result["success"])
        self.assertNotIn("drift_backup", result)
        self.assertNotIn(sentinel, str(result))
        self.assertIn("backup", result["error"].lower())
        self.assertEqual(path.read_text(encoding="utf-8"), raw)
        self.assertEqual(outside.read_bytes(), b"bytes externos preservados")
        partial_backups = list(path.parent.glob("MEMORY.md.bak.*"))
        self.assertEqual(len(partial_backups), 1)
        self.assertEqual(partial_backups[0].read_text(encoding="utf-8"), raw[:4])
        self.assertEqual(partial_backups[0].stat().st_mode & 0o777, 0o600)

    def test_arquivo_existente_ilegivel_e_recusa_como_vazio(self):
        path = self.home / "memories" / "MEMORY.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()

        result = self.store.add("memory", "conteúdo que deveria abortar")
        self.assertFalse(result["success"])
        self.assertIn("não pôde ser lido", result["error"])
        self.assertFalse(self.store.list("memory")["success"])
        self.assertTrue(Path(path).is_dir())

    def test_clear_remove_os_arquivos_do_store(self):
        self.store.add("memory", "anotação")
        self.store.add("user", "usuário se chama Maria")
        self.store.clear()
        self.assertFalse((self.home / "memories" / "MEMORY.md").exists())
        self.assertFalse((self.home / "memories" / "USER.md").exists())

    def test_limite_do_alvo_user_e_independente(self):
        store = MemoryStore(home=self.home, user_char_limit=10)
        result = store.add("user", "texto que não cabe no limite do perfil")
        self.assertFalse(result["success"])
        self.assertIn("estouraria o limite", result["error"])


class MemoryToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = MemoryStore(home=Path(self._tmp.name))

    def test_alvo_invalido_e_recusado_nomeado(self):
        result = memory_tool(action="list", target="runtime")
        self.assertFalse(result["success"])
        self.assertIn("Alvo inválido", result["error"])

    def test_acao_desconhecida_e_recusada(self):
        result = memory_tool(action="append", target="memory", store=self.store)
        self.assertFalse(result["success"])
        self.assertIn("Ação desconhecida", result["error"])

    def test_acao_obrigatoriedade_de_argumentos(self):
        self.assertFalse(memory_tool(action="add", store=self.store)["success"])
        self.assertFalse(memory_tool(action="remove", store=self.store)["success"])
        replace = memory_tool(action="replace", content="só o novo", store=self.store)
        self.assertFalse(replace["success"])
        self.assertIn("old_text", replace["error"])

    def test_list_revez_cria_arquivo_algum(self):
        result = memory_tool(action="list", target="user", store=self.store)
        self.assertTrue(result["success"])
        self.assertEqual(result["entries"], [])
        self.assertFalse((Path(self._tmp.name) / "memories" / "USER.md").exists())


class MemoryCliTests(unittest.TestCase):
    """O executor real do comando `kairos memory` fala com o store de verdade."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def _parse(self, *argv):
        from kairos_cli.main import build_parser

        return build_parser().parse_args(list(argv))

    def _run(self, *argv):
        import asyncio
        import io
        from contextlib import redirect_stdout

        from kairos_cli.memory import run_memory

        out = io.StringIO()
        with redirect_stdout(out):
            code = asyncio.run(run_memory(self.home, self._parse(*argv)))
        return code, out.getvalue()

    def test_status_mostra_entradas_reais_do_store(self):
        MemoryStore(home=self.home).add("memory", "anotação persistida")
        code, out = self._run("memory", "status")
        self.assertEqual(code, 0)
        self.assertIn("MEMORY:", out)
        self.assertIn("anotação persistida", out)

    def test_off_limpa_os_arquivos_e_status_reflete(self):
        MemoryStore(home=self.home).add("memory", "anotação")
        MemoryStore(home=self.home).add("user", "perfil do usuário")

        code_off, out_off = self._run("memory", "off")
        self.assertEqual(code_off, 0)
        self.assertIn("limpa", out_off)
        self.assertFalse((self.home / "memories" / "MEMORY.md").exists())
        self.assertFalse((self.home / "memories" / "USER.md").exists())

        code, out = self._run("memory", "status")
        self.assertEqual(code, 0)
        self.assertIn("0 entrada(s)", out)

    def test_subcomando_ausente_devole_erro(self):
        code, out = self._run("memory")
        self.assertEqual(code, 1)
        self.assertIn("Subcomando inválido", out)

    def test_subcomando_fora_da_lista_e_recusado_pelo_parser(self):
        with self.assertRaises(SystemExit):
            self._parse("memory", "dump")


class MemoryRegistrationTests(unittest.TestCase):
    def test_registro_expoe_o_toolset_memory(self):
        reg = ToolRegistry()
        register_memory_tool(reg)
        self.assertIn("memory", reg.get_all_tool_names())
        definitions = [
            d for d in reg.get_definitions() if d.get("function", {}).get("name") == "memory"
        ]
        self.assertEqual(len(definitions), 1)

    def test_memory_nao_vaza_para_o_chat(self):
        # D-08.3: memória fica no toolset próprio; o Chat não expõe nem aprova.
        from kairos_integration.chat_tools import CHAT_TOOLS, MUTATING_TOOLS

        self.assertNotIn("memory", CHAT_TOOLS)
        self.assertNotIn("memory", MUTATING_TOOLS)

    def test_despacho_integrado_com_env_home(self):
        import os
        from unittest.mock import patch

        reg = ToolRegistry()
        register_memory_tool(reg)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"KAIROS_HOME": tmp, "KAIROS_ALLOW_TOOLS_IN_TESTS": "1"}),
        ):
            out = reg.dispatch(
                "memory", {"action": "add", "target": "memory", "content": "via dispatch"}
            )
            self.assertTrue(out["success"])
            listed = reg.dispatch("memory", {"action": "list", "target": "memory"})
            self.assertEqual(listed["entries"], ["via dispatch"])


if __name__ == "__main__":
    unittest.main()
