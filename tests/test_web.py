"""Testes para o servidor FastAPI e rotas da interface web do Kairos."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from kairos_state.repositories.messages import MessageRepository
from kairos_web import server as web_server
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


class WebServerApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("KAIROS_HOME")
        self._orig_passphrase_file = os.environ.get("KAIROS_VAULT_PASSPHRASE_FILE")
        self._orig_disable_keyring = os.environ.get("KAIROS_DISABLE_KEYRING")
        os.environ["KAIROS_HOME"] = self._tmp.name
        passphrase_file = Path(self._tmp.name) / "vault-passphrase"
        passphrase_file.write_text("senha-mestra-de-teste\n", encoding="utf-8")
        passphrase_file.chmod(0o600)
        os.environ["KAIROS_VAULT_PASSPHRASE_FILE"] = str(passphrase_file)
        os.environ["KAIROS_DISABLE_KEYRING"] = "1"
        self.client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def tearDown(self):
        if self._orig_home:
            os.environ["KAIROS_HOME"] = self._orig_home
        else:
            os.environ.pop("KAIROS_HOME", None)
        if self._orig_passphrase_file is None:
            os.environ.pop("KAIROS_VAULT_PASSPHRASE_FILE", None)
        else:
            os.environ["KAIROS_VAULT_PASSPHRASE_FILE"] = self._orig_passphrase_file
        if self._orig_disable_keyring is None:
            os.environ.pop("KAIROS_DISABLE_KEYRING", None)
        else:
            os.environ["KAIROS_DISABLE_KEYRING"] = self._orig_disable_keyring
        self._tmp.cleanup()

    def test_health_endpoint(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("app"), "kairos")

    def test_models_endpoint(self):
        res = self.client.get("/api/models")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("default_model", data)
        self.assertIn("models", data)
        self.assertTrue(len(data["models"]) > 0)
        # Verifica se gpt-4o, claude e gemini estao listados
        ids = [m["id"] for m in data["models"]]
        self.assertIn("gpt-4o", ids)
        self.assertIn("claude-3-7-sonnet-20250219", ids)
        self.assertIn("gemini-2.0-flash", ids)

    def test_providers_status_endpoint(self):
        res = self.client.get("/api/providers")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("providers", data)
        prov_names = [p["provider"] for p in data["providers"]]
        self.assertIn("openai", prov_names)
        self.assertIn("anthropic", prov_names)
        self.assertIn("gemini", prov_names)

    def test_set_default_model_and_task_override(self):
        # 1. Altera modelo global
        res = self.client.post(
            "/api/models/set-default",
            json={"model": "gemini-2.0-flash", "provider": "gemini"},
        )
        self.assertEqual(res.status_code, 200)
        cfg_res = self.client.get("/api/config")
        self.assertEqual(cfg_res.json().get("model"), "gemini-2.0-flash")

        # 2. Altera modelo por tarefa (ex: vision)
        res2 = self.client.post(
            "/api/models/set-default",
            json={"model": "gpt-4o", "task": "vision"},
        )
        self.assertEqual(res2.status_code, 200)
        cfg_res2 = self.client.get("/api/config")
        self.assertEqual(cfg_res2.json().get("auxiliary_models", {}).get("vision"), "gpt-4o")

    def test_save_key_and_auth_persistence(self):
        res = self.client.post(
            "/api/providers/save-key",
            json={"provider": "anthropic", "api_key": "sk-ant-test-1234567890"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "saved")
        self.assertIn(data.get("credential_status"), {"active", "saved_unverified"})

        # Verifica se auth.json foi gravado com segurança
        auth_file = Path(self._tmp.name) / "auth.json"
        self.assertTrue(auth_file.exists())
        content = json.loads(auth_file.read_text(encoding="utf-8"))
        self.assertIn("anthropic", content.get("credential_pool", {}))
        serialized = auth_file.read_text(encoding="utf-8")
        self.assertNotIn("sk-ant-test-1234567890", serialized)
        self.assertNotIn("api_key", res.json())

        status = self.client.get("/api/providers/vault-status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "unlocked")

    def test_saves_concorrentes_preservam_referencias_de_providers_distintos(self):
        responses = []

        def save(provider):
            client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
            responses.append(
                client.post(
                    "/api/providers/save-key",
                    json={"provider": provider, "api_key": f"sk-{provider}"},
                )
            )

        threads = [
            threading.Thread(target=save, args=("openai",)),
            threading.Thread(target=save, args=("anthropic",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([response.status_code for response in responses], [200, 200])
        pool = json.loads((Path(self._tmp.name) / "auth.json").read_text(encoding="utf-8"))[
            "credential_pool"
        ]
        self.assertEqual(set(pool), {"openai", "anthropic"})

    def test_sessions_list_endpoint(self):
        res = self.client.get("/api/sessions")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("sessions", data)


class SessionTokenTests(unittest.TestCase):
    """O dashboard escuta em loopback, mas qualquer processo local alcança a
    porta — o token é o que separa a UI de um curl de outro usuário da máquina."""

    def setUp(self):
        self.anon = TestClient(app)
        self.auth = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def test_token_is_not_the_old_hardcoded_constant(self):
        self.assertNotEqual(SESSION_TOKEN, "kairos-session-token")
        self.assertGreaterEqual(len(SESSION_TOKEN), 32)

    def test_api_rejects_missing_token(self):
        res = self.anon.get("/api/sessions")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"], "invalid_session_token")

    def test_api_rejects_wrong_token(self):
        res = self.anon.get("/api/sessions", headers={TOKEN_HEADER: "kairos-session-token"})
        self.assertEqual(res.status_code, 401)

    def test_api_accepts_token_via_query_param(self):
        res = self.anon.get("/api/sessions", params={"token": SESSION_TOKEN})
        self.assertEqual(res.status_code, 200)

    def test_health_stays_open_for_probes(self):
        self.assertEqual(self.anon.get("/api/health").status_code, 200)

    def test_auth_me_NAO_devolve_o_token(self):
        """A rota respondia com o token no corpo — quem a alcançasse levava a
        credencial junto. Agora diz apenas quem está falando."""
        corpo = self.auth.get("/api/auth/me").json()
        self.assertTrue(corpo["authenticated"])
        self.assertNotIn("token", corpo)
        self.assertNotIn(SESSION_TOKEN, str(corpo))

    def test_ws_ticket_is_short_lived_and_distinct_from_session_token(self):
        res = self.auth.post("/api/auth/ws-ticket")
        payload = res.json()
        self.assertNotEqual(payload["ticket"], SESSION_TOKEN)
        self.assertLessEqual(payload["expires_in"], 60)

    def test_websocket_refuses_connection_without_token(self):
        from starlette.websockets import WebSocketDisconnect as WSDisconnect

        with self.assertRaises(WSDisconnect) as ctx, self.anon.websocket_connect("/ws/chat"):
            pass
        self.assertEqual(ctx.exception.code, 4401)

    def test_websocket_accepts_ticket_once_and_rejects_reuse(self):
        from starlette.websockets import WebSocketDisconnect as WSDisconnect

        ticket = self.auth.post("/api/auth/ws-ticket").json()["ticket"]
        with self.anon.websocket_connect(f"/ws/chat?token={ticket}") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            self.assertEqual(json.loads(ws.receive_text())["type"], "pong")

        with (
            self.assertRaises(WSDisconnect) as ctx,
            self.anon.websocket_connect(f"/ws/chat?token={ticket}"),
        ):
            pass
        self.assertEqual(ctx.exception.code, 4401)

    def test_websocket_rejects_session_token_in_query(self):
        from starlette.websockets import WebSocketDisconnect as WSDisconnect

        with (
            self.assertRaises(WSDisconnect) as ctx,
            self.anon.websocket_connect(f"/ws/chat?token={SESSION_TOKEN}"),
        ):
            pass
        self.assertEqual(ctx.exception.code, 4401)

    def test_logout_revokes_pending_websocket_ticket(self):
        from starlette.websockets import WebSocketDisconnect as WSDisconnect

        ticket = self.auth.post("/api/auth/ws-ticket").json()["ticket"]
        self.auth.post("/api/auth/logout")

        with (
            self.assertRaises(WSDisconnect) as ctx,
            self.anon.websocket_connect(f"/ws/chat?token={ticket}"),
        ):
            pass
        self.assertEqual(ctx.exception.code, 4401)

    def test_expired_websocket_ticket_is_rejected(self):
        from starlette.websockets import WebSocketDisconnect as WSDisconnect

        with patch.object(web_server, "monotonic", return_value=100.0):
            ticket = self.auth.post("/api/auth/ws-ticket").json()["ticket"]

        with (
            patch.object(web_server, "monotonic", return_value=131.0),
            self.assertRaises(WSDisconnect) as ctx,
            self.anon.websocket_connect(f"/ws/chat?token={ticket}"),
        ):
            pass
        self.assertEqual(ctx.exception.code, 4401)

    def test_anonymous_logout_does_not_revoke_pending_ticket(self):
        ticket = self.auth.post("/api/auth/ws-ticket").json()["ticket"]
        self.anon.post("/api/auth/logout")

        with self.anon.websocket_connect(f"/ws/chat?token={ticket}") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            self.assertEqual(json.loads(ws.receive_text())["type"], "pong")


if __name__ == "__main__":
    unittest.main()


class IdentidadeKairosTests(unittest.TestCase):
    """A porta 9119 serve Kairos, não Hermes Agent.

    O `web_dist` chegou pronto do Hermes (não há fonte da SPA neste
    repositório), então a marca só se mantém se alguém verificar. Reimportar o
    dist sem repassar o rebrand faz o dashboard voltar a se anunciar como
    Hermes — e é isso que estes testes travam.
    """

    DIST = Path(__file__).resolve().parent.parent / "kairos_web" / "web_dist"

    def test_nenhum_artefato_servido_menciona_hermes(self):
        suspeitos = []
        for f in (
            list(self.DIST.rglob("*.js"))
            + list(self.DIST.rglob("*.css"))
            + [self.DIST / "index.html"]
        ):
            if not f.is_file():
                continue
            texto = f.read_text(encoding="utf-8", errors="ignore")
            if "hermes" in texto.lower():
                suspeitos.append(f.relative_to(self.DIST).as_posix())
        self.assertEqual(suspeitos, [], f"marca residual do Hermes em: {suspeitos}")

    def test_o_titulo_da_pagina_e_kairos(self):
        html = (self.DIST / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Kairos", html)

    def test_spa_principal_usa_header_compativel_sem_token_injetado(self):
        from kairos_web.server import TOKEN_HEADER as header

        self.assertEqual(header, "X-Kairos-Session-Token")
        api_source = (
            Path(__file__).resolve().parent.parent / "kairos_web" / "ui" / "js" / "api.js"
        ).read_text(encoding="utf-8")
        self.assertIn(header, api_source)
        html = (
            Path(__file__).resolve().parent.parent / "kairos_web" / "ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__KAIROS_SESSION_TOKEN__", html)
        self.assertNotIn("__KAIROS_AUTH_REQUIRED__", html)

    def test_o_health_se_identifica_como_kairos(self):
        res = TestClient(app).get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["app"], "kairos")


class RotasEVerbosTests(unittest.TestCase):
    """Os quatro defeitos que faziam menus abrir em branco.

    Todos falhavam em silêncio: nenhum aparecia nos logs do servidor, e o
    cliente não checa status antes de `res.json()`.
    """

    def setUp(self):
        self.client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def test_api_inexistente_devolve_404_json_e_nao_o_index_html(self):
        """O catch-all servia index.html com 200 para qualquer /api/.

        O cliente recebia `<!doctype html>` de `res.json()` e morria num
        SyntaxError de parse — a página abria vazia sem erro em lugar nenhum.
        """
        res = self.client.get("/api/rota-que-nao-existe")
        self.assertEqual(res.status_code, 404)
        self.assertIn("application/json", res.headers["content-type"])
        self.assertEqual(res.json()["error"], "not_found")

    def test_a_spa_continua_servida_para_rotas_que_nao_sao_api(self):
        """O 404 de /api/ não pode quebrar o roteamento do cliente."""
        res = self.client.get("/skills")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers["content-type"])

    def test_config_aceita_o_verbo_que_a_spa_usa(self):
        """A SPA salva com PUT; só POST existia, e o 405 sumia em silêncio.

        Era isto que impedia o idioma escolhido de ser aplicado.
        """
        res = self.client.put("/api/config", json={"config": {"display": {"language": "pt"}}})
        self.assertEqual(res.status_code, 200)

    def test_skills_sao_encontradas_fora_do_diretorio_de_trabalho(self):
        """O handler usava Path.cwd(); sob s6 isso é `/`, e a lista vinha vazia."""
        import os
        from pathlib import Path as P

        cwd = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())  # longe do repositório, como no container
            res = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN}).get("/api/skills")
            self.assertEqual(res.status_code, 200)
            ids = {s["id"] for s in res.json()["skills"]}
            raiz = P(__file__).resolve().parent.parent / "skills"
            embarcadas = {md.parent.relative_to(raiz).as_posix() for md in raiz.rglob("SKILL.md")}
            self.assertEqual(ids, embarcadas)
            self.assertGreater(len(ids), 30, "o catálogo migrado não foi encontrado")
        finally:
            os.chdir(cwd)

    def test_skill_traz_nome_e_descricao_do_frontmatter(self):
        res = self.client.get("/api/skills")
        for s in res.json()["skills"]:
            with self.subTest(skill=s["id"]):
                self.assertTrue(s["description"], f"{s['id']} sem descrição")
                self.assertIn(s["source"], ("builtin", "user"))

    def test_skill_content_recusa_travessia_de_caminho(self):
        res = self.client.get("/api/skills/content", params={"name": "../../etc"})
        self.assertEqual(res.status_code, 404)

    def test_model_options_nao_estoura_no_descritor(self):
        """`m.context_window` não existe — o atributo é `context_length`.

        A rota inteira devolvia 500 com AttributeError.
        """
        res = self.client.get("/api/model/options")
        self.assertEqual(res.status_code, 200)
        for m in res.json()["models"]:
            with self.subTest(model=m["id"]):
                self.assertIsInstance(m["context_window"], int)


class IdiomaTests(unittest.TestCase):
    """Inglês entre os idiomas, e a escolha realmente aplicada."""

    def test_ingles_esta_disponivel_no_backend(self):
        from kairos_i18n import SUPPORTED_LANGUAGES

        for lang in ("en", "pt", "es", "fr"):
            with self.subTest(lang=lang):
                self.assertIn(lang, SUPPORTED_LANGUAGES)

    def test_ingles_esta_no_seletor_da_interface(self):
        i18n = next(
            (Path(__file__).resolve().parent.parent / "kairos_web" / "web_dist" / "assets").glob(
                "i18n-*.js"
            )
        )
        bundle = i18n.read_text(encoding="utf-8", errors="ignore")
        for code, nome in (
            ("en", "English"),
            ("pt", "Português"),
            ("es", "Español"),
            ("fr", "Français"),
        ):
            with self.subTest(lang=code):
                self.assertIn(f"{code}:{{name:`{nome}`}}", bundle.replace('"', ""))

    def test_todo_catalogo_tem_as_mesmas_chaves_do_ingles(self):
        """Um catálogo incompleto faz a interface cair para inglês só em partes."""
        import yaml

        base = Path(__file__).resolve().parent.parent / "locales"

        def chaves(d, p=""):
            out = set()
            for k, v in (d or {}).items():
                out |= chaves(v, f"{p}{k}.") if isinstance(v, dict) else {f"{p}{k}"}
            return out

        ref = chaves(yaml.safe_load((base / "en.yaml").read_text(encoding="utf-8")))
        self.assertTrue(ref)
        for f in sorted(base.glob("*.yaml")):
            with self.subTest(locale=f.stem):
                self.assertEqual(chaves(yaml.safe_load(f.read_text(encoding="utf-8"))), ref)

    def test_o_idioma_salvo_e_o_idioma_relido(self):
        client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
        anterior = client.get("/api/config").json()
        try:
            for lang in ("en", "pt", "es", "fr"):
                with self.subTest(lang=lang):
                    cfg = {**anterior, "display": {**anterior.get("display", {}), "language": lang}}
                    self.assertEqual(
                        client.put("/api/config", json={"config": cfg}).status_code, 200
                    )
                    self.assertEqual(client.get("/api/config").json()["display"]["language"], lang)
        finally:
            client.put("/api/config", json={"config": anterior})


class AtalhosDeIdiomaTests(unittest.TestCase):
    """Trocar de idioma não pode trocar a tecla do atalho.

    A convenção dos catálogos é `[tecla]palavra traduzida`, com a tecla igual
    em todos os idiomas. pt e es divergiam — e ainda traziam `[a]lways`, em
    inglês, no meio da frase traduzida.
    """

    BASE = Path(__file__).resolve().parent.parent / "locales"
    CAMPOS = ("choose_short", "choose_long")

    def _approval(self, locale: str) -> dict:
        import yaml

        d = yaml.safe_load((self.BASE / f"{locale}.yaml").read_text(encoding="utf-8"))
        return (d or {}).get("approval", {})

    def _teclas(self, texto: str) -> list[str]:
        return re.findall(r"\[(\w)\]", texto)

    def test_as_teclas_sao_as_mesmas_em_todo_idioma(self):
        ref = {c: self._teclas(self._approval("en")[c]) for c in self.CAMPOS}
        self.assertTrue(all(ref.values()))
        for f in sorted(self.BASE.glob("*.yaml")):
            a = self._approval(f.stem)
            for campo in self.CAMPOS:
                with self.subTest(locale=f.stem, campo=campo):
                    self.assertEqual(self._teclas(a[campo]), ref[campo])

    def test_nenhuma_traducao_repete_a_palavra_inglesa(self):
        """`[a]lways` num texto em português é tradução esquecida."""
        for f in sorted(self.BASE.glob("*.yaml")):
            if f.stem in ("en", "af", "de", "ga", "it", "tr", "hu"):
                continue  # latinas/germânicas legítimas podem coincidir
            a = self._approval(f.stem)
            for campo in self.CAMPOS:
                with self.subTest(locale=f.stem, campo=campo):
                    for palavra in ("always", "session", "deny", "never", "once"):
                        self.assertNotIn(
                            palavra,
                            a[campo].lower(),
                            f"{f.stem}.{campo} deixou '{palavra}' em inglês",
                        )


class InterfaceKairosTests(unittest.TestCase):
    """A interface servida na raiz é a do Kairos, não o dist herdado."""

    UI = Path(__file__).resolve().parent.parent / "kairos_web" / "ui"

    def setUp(self):
        self.client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def test_a_raiz_serve_a_interface_propria(self):
        html = self.client.get("/").text
        self.assertIn("/ui/js/app.js", html)

    def test_a_raiz_nao_carrega_nada_do_dist_herdado(self):
        html = self.client.get("/").text
        self.assertNotIn("/assets/", html, "a raiz ainda referencia bundles do Hermes")

    def test_os_estaticos_da_interface_sao_servidos(self):
        for caminho in (
            "/ui/styles/tokens.css",
            "/ui/styles/components.css",
            "/ui/js/app.js",
            "/ui/js/views/skills.js",
            "/ui/kairos-symbol.svg",
        ):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.client.get(caminho).status_code, 200)

    def test_o_dist_herdado_nao_e_mais_servido(self):
        """A SPA principal já possui Chat; o dist herdado saiu do runtime."""
        self.assertEqual(self.client.get("/legacy/").status_code, 404)

    def test_o_token_NAO_viaja_no_html_da_interface_propria(self):
        """Injetar o token no HTML entregava a credencial a quem só carregasse
        a página. A interface autentica e passa a usar o cookie httpOnly."""
        html = self.client.get("/").text
        self.assertNotIn(SESSION_TOKEN, html)
        self.assertNotIn("__KAIROS_SESSION_TOKEN__", html)

    def test_legacy_nao_expoe_token(self):
        self.assertNotIn("__KAIROS_SESSION_TOKEN__", self.client.get("/legacy/").text)

    def test_nenhum_arquivo_da_interface_menciona_hermes(self):
        for f in sorted(self.UI.rglob("*")):
            if f.suffix in (".js", ".css", ".html", ".svg") and f.is_file():
                with self.subTest(arquivo=f.name):
                    self.assertNotIn("hermes", f.read_text(encoding="utf-8").lower())

    def test_os_modulos_da_interface_tem_sintaxe_valida(self):
        """Sem passo de build, um erro de sintaxe só apareceria no navegador."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node não disponível")
        for f in sorted(self.UI.rglob("*.js")):
            with self.subTest(modulo=f.name):
                with f.open("rb") as fonte:
                    r = subprocess.run(
                        [node, "--input-type=module", "--check"],
                        stdin=fonte,
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                self.assertEqual(r.returncode, 0, r.stderr.decode()[:400])

    def test_toda_cor_da_interface_vem_de_token(self):
        """Cor solta é como um sistema visual vira uma coleção de exceções."""
        permitido = {"#fff", "#ffffff"}  # o polegar do interruptor, sempre branco
        for f in sorted(self.UI.rglob("*.css")):
            if f.name == "tokens.css":
                continue
            for cor in re.findall(r"#[0-9a-fA-F]{3,8}\b", f.read_text(encoding="utf-8")):
                with self.subTest(arquivo=f.name, cor=cor):
                    self.assertIn(cor.lower(), permitido, f"{cor} em {f.name} fora de tokens.css")


class AutenticacaoTests(unittest.TestCase):
    """Entrar, sair, e voltar a ser barrado."""

    def setUp(self):
        self.anon = TestClient(app)

    def test_rota_protegida_recusa_sem_credencial(self):
        self.assertEqual(self.anon.get("/api/skills").status_code, 401)

    def test_me_e_aberto_e_responde_que_nao_ha_sessao(self):
        corpo = self.anon.get("/api/auth/me").json()
        self.assertFalse(corpo["authenticated"])
        self.assertTrue(corpo["auth_required"])

    def test_login_com_token_errado_recusa(self):
        res = self.anon.post("/api/auth/login", json={"token": "nao-e-o-token"})
        self.assertEqual(res.status_code, 401)
        self.assertNotIn(SESSION_TOKEN, res.text)

    def test_o_ciclo_completo_entrar_usar_sair(self):
        c = TestClient(app)
        self.assertEqual(c.post("/api/auth/login", json={"token": SESSION_TOKEN}).status_code, 200)
        self.assertEqual(c.get("/api/skills").status_code, 200, "não entrou")
        self.assertTrue(c.get("/api/auth/me").json()["authenticated"])

        self.assertEqual(c.post("/api/auth/logout").status_code, 200)
        self.assertEqual(c.get("/api/skills").status_code, 401, "continuou dentro depois de sair")
        self.assertFalse(c.get("/api/auth/me").json()["authenticated"])

    def test_o_cookie_de_sessao_e_httponly(self):
        """Sem httpOnly, qualquer script da página lê a credencial."""
        c = TestClient(app)
        res = c.post("/api/auth/login", json={"token": SESSION_TOKEN})
        bruto = res.headers.get("set-cookie", "")
        self.assertIn("kairos_session", bruto)
        self.assertIn("httponly", bruto.lower())
        self.assertIn("samesite=lax", bruto.lower())

    def test_o_header_continua_valendo_para_cli_e_integracao(self):
        c = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
        self.assertEqual(c.get("/api/skills").status_code, 200)


class CatalogoDeSkillsTests(unittest.TestCase):
    """As skills migradas do Hermes carregam e não trazem a marca de origem."""

    RAIZ = Path(__file__).resolve().parent.parent / "skills"

    def test_o_catalogo_tem_mais_de_trinta_skills(self):
        encontradas = list(self.RAIZ.rglob("SKILL.md"))
        self.assertGreater(len(encontradas), 30, f"só {len(encontradas)} skills")

    def test_toda_skill_carrega_e_valida(self):
        from kairos_skills.frontmatter import parse_frontmatter, validate_frontmatter

        for md in sorted(self.RAIZ.rglob("SKILL.md")):
            with self.subTest(skill=md.parent.name):
                fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
                validate_frontmatter(fm)
                self.assertTrue(fm.description.strip())

    def test_nenhuma_skill_procura_o_ambiente_do_hermes(self):
        """`$HERMES_HOME/.env` não existe no Kairos: a skill não acharia nada.

        As referências de ambiente são funcionais, não decorativas — é por isso
        que traduzi-las faz parte de migrar a capacidade, e não da estética.
        """
        proibidos = ("HERMES_HOME", "hermes_home", "~/.hermes", "$HOME/.hermes")
        for f in sorted(self.RAIZ.rglob("*")):
            if not f.is_file():
                continue
            try:
                texto = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for termo in proibidos:
                with self.subTest(arquivo=str(f.relative_to(self.RAIZ)), termo=termo):
                    self.assertNotIn(termo, texto)

    def test_as_skills_que_se_citam_por_caminho_resolvem(self):
        """Achatar as categorias quebraria estes caminhos sem nada acusar."""
        alvos = set()
        for md in sorted(self.RAIZ.rglob("SKILL.md")):
            alvos |= set(
                re.findall(
                    r"KAIROS_HOME[:\-\w${}/.]*?/skills/([\w/-]+\.\w+)",
                    md.read_text(encoding="utf-8"),
                )
            )
        self.assertTrue(alvos, "nenhuma referência cruzada encontrada")
        for alvo in sorted(alvos):
            with self.subTest(alvo=alvo):
                self.assertTrue((self.RAIZ / alvo).exists(), f"skills/{alvo} não existe")


class TokenDeAcessoTests(unittest.TestCase):
    """Como o token é obtido, e por onde ele NÃO passa."""

    def test_o_arquivo_de_token_e_lido_quando_nao_ha_variavel(self):
        """O arquivo existe para haver um caminho estável fora do ambiente:
        `printenv` e `docker inspect` revelam a variável, o arquivo 0600 não."""
        import importlib

        with tempfile.TemporaryDirectory() as tmp:
            alvo = Path(tmp) / "web-token"
            alvo.write_text("token-vindo-do-arquivo\n", encoding="utf-8")
            antigo_home = os.environ.get("KAIROS_HOME")
            antigo_tok = os.environ.pop("KAIROS_WEB_TOKEN", None)
            os.environ["KAIROS_HOME"] = tmp
            try:
                import kairos_web.server as srv

                self.assertEqual(srv._token_configurado(), "token-vindo-do-arquivo")
            finally:
                if antigo_home is None:
                    os.environ.pop("KAIROS_HOME", None)
                else:
                    os.environ["KAIROS_HOME"] = antigo_home
                if antigo_tok is not None:
                    os.environ["KAIROS_WEB_TOKEN"] = antigo_tok
                importlib.invalidate_caches()

    def test_a_variavel_de_ambiente_tem_precedencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "web-token").write_text("do-arquivo\n", encoding="utf-8")
            antigo_home = os.environ.get("KAIROS_HOME")
            antigo_tok = os.environ.get("KAIROS_WEB_TOKEN")
            os.environ["KAIROS_HOME"] = tmp
            os.environ["KAIROS_WEB_TOKEN"] = "do-ambiente"  # noqa: S105 — fixture, não segredo
            try:
                import kairos_web.server as srv

                self.assertEqual(srv._token_configurado(), "do-ambiente")
            finally:
                for chave, valor in (
                    ("KAIROS_HOME", antigo_home),
                    ("KAIROS_WEB_TOKEN", antigo_tok),
                ):
                    if valor is None:
                        os.environ.pop(chave, None)
                    else:
                        os.environ[chave] = valor

    def test_o_token_nunca_aparece_nos_logs_do_servidor(self):
        """Um segredo em log vaza para onde os logs forem — e eles vão longe."""
        fonte = (Path(__file__).resolve().parent.parent / "kairos_web" / "server.py").read_text(
            encoding="utf-8"
        )
        for linha in fonte.splitlines():
            despido = linha.strip()
            if despido.startswith(("logger.", "print(")) or ".info(" in despido:
                with self.subTest(linha=despido[:70]):
                    self.assertNotIn("SESSION_TOKEN", despido)
                    self.assertNotIn("_token_configurado", despido)


