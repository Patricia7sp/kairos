"""Tarefa 17 — acp-adapter.

Critério de pronto: "nenhuma lógica do agente é duplicada aqui; as três
políticas produzem comportamentos distintos e escopados."
Mais **T-15** (workspace como piso, G-16) e **T-16** (deny_always → never, G-22).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kairos_acp.approval import (
    SENSITIVE_NAMES,
    AutoApprovePolicy,
    EditProposal,
    build_permission_options,
    edit_approval_requester,
    map_outcome,
    require_edit_approval,
    should_auto_approve_edit,
)
from kairos_acp.session import (
    PROTOCOL_VERSION,
    SESSION_METHODS,
    BlockKind,
    Capabilities,
    ContentBlock,
    classify_resource,
    content_blocks_to_parts,
    decode_text_bytes,
    negotiate_version,
    path_from_file_uri,
)


class T15PisoTests(unittest.TestCase):
    """O workspace é PISO, não alargador."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def prop(self, path):
        return EditProposal(tool_name="write_file", path=path, new_text="x")

    def test_T15_a_politica_session_NAO_aprova_mais_qualquer_caminho(self):
        """No legado, `session` auto-aprovava /etc/hosts e ~/.bashrc, com
        cinco nomes de arquivo entre ela e o disco inteiro."""
        for fora in ("/etc/hosts", "/usr/bin/env", str(Path.home() / ".bashrc")):
            with self.subTest(path=fora):
                self.assertFalse(
                    should_auto_approve_edit(
                        self.prop(fora), AutoApprovePolicy.SESSION, cwd=self.ws
                    )
                )

    def test_dentro_do_workspace_session_aprova(self):
        self.assertTrue(
            should_auto_approve_edit(
                self.prop(f"{self.ws}/src/a.py"), AutoApprovePolicy.SESSION, cwd=self.ws
            )
        )

    def test_workspace_session_tambem_respeita_o_piso(self):
        self.assertTrue(
            should_auto_approve_edit(
                self.prop(f"{self.ws}/a.py"), AutoApprovePolicy.WORKSPACE_SESSION, cwd=self.ws
            )
        )
        self.assertFalse(
            should_auto_approve_edit(
                self.prop("/etc/hosts"), AutoApprovePolicy.WORKSPACE_SESSION, cwd=self.ws
            )
        )

    def test_ask_nunca_aprova(self):
        self.assertFalse(
            should_auto_approve_edit(
                self.prop(f"{self.ws}/a.py"), AutoApprovePolicy.ASK, cwd=self.ws
            )
        )

    def test_a_lista_sensivel_vale_DOS_DOIS_LADOS(self):
        for nome in SENSITIVE_NAMES:
            with self.subTest(nome=nome):
                self.assertFalse(
                    should_auto_approve_edit(
                        self.prop(f"{self.ws}/{nome}"), AutoApprovePolicy.SESSION, cwd=self.ws
                    )
                )

    def test_componente_git_e_ssh_tambem_barram(self):
        for p in (f"{self.ws}/.git/config", f"{self.ws}/sub/.ssh/known_hosts"):
            with self.subTest(path=p):
                self.assertFalse(
                    should_auto_approve_edit(self.prop(p), AutoApprovePolicy.SESSION, cwd=self.ws)
                )

    def test_sem_workspace_resolvido_NAO_ha_dentro(self):
        self.assertFalse(
            should_auto_approve_edit(
                self.prop("/qualquer/a.py"), AutoApprovePolicy.SESSION, cwd=None
            )
        )

    def test_symlink_e_avaliado_pelo_caminho_RESOLVIDO(self):
        alvo = Path(self._tmp.name).parent / "fora_do_ws.txt"
        alvo.write_text("x", encoding="utf-8")
        link = Path(self.ws) / "link.txt"
        try:
            link.symlink_to(alvo)
        except OSError:
            self.skipTest("symlink indisponível")
        self.assertFalse(
            should_auto_approve_edit(self.prop(str(link)), AutoApprovePolicy.SESSION, cwd=self.ws),
            "o link aponta para fora: o piso vale pelo destino",
        )
        alvo.unlink()

    def test_o_temp_NAO_e_piso_por_default(self):
        """No legado o /tmp passava como efeito colateral de resolve() seguir
        symlink no macOS, não como intenção."""
        tmp_file = f"{tempfile.gettempdir()}/x.txt"
        self.assertFalse(
            should_auto_approve_edit(self.prop(tmp_file), AutoApprovePolicy.SESSION, cwd=self.ws)
        )
        self.assertTrue(
            should_auto_approve_edit(
                self.prop(tmp_file), AutoApprovePolicy.SESSION, cwd=self.ws, temp_is_floor=True
            )
        )

    def test_caminho_externo_VOLTA_AO_DIALOGO_nao_e_recusado(self):
        """Não é contenção dura: recusar quebraria monorepo com irmãos e
        edição em ~/.config, e o escape hatch reabriria o buraco."""
        # `should_auto_approve_edit` devolvendo False significa "pergunte",
        # não "negue" — quem nega é o usuário no diálogo.
        self.assertIs(
            should_auto_approve_edit(
                self.prop("/etc/hosts"), AutoApprovePolicy.SESSION, cwd=self.ws
            ),
            False,
        )


