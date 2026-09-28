"""`kairos mcp serve` — servidor stdio JSON-RPC com efeito real.

Comportamento, não snapshot: o servidor é exercitado de verdade num
subprocesso (`python -m kairos_cli.main mcp serve`), um cliente fala o
protocolo no stdin/stdout, e as asserções descrevem o contrato — handshake,
catálogo, conversas reais da `state.db`, baseline do `EventBridge` e as
recusas honestas do fail-closed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.messages import MessageRepository
from kairos_state.repositories.sessions import SessionRepository


def _seed(home: Path) -> None:
    db = connect(home / "state.db")
    try:
        migrate(db)
        sessoes = SessionRepository(db)
        mensagens = MessageRepository(db)
        sessoes.create("sess-visivel", source="web", display_name="Projeto Alfa")
        sessoes.create("sess-oculta", source="web", hidden=1)
        mensagens.append("sess-visivel", "user", content="saldos do mês completo")
        mensagens.append("sess-visivel", "assistant", content="resumo concluído")
        mensagens.append("sess-oculta", "user", content="mensagem oculta")
    finally:
        db.close()


class _Resp:
    def __init__(self, raw: dict) -> None:
        self.raw = raw

    @property
    def is_error(self) -> bool:
        return self.raw.get("isError", False)

    @property
    def text(self) -> str:
        blocos = self.raw.get("content", [])
        return "".join(bloco.get("text", "") for bloco in blocos if isinstance(bloco, dict))

    @property
    def payload(self) -> object:
        texto = self.text
        if not texto:
            return None
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            return texto


class _MCPClient:
    """Cliente mínimo sobre o servidor real — espelho do runtime do projeto."""

    def __init__(self, home: Path) -> None:
        env = dict(os.environ)
        env["KAIROS_HOME"] = str(home)
        self._next = 0
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "kairos_cli.main", "mcp", "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        self._stdin = self.proc.stdin
        self._stdout = self.proc.stdout

    def write_line(self, linha: str) -> None:
        self._stdin.write(linha + "\n")
        self._stdin.flush()

    def _send(self, payload: dict) -> None:
        self.write_line(json.dumps(payload, ensure_ascii=False))

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next += 1
        rid = self._next
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        while True:
            msg = self._read_message()
            if msg.get("id") == rid:
                return msg

    def read_message(self) -> dict:
        linha = self._stdout.readline()
        if not linha:
            raise AssertionError("servidor encerrou antes da resposta")
        return json.loads(linha)

    def _read_message(self) -> dict:
        return self.read_message()

    def call_tool(self, name: str, arguments: dict | None = None) -> _Resp:
        resposta = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return _Resp(resposta["result"])

    def close(self) -> int:
        try:
            self._stdin.close()
            return self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout=15)
        finally:
            self._stdout.close()


class _HomeBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name
        self.home = Path(self._tmp.name)
        _seed(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = self._saved
        self._tmp.cleanup()


class CatalogoTests(unittest.TestCase):
    def test_o_catalogo_e_exatamente_o_que_o_contrato_publica(self):
        from kairos_mcp.serve import tool_catalog
        from kairos_mcp.server import SERVER_TOOLS

        self.assertEqual({s.name for s in tool_catalog()}, set(SERVER_TOOLS))

    def test_attachments_list_deixou_de_ser_publicada(self):
        from kairos_mcp.serve import tool_catalog
        from kairos_mcp.server import UNPUBLISHED_TOOLS

        nomes = {s.name for s in tool_catalog()}
        self.assertNotIn("attachments_list", nomes)
        self.assertIn("attachments_list", UNPUBLISHED_TOOLS)
        self.assertGreater(len(UNPUBLISHED_TOOLS["attachments_list"]), 40)

    def test_toda_espec_tem_handler_real_e_schema(self):
        from kairos_mcp.serve import MCPServer, tool_catalog

        for spec in tool_catalog():
            with self.subTest(tool=spec.name):
                self.assertTrue(spec.description.strip(), "descrição vazia")
                self.assertEqual(spec.input_schema.get("type"), "object")
                self.assertTrue(callable(getattr(MCPServer, spec.handler, None)))


class ServidorStdioTests(_HomeBase):
    def test_handshake_e_catalogo_sem_anexos(self):
        from kairos_mcp.server import SERVER_TOOLS

        client = _MCPClient(self.home)
        try:
            init = client.request("initialize")
            client.notify("notifications/initialized")
            self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
            self.assertEqual(init["result"]["serverInfo"]["name"], "kairos")

            lista = client.request("tools/list")
            ferramentas = {t["name"] for t in lista["result"]["tools"]}
            self.assertEqual(ferramentas, set(SERVER_TOOLS))
            self.assertNotIn("attachments_list", ferramentas)
            for tool in lista["result"]["tools"]:
                with self.subTest(tool=tool["name"]):
                    self.assertIsInstance(tool.get("inputSchema"), dict)
        finally:
            client.close()

    def test_listar_ler_detalhar_e_buscar_sessoes_reais(self):
        client = _MCPClient(self.home)
        try:
            client.request("initialize")
            client.notify("notifications/initialized")

            listada = client.call_tool("conversations_list")
            self.assertFalse(listada.is_error)
            assert isinstance(listada.payload, dict)
            self.assertEqual(listada.payload["count"], 1)
            self.assertEqual(listada.payload["conversations"][0]["session_key"], "sess-visivel")

            ocultas = client.call_tool("conversations_list", {"search": "oculta"})
            assert isinstance(ocultas.payload, dict)
            self.assertEqual(ocultas.payload.get("count"), 0, "sessão oculta não aparece")

            lida = client.call_tool("conversation_read", {"session_key": "sess-visivel"})
            self.assertFalse(lida.is_error)
            assert isinstance(lida.payload, dict)
            self.assertEqual(lida.payload["count"], 2)
            self.assertEqual(lida.payload["messages"][0]["role"], "user")
            self.assertEqual(lida.payload["messages"][1]["role"], "assistant")

            detalhe = client.call_tool("session_info", {"session_key": "sess-visivel"})
            self.assertFalse(detalhe.is_error)
            assert isinstance(detalhe.payload, dict)
            sessao = detalhe.payload["session"]
            self.assertEqual(sessao["source"], "web")
            self.assertEqual(sessao["display_name"], "Projeto Alfa")
            self.assertGreaterEqual(sessao["message_count"], 2)

            busca = client.call_tool("conversation_search", {"term": "saldo"})
            self.assertFalse(busca.is_error)
            assert isinstance(busca.payload, dict)
            self.assertGreaterEqual(busca.payload["count"], 1)
            self.assertEqual(busca.payload["results"][0]["session_id"], "sess-visivel")

            inexistente = client.call_tool("conversation_read", {"session_key": "nao-existe"})
            self.assertTrue(inexistente.is_error)
            self.assertIn("não encontrada", inexistente.text)
        finally:
            client.close()

    def test_events_poll_baseline_corta_historico_e_sessao_nova_emite(self):
        client = _MCPClient(self.home)
        try:
            client.request("initialize")
            client.notify("notifications/initialized")

            vazio = client.call_tool("events_poll", {"after_cursor": 0})
            self.assertFalse(vazio.is_error)
            assert isinstance(vazio.payload, dict)
            self.assertEqual(vazio.payload["count"], 0)
            self.assertEqual(vazio.payload["next_cursor"], 0)

            db = connect(self.home / "state.db")
            try:
                migrate(db)
                SessionRepository(db).create("sess-nova", source="web")
                MessageRepository(db).append("sess-nova", "user", content="primeira fala")
            finally:
                db.close()

            novidade = client.call_tool("events_poll", {"after_cursor": 0})
            self.assertFalse(novidade.is_error)
            assert isinstance(novidade.payload, dict)
            self.assertEqual(novidade.payload["count"], 1)
            evento = novidade.payload["events"][0]
            self.assertEqual(evento["session_key"], "sess-nova")
            self.assertEqual(evento["type"], "message")
            cursor = novidade.payload["next_cursor"]
            self.assertGreater(cursor, 0)

            depois = client.call_tool("events_poll", {"after_cursor": cursor})
            assert isinstance(depois.payload, dict)
            self.assertEqual(depois.payload.get("count"), 0)
        finally:
            client.close()

    def test_message_send_recusa_honesta_sem_plataforma_entregavel(self):
        client = _MCPClient(self.home)
        try:
            client.request("initialize")
            client.notify("notifications/initialized")

            sem_alvo = client.call_tool("messages_send", {"target": "telegram", "message": "oi"})
            self.assertTrue(sem_alvo.is_error)
            self.assertIn("plataforma:destino", sem_alvo.text)

            nao_entregavel = client.call_tool(
                "messages_send", {"target": "telegram:12345", "message": "oi"}
            )
            self.assertTrue(nao_entregavel.is_error)
            self.assertIn("não está entregável", nao_entregavel.text)
        finally:
            client.close()

    def test_protocolo_fechado_recusa_sem_derrubar_o_servidor(self):
        client = _MCPClient(self.home)
        try:
            client.request("initialize")
            client.notify("notifications/initialized")

            desconhecido = client.request("cafe")
            self.assertEqual(desconhecido["error"]["code"], -32601)

            ping = client.request("ping")
            self.assertEqual(ping["result"], {})

            fantasma = client.call_tool("ferramenta_fantasma")
            self.assertTrue(fantasma.is_error)
            self.assertIn("desconhecida", fantasma.text)

            client.write_line("{lixo")
            parse = client.read_message()
            self.assertEqual(parse["error"]["code"], -32700)
        finally:
            client.close()

    def test_eof_encerra_com_zero(self):
        client = _MCPClient(self.home)
        client.request("initialize")
        client.notify("notifications/initialized")
        self.assertEqual(client.close(), 0)


if __name__ == "__main__":
    unittest.main()