class TextoDoLoginTests(unittest.TestCase):
    """A entrada apresenta uma plataforma de agentes, não uma aula de mitologia."""

    FONTE = (
        Path(__file__).resolve().parent.parent / "kairos_web" / "ui" / "js" / "views" / "login.js"
    )

    def test_a_entrada_liga_o_conceito_a_agentes(self):
        texto = self.FONTE.read_text(encoding="utf-8").lower()
        for termo in ("agentes", "momento", "autonomia", "precisão"):
            with self.subTest(termo=termo):
                self.assertIn(termo, texto)

    def test_a_entrada_nao_ensina_a_procurar_o_token_no_log(self):
        """A ajuda antiga mandava procurar o token no log do container — ou
        seja, ensinava a tratar como normal um segredo estar em log."""
        texto = self.FONTE.read_text(encoding="utf-8").lower()
        self.assertNotIn("log do container", texto)
        self.assertIn("kairos token new", texto)


class SkillsNavegacaoTests(unittest.TestCase):
    """A API entrega o que o navegador de categorias precisa."""

    def setUp(self):
        self.client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
        self.dados = self.client.get("/api/skills").json()

    def test_a_resposta_traz_o_resumo_por_categoria(self):
        """Sem isto a interface recontaria 201 itens a cada repintura."""
        self.assertIn("categorias", self.dados)
        self.assertEqual(self.dados["total"], len(self.dados["skills"]))
        soma = sum(c["total"] for c in self.dados["categorias"])
        self.assertEqual(soma, self.dados["total"], "a soma das categorias não fecha com o total")

    def test_toda_skill_declara_a_categoria_do_resumo(self):
        conhecidas = {c["id"] for c in self.dados["categorias"]}
        for s in self.dados["skills"]:
            with self.subTest(skill=s["id"]):
                self.assertIn(s["category"], conhecidas)

    def test_a_lista_traz_o_bastante_para_o_detalhe(self):
        """Um request por cartão seria uma tempestade a cada troca de filtro."""
        for chave in ("version", "author", "license", "tags", "platforms", "related"):
            with self.subTest(chave=chave):
                self.assertIn(chave, self.dados["skills"][0])

    def test_as_tags_do_catalogo_sao_lidas(self):
        """O parser lia tags só de `metadata.kairos`; o catálogo as traz no
        nível de topo, e a forma ignorada some sem erro nenhum."""
        com_tags = [s for s in self.dados["skills"] if s["tags"]]
        self.assertGreater(len(com_tags), 100, "quase nenhuma skill expôs tags")

    def test_filtrar_por_categoria_isola_o_conjunto(self):
        for cat in ("finance", "creative"):
            with self.subTest(categoria=cat):
                da_cat = [s for s in self.dados["skills"] if s["category"] == cat]
                declarado = next(c["total"] for c in self.dados["categorias"] if c["id"] == cat)
                self.assertEqual(len(da_cat), declarado)
                self.assertTrue(all(s["id"].startswith(f"{cat}/") for s in da_cat))