class T16NeverTests(unittest.TestCase):
    """deny_always → never, com simetria exata."""

    def test_T16_deny_always_NAO_colapsa_mais_em_deny(self):
        self.assertEqual(map_outcome("deny_always"), "never")
        self.assertEqual(map_outcome("deny"), "deny")
        self.assertNotEqual(map_outcome("deny_always"), map_outcome("deny"))

    def test_a_simetria_com_allow_e_exata(self):
        self.assertEqual(map_outcome("allow_always"), "always")
        self.assertEqual(map_outcome("deny_always"), "never")

    def test_opcao_desconhecida_do_editor_NEGA(self):
        """Fail-closed é a única leitura segura de uma resposta que não se
        entende."""
        self.assertEqual(map_outcome("opcao_inventada"), "deny")

    def test_a_opcao_never_e_oferecida(self):
        ids = [o.option_id for o in build_permission_options(allow_permanent=True)]
        self.assertIn("deny_always", ids)

    def test_smart_denied_encolhe_a_lista(self):
        """Não se oferece 'negar sempre' para algo que o sistema já nega: a
        opção sugeriria que o usuário decide algo já decidido."""
        ids = [
            o.option_id for o in build_permission_options(allow_permanent=True, smart_denied=True)
        ]
        self.assertEqual(ids, ["allow_once", "deny"])

    def test_sem_allow_permanent_a_opcao_always_some(self):
        ids = [o.option_id for o in build_permission_options(allow_permanent=False)]
        self.assertNotIn("allow_always", ids)
        self.assertIn("allow_session", ids)

    def test_sdk_sem_reject_always_omite_a_opcao(self):
        ids = [
            o.option_id
            for o in build_permission_options(allow_permanent=True, supports_reject_always=False)
        ]
        self.assertNotIn("deny_always", ids)


