"""A superfície CLI: árvore de comandos e executável.

Fecha a lacuna que nenhuma das 21 tarefas cobria — a spec marca
`hermes_cli/main.py` como 🔴 *"árvore de comandos (50 comandos) — não
percorrida"*.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kairos_cli.commands import COMMANDS, Status, command_names, find_command, leaf_count
from kairos_cli.handlers import HANDLERS, ExitCode
from kairos_cli.main import build_parser, main

REPO = Path(__file__).resolve().parent.parent


#: Comandos que nascem no Kairos e não têm correspondente no legado.
#: `token` existe porque o painel precisava de um caminho para a credencial
#: que não passasse por variável de ambiente nem por log.
PROPRIOS_DO_KAIROS = frozenset({"runtime", "token", "telegram"})


class ArvoreTests(unittest.TestCase):
    def test_a_contagem_e_EVIDENCIA_nao_a_frase_da_spec(self):
        """A spec dizia "50 comandos" e marcava a árvore como 🔴 **não
        percorrida** — o número nunca foi verificado.

        Percorrida agora: `hermes_cli/subcommands/` tem **44** módulos de
        comando (excluídos `__init__` e `_shared`), mais **4** de topo (`run`,
        `chat`, `tick`, `version`) = **48**. Contando os subcomandos
        aninhados, são 84 invocações distintas.

        Os comandos PRÓPRIOS do Kairos são contados à parte. Somá-los ao 48
        apagaria a evidência — o número deixaria de dizer o que foi percorrido
        no legado e passaria a dizer só "o que temos hoje".
        """
        herdados = len(COMMANDS) - len(PROPRIOS_DO_KAIROS)
        self.assertEqual(herdados, 48)
        self.assertGreater(leaf_count(), 48)

    def test_nomes_unicos(self):
        nomes = command_names()
        self.assertEqual(len(nomes), len(set(nomes)))

    def test_os_grupos_extraidos_do_legado_estao_todos_la(self):
        do_legado = {
            "acp",
            "approvals",
            "auth",
            "backup",
            "claw",
            "config",
            "console",
            "cron",
            "dashboard",
            "debug",
            "doctor",
            "dump",
            "gateway",
            "gui",
            "hooks",
            "insights",
            "login",
            "logout",
            "logs",
            "mcp",
            "memory",
            "model",
            "monitoring",
            "pairing",
            "pause",
            "peer",
            "plugins",
            "profile",
            "security",
            "setup",
            "skills",
            "skin",
            "slack",
            "status",
            "sync",
            "tools",
            "uninstall",
            "update",
            "verify",
            "webhook",
            "whatsapp",
        }
        self.assertLessEqual(do_legado, set(command_names()))

    def test_os_subcomandos_reais_foram_preservados(self):
        # Extraídos de `add_parser("...")` em cada módulo do legado. O grupo
        # `cron` ganhou os subcomandos de monitor (`monitor-set/clear/show/
        # run`), implementados pelo lote de fontes de monitor.
        esperado = {
            "config": {"check", "edit", "env-path", "migrate", "path", "set", "show"},
            "cron": {
                "list",
                "create",
                "remove",
                "history",
                "pause",
                "resume",
                "status",
                "tick",
                "monitor-set",
                "monitor-clear",
                "monitor-show",
                "monitor-run",
                "notepad",
                "blueprint",
            },
            "skills": {"add", "install", "list", "remove", "tap"},
            "sync": {"disable", "enable", "now", "push", "status"},
            "pairing": {"clear-pending", "list", "revoke"},
            "peer": {"add", "list", "remove"},
        }
        for grupo, subs in esperado.items():
            with self.subTest(grupo=grupo):
                cmd = find_command(grupo)
                self.assertIsNotNone(cmd, grupo)
                self.assertEqual({s.name for s in cmd.subcommands}, subs)

    def test_todo_comando_tem_ajuda(self):
        for c in COMMANDS:
            with self.subTest(cmd=c.name):
                self.assertTrue(c.help.strip())
                for s in c.subcommands:
                    self.assertTrue(s.help.strip(), f"{c.name} {s.name}")

    def test_todo_comando_IMPLEMENTADO_tem_handler(self):
        for c in COMMANDS:
            if c.status is Status.IMPLEMENTED:
                with self.subTest(cmd=c.name):
                    self.assertIn(c.name, HANDLERS, f"{c.name} declarado implementado sem handler")

    def test_todo_handler_corresponde_a_um_comando_declarado(self):
        declarados = set(command_names())
        for nome in HANDLERS:
            with self.subTest(handler=nome):
                self.assertIn(nome, declarados, f"handler órfão: {nome}")


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_a_arvore_monta_e_todo_comando_e_alcancavel(self):
        defaults = {
            "approvals.test": ("approvals", "test", "ls"),
            "chat": ("chat", "--session", "surface-test"),
            "config.set": ("config", "set", "k", "v"),
            "login": ("login", "--provider", "surface-test", "--api-key", "sk-surface-test"),
            "logout": ("logout", "--provider", "surface-test"),
            "peer.add": ("peer", "add", "--target", "surface-test"),
            "peer.remove": ("peer", "remove", "--target", "surface-test"),
            "pairing.revoke": ("pairing", "revoke", "--target", "surface-test"),
            "prompt-size.set": ("prompt-size", "set", "--size", "14"),
            "console.eval": ("console", "eval", "--expression", "1+1"),
            "skin.use": ("skin", "use", "--theme", "dark"),
            "hooks.use": ("hooks", "use", "--hook", "pre-turn"),
        }
        for c in COMMANDS:
            with self.subTest(cmd=c.name):
                argv = [c.name]
                if c.subcommands:
                    argv.append(c.subcommands[0].name)
                chave = c.name if not c.subcommands else f"{c.name}.{argv[1]}"
                if chave in defaults:
                    argv = list(defaults[chave])
                args = self.parser.parse_args(argv)
                self.assertEqual(args.command, c.name)

    def test_o_positional_de_approvals_NAO_colide_com_o_dest_de_topo(self):
        """O bug que apareceu ao exercitar o CLI: um positional chamado
        `command` SOBRESCREVE o `dest="command"` do parser de topo, e o
        despacho passa a ver a linha avaliada como se fosse o comando."""
        args = self.parser.parse_args(["approvals", "test", "rm -rf x"])
        self.assertEqual(args.command, "approvals")
        self.assertEqual(args.cmdline, "rm -rf x")

    def test_comando_desconhecido_e_recusado(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["inventado"])

    def test_unlock_nao_aceita_senha_por_argumento(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(
                ["auth", "vault-unlock", "--password", "segredo-na-linha-de-comando"]
            )


class ExecucaoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name
        self._saved_disable_keyring = os.environ.get("KAIROS_DISABLE_KEYRING")
        os.environ["KAIROS_DISABLE_KEYRING"] = "1"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = self._saved
        if self._saved_disable_keyring is None:
            os.environ.pop("KAIROS_DISABLE_KEYRING", None)
        else:
            os.environ["KAIROS_DISABLE_KEYRING"] = self._saved_disable_keyring
        self._tmp.cleanup()

    def test_version_pelo_FAST_PATH(self):
        self.assertEqual(main(["--version"]), 0)

    def test_sem_comando_mostra_ajuda_e_sai_com_USAGE(self):
        self.assertEqual(main([]), ExitCode.USAGE)

    def test_doctor_migra_e_sai_zero_num_home_limpo(self):
        self.assertEqual(main(["doctor"]), ExitCode.OK)

    def test_o_doctor_SAI_COM_ERRO_quando_acha_problema(self):
        """Um doctor que sempre sai 0 não serve nem para script nem para CI."""
        main(["doctor"])  # cria o banco
        db = Path(self._tmp.name) / "state.db"
        db.write_bytes(b"isto nao e um banco sqlite")
        self.assertEqual(main(["doctor"]), ExitCode.ERROR)

    def test_superficie_totalmente_implementada_sem_pendencias(self):
        """A regra do projeto continua valendo — reportar sucesso sem efeito
        é pior que ausência — mas a superfície está 100% ligada: nenhum
        comando declarado fica sem handler. O código próprio segue distinto
        de OK e ERROR para qualquer comando que venha a ficar pendente."""
        from kairos_cli.commands import COMMANDS, Status

        pendentes = [c.name for c in COMMANDS if c.status is not Status.IMPLEMENTED]
        self.assertEqual(pendentes, [])
        for c in COMMANDS:
            with self.subTest(cmd=c.name):
                self.assertIn(c.name, HANDLERS)
        self.assertNotEqual(ExitCode.NOT_IMPLEMENTED, ExitCode.OK)
        self.assertNotEqual(ExitCode.NOT_IMPLEMENTED, ExitCode.ERROR)

    def test_dump_emite_json_valido_com_fontes_locais(self):
        import contextlib
        import io
        import json

        from kairos_cli.startup_fast import resolve_kairos_home
        home = Path(resolve_kairos_home())
        home.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = main(["dump", "--json"])
        self.assertIn(r, (0, 1))
        data = json.loads(buf.getvalue())
        self.assertIn("scope", data)
        self.assertIn("state", data)
        self.assertIn("sources", data)
        self.assertIn("home", data["sources"])

    def test_setup_cria_home_e_token_idempotentemente(self):
        home = Path(self._tmp.name)
        self.assertEqual(main(["setup", "--json"]), 0)
        self.assertTrue((home / "web-token").exists())
        self.assertEqual(main(["setup", "--json"]), 0)

    def test_import_com_credenciais_json(self):
        import contextlib
        import io
        import json

        # Formato esperado: {"credentials": {"api_key": "...", "provider": "..."}}
        creds = {"credentials": {"api_key": "sk-test-import", "provider": "openai"}}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["import", "--data", json.dumps(creds)])
        self.assertIn("Credenciais importadas com sucesso", buf.getvalue())
        dados = json.loads((Path(self._tmp.name) / "auth.json").read_text(encoding="utf-8"))
        self.assertIn("openai", dados["credential_pool"])

    def test_import_agent_com_config_json(self):
        import contextlib
        import io
        import json

        # Formato esperado: {"agent": {"name": "...", "capabilities": [...]}}
        agent_cfg = {"agent": {"name": "test-agent", "capabilities": ["chat"]}}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["import-agent", "--data", json.dumps(agent_cfg)])
        self.assertIn("Configuração do agente importada com sucesso", buf.getvalue())
        persistido = json.loads((Path(self._tmp.name) / "agent.json").read_text(encoding="utf-8"))
        self.assertEqual(persistido, {"name": "test-agent", "capabilities": ["chat"]})

    def test_import_agent_rejeita_formato_ou_dados_invalidos(self):
        import contextlib
        import io

        for payload in ('{"nome": "x"}', '{"agent": "x"}', "{lixo"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["import-agent", "--data", payload]), 1)

    def test_uninstall_remove_home_de_verdade_e_falha_se_ausente(self):
        home = Path(self._tmp.name)
        marker = home / "state.db"
        marker.write_text("x", encoding="utf-8")
        self.assertEqual(main(["uninstall"]), 0)
        self.assertFalse(home.exists())
        self.assertEqual(main(["uninstall"]), 1)

    def test_login_logout_persistem_no_auth_json(self):
        import json

        home = Path(self._tmp.name)
        self.assertEqual(main(["login", "--provider", "roundtrip", "--api-key", "sk-roundtrip"]), 0)
        dados = json.loads((home / "auth.json").read_text(encoding="utf-8"))
        self.assertIn("roundtrip", dados["credential_pool"])
        self.assertEqual(main(["logout", "--provider", "roundtrip"]), 0)
        dados = json.loads((home / "auth.json").read_text(encoding="utf-8"))
        self.assertNotIn("roundtrip", dados["credential_pool"])

    def test_login_rejeita_placeholder(self):
        self.assertEqual(main(["login", "--provider", "x", "--api-key", "changeme"]), 1)

    def test_console_eval_avaliacao_real_e_recusa_codigo(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["console", "eval", "--expression", "2+3*4"]), 0)
        self.assertEqual(buf.getvalue().strip(), "14")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["console", "eval", "--expression", "__import__('os')"]), 1)

    def test_skin_use_persiste_tema(self):
        from kairos_cli.config import load_config

        self.assertEqual(main(["skin", "use", "--theme", "dark"]), 0)
        self.assertEqual((load_config() or {}).get("ui", {}).get("theme"), "dark")

    def test_prompt_size_set_get_persiste(self):
        import contextlib
        import io

        self.assertEqual(main(["prompt-size", "set", "--size", "42"]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["prompt-size", "get"]), 0)
        self.assertIn("42", buf.getvalue())

    def test_peer_add_list_remove_persiste(self):
        self.assertEqual(main(["peer", "add", "--target", "p1"]), 0)
        self.assertEqual(main(["peer", "remove", "--target", "p1"]), 0)
        self.assertEqual(main(["peer", "remove", "--target", "p1"]), 1)

    def test_pairing_revoke_exige_registro(self):
        import json

        home = Path(self._tmp.name)
        (home / "pairings.json").write_text(
            json.dumps({"active": ["u1"], "pending": []}), encoding="utf-8"
        )
        self.assertEqual(main(["pairing", "revoke", "--target", "u1"]), 0)
        self.assertEqual(main(["pairing", "revoke", "--target", "u1"]), 1)

    def test_pause_alterna_estado_real(self):
        import contextlib
        import io

        self.assertEqual(main(["pause"]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["pause", "status"]), 0)
        self.assertIn("pausada", buf.getvalue())
        self.assertEqual(main(["pause", "resume"]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["pause", "status"]), 0)
        self.assertIn("ativa", buf.getvalue())

    def test_integracoes_test_nao_alegam_envio(self):
        import contextlib
        import io

        for cmd in ("slack", "telegram", "whatsapp"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main([cmd, "test"]), 0)
            self.assertIn("nada foi enviado", buf.getvalue())

    def test_inicios_indisponiveis_falham_em_vez_de_fingir(self):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["claw", "start"]), 1)
            self.assertEqual(main(["gui", "start"]), 1)
            self.assertEqual(main(["update"]), 1)

    def test_webhook_lista_endpoints_do_home(self):
        import contextlib
        import io
        import json

        home = Path(self._tmp.name)
        (home / "webhooks.json").write_text(
            json.dumps(["https://exemplo.test/hook"]), encoding="utf-8"
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["webhook", "list", "--json"]), 0)
        self.assertEqual(json.loads(buf.getvalue()), {"endpoints": ["https://exemplo.test/hook"]})

    def test_approvals_test_devolve_codigo_por_veredito(self):
        self.assertEqual(main(["approvals", "test", "ls -la"]), 0)
        self.assertEqual(main(["approvals", "test", "sudo apt update"]), 2)
        self.assertEqual(main(["approvals", "test", "rm -rf /"]), 3)

    def test_a_negacao_do_usuario_vence_o_yolo_pela_LINHA_DE_COMANDO(self):
        self.assertEqual(main(["approvals", "test", "rm -rf build", "--deny", "rm *", "--yolo"]), 3)
        self.assertEqual(main(["approvals", "test", "rm -rf build", "--yolo"]), 0)

    def test_os_comandos_implementados_rodam_sem_levantar(self):
        for argv in (
            ["status"],
            ["tools"],
            ["profile", "show"],
            ["auth", "list"],
            ["mcp", "list"],
            ["plugins", "list"],
            ["cron", "status"],
            ["sync", "status"],
            ["skills", "list"],
            ["config", "path"],
            ["config", "check"],
              ["tick"],
              ["version"],
              ["setup", "--json"],
              ["backup", "--json"],
              ["telegram", "test"],
              ["slack", "test"],
              ["whatsapp", "test"],
              ["webhook", "status"],
              ["webhook", "list"],
              ["pairing", "list"],
              ["peer", "list"],
              ["skin", "list"],
              ["memory", "status"],
              ["acp", "status"],
              ["claw", "status"],
              ["console", "eval", "--expression", "2+2"],
              ["gui", "status"],
              ["hooks", "list"],
              ["pause", "status"],
              ["prompt-size", "get"],
         ):
             with self.subTest(argv=argv):
                 self.assertIn(main(argv), (ExitCode.OK, ExitCode.NOT_IMPLEMENTED))

    def test_saida_JSON_e_valida(self):
        import contextlib
        import io
        import json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["status", "--json"])
        json.loads(buf.getvalue())

    def test_auth_list_NUNCA_imprime_o_segredo(self):
        import contextlib
        import io
        import json as _json

        (Path(self._tmp.name) / "auth.json").write_text(
            _json.dumps({"credential_pool": {"openai": [{"key": "sk-NAO-VAZAR"}]}}),
            encoding="utf-8",
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["auth", "list"])
        self.assertNotIn("sk-NAO-VAZAR", buf.getvalue())
        self.assertIn("openai", buf.getvalue())

    def test_vault_init_e_status_nao_imprimem_senha(self):
        import contextlib
        import io

        output = io.StringIO()
        with (
            patch("getpass.getpass", side_effect=["senha-secreta", "senha-secreta"]),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(["auth", "vault-init"]), ExitCode.OK)
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["auth", "vault-status"]), ExitCode.OK)

        self.assertNotIn("senha-secreta", output.getvalue())
        self.assertIn("locked", output.getvalue())

    def test_migrate_sem_confirmacao_apenas_relata(self):
        import contextlib
        import io
        import json as _json

        (Path(self._tmp.name) / "auth.json").write_text(
            _json.dumps({"credential_pool": {"openai": [{"api_key": "sk-legado"}]}}),
            encoding="utf-8",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["auth", "migrate"]), ExitCode.OK)

        self.assertIn("confirmação necessária", output.getvalue())
        self.assertNotIn("sk-legado", output.getvalue())
        self.assertIn("sk-legado", (Path(self._tmp.name) / "auth.json").read_text())

    def test_migrate_confirmado_remove_plaintext_sem_imprimi_lo(self):
        import contextlib
        import io
        import json as _json

        with patch("getpass.getpass", side_effect=["senha-secreta", "senha-secreta"]):
            self.assertEqual(main(["auth", "vault-init"]), ExitCode.OK)
        auth_path = Path(self._tmp.name) / "auth.json"
        auth_path.write_text(
            _json.dumps({"credential_pool": {"openai": [{"api_key": "sk-legado"}]}}),
            encoding="utf-8",
        )
        output = io.StringIO()
        with (
            patch("getpass.getpass", return_value="senha-secreta"),
            contextlib.redirect_stdout(output),
        ):
            code = main(["auth", "migrate", "--confirm-remove-plaintext"])

        self.assertEqual(code, ExitCode.OK)
        self.assertNotIn("sk-legado", output.getvalue())
        self.assertNotIn("sk-legado", auth_path.read_text(encoding="utf-8"))


class ExecutavelTests(unittest.TestCase):
    def test_o_entry_point_esta_declarado(self):
        conteudo = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[project.scripts]", conteudo)
        self.assertIn('kairos = "kairos_cli.main:main"', conteudo)

    def test_o_executavel_roda_de_verdade(self):
        exe = REPO / ".venv" / "bin" / "kairos"
        if not exe.is_file():
            self.skipTest("venv sem o executável instalado")
        r = subprocess.run(
            [str(exe), "--version"], capture_output=True, text=True, check=False, timeout=30
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kairos", r.stdout)

    def test_o_caminho_que_o_CONTAINER_espera(self):
        """O Dockerfile e os scripts referenciam
        `/opt/kairos/.venv/bin/kairos`. Sem o entry point, a imagem constrói
        e falha com 'No such file or directory' — que foi exatamente o que a
        Tarefa 07 observou."""
        for arquivo in ("docker/main-wrapper.sh", "docker/bin/kairos"):
            with self.subTest(arquivo=arquivo):
                self.assertIn(
                    "/opt/kairos/.venv/bin/kairos", (REPO / arquivo).read_text(encoding="utf-8")
                )


class FastPathTests(unittest.TestCase):
    def test_o_fast_path_NAO_monta_a_arvore_de_argparse(self):
        """A guarda de leveza só tem sentido se alguém de fato usar o módulo
        antes dos imports pesados. `--version` é esse uso."""
        codigo = (
            "import sys;"
            "from kairos_cli.main import main;"
            "rc = main(['--version']);"
            "print('argparse' in sys.modules)"
        )
        r = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True, check=False, timeout=30
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("False", r.stdout, "o fast path montou a árvore")


if __name__ == "__main__":
    unittest.main()