class SessoesTests(unittest.TestCase):
    """Lista, detalhe e transcrição — contra um banco com sessões de verdade."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._home_antigo = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name
        self.addCleanup(self._restaurar)

        from kairos_state import connect, initialize_schema
        from kairos_state.repositories.sessions import SessionRepository

        conn = connect(Path(self._tmp.name) / "state.db")
        initialize_schema(conn)
        sr = SessionRepository(conn)
        mr = MessageRepository(conn)
        agora = 1_700_000_000.0
        sr.create(session_id="s-fechada", source="cli", started_at=agora, display_name="Fechada")
        sr.create(session_id="s-aberta", source="web", started_at=agora + 10)
        for i, (papel, texto) in enumerate(
            [("user", "oi"), ("assistant", "olá"), ("user", "tudo bem?")]
        ):
            mr.append(session_id="s-fechada", role=papel, content=texto, timestamp=agora + i)
        sr.end("s-fechada", "concluida", ended_at=agora + 100)
        conn.commit()
        conn.close()
        self.client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def _restaurar(self):
        if self._home_antigo is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = self._home_antigo

    def test_lista_traz_as_sessoes_com_estado(self):
        d = self.client.get("/api/sessions").json()
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["abertas"], 1)
        estados = {s["id"]: s["status"] for s in d["sessions"]}
        self.assertEqual(estados["s-fechada"], "encerrada")
        self.assertEqual(estados["s-aberta"], "aberta")

    def test_lista_permite_busca_e_filtro_de_arquivadas(self):
        from kairos_state import connect, default_db_path

        conn = connect(default_db_path())
        with conn:
            conn.execute("UPDATE sessions SET archived = 1 WHERE id = ?", ("s-fechada",))
        conn.close()

        buscada = self.client.get("/api/sessions", params={"q": "Fechada"}).json()
        self.assertEqual([s["id"] for s in buscada["sessions"]], ["s-fechada"])
        arquivadas = self.client.get("/api/sessions", params={"status": "arquivadas"}).json()
        self.assertEqual([s["id"] for s in arquivadas["sessions"]], ["s-fechada"])
        abertas = self.client.get("/api/sessions", params={"status": "abertas"}).json()
        self.assertEqual([s["id"] for s in abertas["sessions"]], ["s-aberta"])

    def test_sessao_pode_ser_arquivada_sem_apagar_mensagens(self):
        res = self.client.patch("/api/sessions/s-fechada", json={"archived": True})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["archived"])
        mensagens = self.client.get("/api/sessions/s-fechada/messages").json()["messages"]
        self.assertEqual(len(mensagens), 3)

    def test_tags_sao_normalizadas_e_filtravel(self):
        res = self.client.patch(
            "/api/sessions/s-fechada",
            json={"tags": [" Projeto ", "URGENTE", "projeto"]},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["tags"], ["projeto", "urgente"])
        filtrada = self.client.get("/api/sessions", params={"tag": "projeto"}).json()
        self.assertEqual([s["id"] for s in filtrada["sessions"]], ["s-fechada"])
        self.assertEqual(
            filtrada["tag_counts"], [{"tag": "projeto", "count": 1}, {"tag": "urgente", "count": 1}]
        )

    def test_lista_tem_total_e_paginacao_e_oculta_reversivel(self):
        self.assertEqual(
            self.client.patch("/api/sessions/s-aberta", json={"hidden": True}).status_code, 200
        )
        visiveis = self.client.get("/api/sessions", params={"limit": 1}).json()
        self.assertEqual(visiveis["total"], 1)
        self.assertTrue(visiveis["has_more"] is False)
        ocultas = self.client.get("/api/sessions", params={"status": "ocultas"}).json()
        self.assertEqual([s["id"] for s in ocultas["sessions"]], ["s-aberta"])

    def test_tags_da_visao_oculta_contam_sessoes_ocultas(self):
        self.client.patch("/api/sessions/s-aberta", json={"hidden": True, "tags": ["secreta"]})

        ocultas = self.client.get("/api/sessions", params={"status": "ocultas"}).json()

        self.assertEqual(ocultas["tag_counts"], [{"tag": "secreta", "count": 1}])

    def test_sessoes_fixadas_aparecem_primeiro(self):
        self.client.patch("/api/sessions/s-fechada", json={"pinned": True})

        sessoes = self.client.get("/api/sessions").json()["sessions"]

        self.assertEqual([s["id"] for s in sessoes], ["s-fechada", "s-aberta"])
        self.assertTrue(sessoes[0]["pinned"])

    def test_contagem_de_mensagens_fica_restrita_a_pagina(self):
        from kairos_state import connect, default_db_path
        from kairos_web.server import _contagem_de_mensagens

        conn = connect(default_db_path())
        try:
            self.assertEqual(_contagem_de_mensagens(conn, ["s-aberta"]), {})
            self.assertEqual(_contagem_de_mensagens(conn, ["s-fechada"]), {"s-fechada": 3})
        finally:
            conn.close()

    def test_a_contagem_de_mensagens_vem_das_mensagens(self):
        """`sessions.message_count` fica em zero — nenhum caminho de escrita o
        incrementa. Confiar nele mostraria 0 com as mensagens todas lá."""
        d = self.client.get("/api/sessions").json()
        por_id = {s["id"]: s["message_count"] for s in d["sessions"]}
        self.assertEqual(por_id["s-fechada"], 3)
        self.assertEqual(por_id["s-aberta"], 0)

    def test_detalhe_de_uma_sessao(self):
        s = self.client.get("/api/sessions/s-fechada").json()
        self.assertEqual(s["source"], "cli")
        self.assertEqual(s["title"], "Fechada")
        self.assertEqual(s["message_count"], 3)

    def test_sessao_inexistente_devolve_404(self):
        res = self.client.get("/api/sessions/nao-existe")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "session_not_found")

    def test_a_transcricao_preserva_a_ordem_e_os_papeis(self):
        corpo = self.client.get("/api/sessions/s-fechada/messages").json()
        msgs = corpo.get("messages", corpo)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])


class SessoesInterfaceTests(unittest.TestCase):
    """Contratos pequenos da interação da tabela e da transcrição."""

    FONTE = Path(__file__).resolve().parent.parent / "kairos_web/ui/js/views/sessoes.js"

    def test_linha_de_sessao_pode_ser_ativada_com_teclado(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        self.assertIn('addEventListener("keydown"', texto)
        self.assertIn('ev.key !== "Enter" && ev.key !== " "', texto)

    def test_transcricao_le_o_timestamp_que_a_api_entrega(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        self.assertIn("m.created_at", texto)

    def test_interface_expoe_busca_filtro_e_exportacao(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        self.assertIn("data-busca-sessoes", texto)
        self.assertIn("data-filtro-sessoes", texto)
        self.assertIn("URL.createObjectURL", texto)

    def test_interface_pode_alternar_arquivo(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        self.assertIn("api.atualizarSessao", texto)
        self.assertIn("archived: !sessao.archived", texto)

    def test_interface_pode_fixar_e_desafixar_sessao(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        self.assertIn("data-fixar-sessao", texto)
        self.assertIn("pinned: !sessao.pinned", texto)

    def test_interface_expoe_tags_paginacao_e_markdown(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        self.assertIn("data-filtro-tag", texto)
        self.assertIn("data-pagina-anterior", texto)
        self.assertIn("data-pagina-proxima", texto)
        self.assertIn("Exportar Markdown", texto)
        self.assertIn("api.atualizarSessao(id, { tags })", texto)