class GuardTests(unittest.TestCase):
    def prop(self):
        return EditProposal(tool_name="patch", path="/x/a.py", new_text="y")

    def test_a_guarda_e_INEXISTENTE_quando_nao_ligada(self):
        """CLI, gateway e cron não a ligam — para elas a guarda não existe,
        e não é 'permissiva'. A distinção importa ao auditar."""
        self.assertIsNone(require_edit_approval(self.prop()))

    def test_ligada_e_aprovando_segue(self):
        with edit_approval_requester(lambda p: True):
            self.assertIsNone(require_edit_approval(self.prop()))

    def test_ligada_e_negando_bloqueia(self):
        with edit_approval_requester(lambda p: False):
            msg = require_edit_approval(self.prop())
        self.assertIn("denied", msg)

    def test_INVARIANTE_14_excecao_no_aprovador_NEGA(self):
        """Um aprovador que quebra não pode virar um aprovador que aceita."""

        def quebra(p):
            raise RuntimeError("boom")

        with edit_approval_requester(quebra):
            msg = require_edit_approval(self.prop())
        self.assertIsNotNone(msg)
        self.assertIn("denied", msg)

    def test_o_ContextVar_e_restaurado_ao_sair(self):
        with edit_approval_requester(lambda p: False):
            pass
        self.assertIsNone(require_edit_approval(self.prop()))

    def test_o_contexto_e_restaurado_mesmo_com_excecao(self):
        with self.assertRaises(RuntimeError), edit_approval_requester(lambda p: False):
            raise RuntimeError("x")
        self.assertIsNone(require_edit_approval(self.prop()))


class SessaoTests(unittest.TestCase):
    def test_os_dez_metodos(self):
        self.assertEqual(len(SESSION_METHODS), 10)
        for esperado in (
            "fork_session",
            "resume_session",
            "list_sessions",
            "set_session_model",
            "set_session_mode",
            "set_config_option",
        ):
            with self.subTest(m=esperado):
                self.assertIn(esperado, SESSION_METHODS)

    def test_a_versao_respondida_e_SEMPRE_a_propria(self):
        """Espelhar a do cliente seria prometer um protocolo que o agente não
        implementa, e o editor usaria recursos inexistentes."""
        for recebida in (0, 1, 99):
            with self.subTest(recebida=recebida):
                self.assertEqual(negotiate_version(recebida), PROTOCOL_VERSION)

    def test_as_capacidades_anunciadas(self):
        c = Capabilities()
        self.assertTrue(c.load_session and c.prompt_image)
        self.assertTrue(c.session_fork and c.session_list and c.session_resume)

    def test_file_uri_vira_Path(self):
        self.assertEqual(path_from_file_uri("file:///tmp/a%20b.txt"), Path("/tmp/a b.txt"))
        self.assertIsNone(path_from_file_uri("https://exemplo/x"))

    def test_classificacao_por_MIME(self):
        self.assertEqual(classify_resource("image/png"), BlockKind.IMAGE)
        self.assertEqual(classify_resource("text/plain"), BlockKind.TEXT)
        self.assertEqual(classify_resource("application/json"), BlockKind.TEXT)

    def test_ADIVINHA_pela_extensao_quando_o_MIME_falta(self):
        """Editores omitem o MIME com frequência; sem o palpite, um .png
        anexado viraria texto binário no prompt."""
        self.assertEqual(classify_resource(None, "foto.png"), BlockKind.IMAGE)
        self.assertEqual(classify_resource(None, "notas.txt"), BlockKind.TEXT)

    def test_decodificacao_TOLERANTE_de_bytes(self):
        """Um byte inválido não pode derrubar o turno: o usuário anexou o
        arquivo justamente para que o agente o olhasse."""
        self.assertEqual(decode_text_bytes("olá".encode()), "olá")
        self.assertIsInstance(decode_text_bytes(b"\xff\xfe\x00ruim"), str)

    def test_as_quatro_formas_de_bloco_viram_partes(self):
        partes = content_blocks_to_parts(
            [
                ContentBlock(BlockKind.TEXT, text="oi"),
                ContentBlock(BlockKind.IMAGE, data=b"\x89PNG", mime="image/png"),
                ContentBlock(BlockKind.EMBEDDED_RESOURCE, data=b"conteudo"),
                ContentBlock(BlockKind.RESOURCE_LINK, uri="file:///x.md"),
            ]
        )
        tipos = [p["type"] for p in partes]
        self.assertEqual(tipos, ["text", "image_url", "text", "text"])
        self.assertTrue(partes[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("file:///x.md", partes[3]["text"])


if __name__ == "__main__":
    unittest.main()
