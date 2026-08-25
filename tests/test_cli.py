"""Tarefa 15 — hermes-cli.

Critério de pronto: "o bootstrap roda sem dependência de terceiros; a auth é
transacional (nunca deixa `auth.json` meio escrito); `KAIROS_HOME` isola
instalações concorrentes."
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from kairos_cli.auth import AuthStore, CredentialPool, LockBusy, auth_lock, has_usable_secret
from kairos_cli.config import (
    ConfigLayer,
    ConfigResolver,
    Migration,
    apply_migrations,
)
from kairos_cli.startup_fast import (
    container_mode_marker_exists,
    fast_version_line,
    profile_name,
    resolve_kairos_home,
)


class HomeBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name
        self.home = Path(self._tmp.name)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = self._saved
        self._tmp.cleanup()


class FastPathTests(HomeBase):
    def test_o_modulo_e_stdlib_only_EM_SUBPROCESSO_LIMPO(self):
        """A guarda que impede a leveza de se perder na primeira vez que
        alguém adiciona um import 'só para uma coisinha'."""
        codigo = (
            "import sys; import kairos_cli.startup_fast as m; "
            "print(','.join(sorted(m.heavy_modules_loaded())))"
        )
        r = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True, check=False
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "", f"módulos pesados carregados: {r.stdout}")

    def test_KAIROS_HOME_isola_instalacoes(self):
        self.assertEqual(resolve_kairos_home(), self._tmp.name)
        with tempfile.TemporaryDirectory() as outro:
            os.environ["KAIROS_HOME"] = outro
            self.assertNotEqual(resolve_kairos_home(), self._tmp.name)

    def test_sem_KAIROS_HOME_cai_no_padrao(self):
        os.environ.pop("KAIROS_HOME", None)
        self.assertTrue(resolve_kairos_home().endswith(".kairos"))

    def test_o_fast_path_so_SONDA_o_marcador_de_container(self):
        """Ele erra em direção ao caminho lento, que faz o parse
        autoritativo."""
        self.assertFalse(container_mode_marker_exists())
        (self.home / ".container-mode").write_text("runtime=s6\n", encoding="utf-8")
        self.assertTrue(container_mode_marker_exists())

    def test_a_linha_de_versao_tem_UMA_implementacao(self):
        """O bug do legado: cópias `*_fast()` divergiram e `--version` dava
        NameError num dos caminhos 'and nobody noticed'."""
        linha = fast_version_line()
        self.assertIn("kairos", linha)
        (self.home / ".profile-name").write_text("trabalho\n", encoding="utf-8")
        self.assertIn("perfil: trabalho", fast_version_line())
        (self.home / ".container-mode").touch()
        self.assertIn("[container]", fast_version_line())

    def test_perfil_ausente_ou_vazio_devolve_None(self):
        self.assertIsNone(profile_name())
        (self.home / ".profile-name").write_text("   \n", encoding="utf-8")
        self.assertIsNone(profile_name())


class PrecedenciaTests(unittest.TestCase):
    def resolver(self, **kw):
        base = {
            "cli_flags": {},
            "env": {},
            "profile_config": {"model": {"name": "do-perfil"}},
            "global_config": {"model": {"name": "do-global"}},
            "defaults": {"model": {"name": "padrão"}},
        }
        return ConfigResolver(**{**base, **kw})

    def test_as_cinco_camadas_em_ordem(self):
        self.assertEqual(
            [c.name for c in sorted(ConfigLayer)],
            ["CLI_FLAG", "ENV_VAR", "PROFILE_CONFIG", "GLOBAL_CONFIG", "DEFAULTS"],
        )

    def test_flag_vence_tudo(self):
        r = self.resolver(cli_flags={"model.name": "da-flag"}, env={"KAIROS_MODEL_NAME": "do-env"})
        self.assertEqual(r.resolve("model.name"), ("da-flag", ConfigLayer.CLI_FLAG))

    def test_env_vence_config(self):
        r = self.resolver(env={"KAIROS_MODEL_NAME": "do-env"})
        self.assertEqual(r.resolve("model.name"), ("do-env", ConfigLayer.ENV_VAR))

    def test_perfil_vence_global(self):
        self.assertEqual(self.resolver().resolve("model.name")[1], ConfigLayer.PROFILE_CONFIG)

    def test_global_vence_default(self):
        r = self.resolver(profile_config={})
        self.assertEqual(r.resolve("model.name"), ("do-global", ConfigLayer.GLOBAL_CONFIG))

    def test_default_e_o_ultimo(self):
        r = self.resolver(profile_config={}, global_config={})
        self.assertEqual(r.resolve("model.name"), ("padrão", ConfigLayer.DEFAULTS))

    def test_chave_inexistente_devolve_None_em_ambos(self):
        self.assertEqual(self.resolver().resolve("nao.existe"), (None, None))

    def test_a_CAMADA_e_devolvida_junto(self):
        """ "Por que este valor?" tem resposta sem o usuário adivinhar em qual
        dos cinco lugares olhar."""
        _, camada = self.resolver().resolve("model.name")
        self.assertIsInstance(camada, ConfigLayer)


class MigracaoTests(unittest.TestCase):
    def setUp(self):
        self.migracoes = (
            Migration(1, "renomeia a", lambda c: {**c, "b": c.pop("a", None)}),
            Migration(2, "acrescenta c", lambda c: {**c, "c": 1}),
        )

    def test_migra_em_ordem_e_grava_a_versao(self):
        out, v = apply_migrations({"a": "x"}, self.migracoes)
        self.assertEqual(v, 2)
        self.assertEqual(out["b"], "x")
        self.assertEqual(out["c"], 1)

    def test_e_IDEMPOTENTE(self):
        out1, v1 = apply_migrations({"a": "x"}, self.migracoes)
        out2, v2 = apply_migrations(out1, self.migracoes)
        self.assertEqual((out1, v1), (out2, v2))

    def test_config_ja_migrado_parcialmente_continua_de_onde_parou(self):
        out, v = apply_migrations({"config_version": 1, "b": "x"}, self.migracoes)
        self.assertEqual(v, 2)
        self.assertEqual(out["c"], 1)


class LockTests(HomeBase):
    def test_a_mesma_THREAD_nao_reentra(self):
        """O flock do kernel não separa threads do mesmo processo: sem o
        holder por thread, duas escreveriam por cima uma da outra."""
        alvo = self.home / "auth.json"
        with auth_lock(alvo), self.assertRaises(LockBusy), auth_lock(alvo):
            pass

    def test_o_lock_e_liberado_ao_sair(self):
        alvo = self.home / "auth.json"
        with auth_lock(alvo):
            pass
        with auth_lock(alvo):
            pass  # não levanta

    def test_o_lock_e_liberado_mesmo_com_excecao(self):
        alvo = self.home / "auth.json"
        with self.assertRaises(RuntimeError), auth_lock(alvo):
            raise RuntimeError("boom")
        with auth_lock(alvo):
            pass

    def test_threads_diferentes_nao_se_confundem(self):
        alvo = self.home / "auth.json"
        erros = []

        def trabalha():
            try:
                with auth_lock(alvo):
                    pass
            except LockBusy:
                pass  # esperado se houver disputa real
            except Exception as exc:  # noqa: BLE001
                erros.append(exc)

        ts = [threading.Thread(target=trabalha) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(erros, [])


class AuthStoreTests(HomeBase):
    def setUp(self):
        super().setUp()
        self.store = AuthStore(
            profile={"openai": [{"key": "do-perfil"}]},
            global_store={"openai": [{"key": "do-global"}], "anthropic": [{"key": "so-global"}]},
        )

    def test_o_perfil_e_a_AUTORIDADE(self):
        creds, origem = self.store.credentials_for("openai")
        self.assertEqual(creds[0]["key"], "do-perfil")
        self.assertEqual(origem, "profile")

    def test_provedor_nao_configurado_HERDA_do_global_somente_leitura(self):
        creds, origem = self.store.credentials_for("anthropic")
        self.assertEqual(creds[0]["key"], "so-global")
        self.assertEqual(origem, "global_readonly")
        self.assertTrue(self.store.is_readonly("anthropic"))

    def test_escrita_nova_vai_SEMPRE_para_o_perfil(self):
        """Sem a assimetria, configurar um provedor dentro de um perfil
        escreveria no global e vazaria para os outros perfis."""
        self.store.add_credential("anthropic", {"key": "nova"})
        creds, origem = self.store.credentials_for("anthropic")
        self.assertEqual(origem, "profile")
        self.assertEqual(creds[0]["key"], "nova")
        self.assertFalse(self.store.is_readonly("anthropic"))
        # O global permanece intocado.
        self.assertEqual(self.store.global_store["anthropic"][0]["key"], "so-global")

    def test_a_escrita_e_TRANSACIONAL(self):
        """Um auth.json meio escrito é indistinguível de um sem credencial, e
        o sintoma aparece como falha de autenticação."""
        alvo = self.home / "auth.json"
        self.store.write_atomically(alvo)
        dados = json.loads(alvo.read_text(encoding="utf-8"))
        self.assertIn("openai", dados["credential_pool"])
        self.assertFalse(alvo.with_suffix(".json.tmp").exists())

    def test_provedor_desconhecido_devolve_lista_vazia(self):
        creds, _ = self.store.credentials_for("inexistente")
        self.assertEqual(creds, [])

    def test_cofre_configurado_rejeita_segredo_no_auth_json(self):
        self.store.vault = object()
        self.store.profile = {"openai": [{"api_key": "sk-nao-vazar"}]}

        with self.assertRaisesRegex(ValueError, "segredo"):
            self.store.write_atomically(self.home / "auth.json")


class CredentialPoolTests(unittest.TestCase):
    def setUp(self):
        self.pool = CredentialPool(credentials=[{"k": 1}, {"k": 2}, {"k": 3}])

    def test_rotaciona_apos_erro_transitorio(self):
        self.assertEqual(self.pool.current()["k"], 1)
        self.assertEqual(self.pool.rotate()["k"], 2)
        self.assertEqual(self.pool.rotate()["k"], 3)

    def test_esgotamento_e_detectavel(self):
        for _ in range(3):
            self.pool.rotate()
        self.assertTrue(self.pool.exhausted)
        self.assertIsNone(self.pool.current())

    def test_reset_devolve_todas(self):
        for _ in range(3):
            self.pool.rotate()
        self.pool.reset()
        self.assertFalse(self.pool.exhausted)
        self.assertEqual(self.pool.current()["k"], 1)

    def test_pool_vazio_nao_levanta(self):
        vazio = CredentialPool()
        self.assertIsNone(vazio.current())
        self.assertIsNone(vazio.rotate())
        self.assertFalse(vazio.exhausted)

    def test_placeholder_nao_e_segredo_usavel(self):
        # Reexportado do domínio: uma definição só.
        self.assertFalse(has_usable_secret("changeme"))
        self.assertTrue(has_usable_secret("sk-real"))


if __name__ == "__main__":
    unittest.main()
