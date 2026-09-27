"""Servidor SMTP mínimo em socket puro — sink de loopback para testes reais.

Cada teste abre um `SmtpSink`; o `EmailAdapter` real (smtplib) fala SMTP de
verdade sobre TCP com ele: HELO/EHLO, (opcional) STARTTLS/EHLO, AUTH PLAIN,
MAIL FROM, RCPT TO, DATA, QUIT. As mensagens completas ficam em `capturadas`
(com cabeçalhos e corpo). Flags de modo (`recusar_rcpt`, `auth_invalida`)
forçam respostas negativas para exercitar os ramos permanentes do adapter —
sem socket fake, sem monkeypatch de biblioteca: o caminho é o fio de verdade.
"""

from __future__ import annotations

import socketserver
import threading
from typing import Any

__all__ = ["SmtpSink", "porta_livre"]


class _SmtpHandler(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self._rcpt: list[str] = []
        self._data = []
        self._mail_from = ""
        self._logged_in = False

    def _send(self, line: str) -> None:
        self.request.sendall(f"{line}\r\n".encode("ascii"))

    def _readline(self) -> str:
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = self.request.recv(1)
            if not chunk:
                break
            raw += chunk
        return raw.decode("ascii", "replace").rstrip("\r\n")

    def handle(self) -> None:
        self._send("220 sink ESMTP")
        while True:
            comando, _, _arg = self._readline().partition(" ")
            if not comando:
                break
            acao = _COMANDOS.get(comando.upper())
            if acao is None:
                self._send("500 comando desconhecido")
                continue
            if acao(self, _arg.strip()):
                break

    def _ehlo(self, argumento: str) -> bool:
        self._send("250-sink")
        self._send("250-AUTH PLAIN LOGIN")
        self._send("250 8BITMIME")
        return False

    def _helo(self, argumento: str) -> bool:
        self._send("250 sink")
        return False

    def _auth(self, argumento: str) -> bool:
        sink = self.server.state  # type: ignore[attr-defined]
        mecanismo = argumento.split(" ", 1)[0].upper()
        if mecanismo == "PLAIN":
            if sink.auth_invalida:
                self._send("535 5.7.8 auth rejeitada")
                return False
            self._logged_in = True
            self._send("235 2.7.0 autenticado")
            return False
        if mecanismo == "LOGIN":
            if sink.auth_invalida:
                self._send("535 5.7.8 auth rejeitada")
                return False
            self._send("334 VXNlcm5hbWU6")
            self._readline()
            self._send("334 UGFzc3dvcmQ6")
            self._readline()
            self._logged_in = True
            self._send("235 2.7.0 autenticado")
            return False
        self._send("504 mecanismo não suportado")
        return False

    def _mail(self, argumento: str) -> bool:
        self._mail_from = argumento
        self._send("250 ok")
        return False

    def _rcpt(self, argumento: str) -> bool:
        sink = self.server.state  # type: ignore[attr-defined]
        if sink.recusar_rcpt:
            self._send("550 5.1.1 destinatario rejeitado")
            return False
        destino = argumento[3:] if argumento.lower().startswith("to:") else argumento
        self._rcpt.append(destino.strip(" <>"))
        self._send("250 ok")
        return False

    def _data(self, argumento: str) -> bool:
        sink = self.server.state  # type: ignore[attr-defined]
        self._send("354 ok")
        corpo = self._ler_dados()
        sink.capturadas.append(
            {
                "mail_from": self._mail_from.strip("<>"),
                "rcpt": list(self._rcpt),
                "data": corpo,
            }
        )
        self._send("250 ok, mensagem aceita")
        return False

    def _rset(self, argumento: str) -> bool:
        self._rcpt.clear()
        self._send("250 ok")
        return False

    def _noop(self, argumento: str) -> bool:
        self._send("250 ok")
        return False

    def _quit(self, argumento: str) -> bool:
        self._send("221 atencao")
        return True

    def _ler_dados(self) -> str:
        corpo = ""
        while True:
            linha = self._readline()
            if linha == ".":
                break
            corpo += (linha[1:] if linha.startswith(".") else linha) + "\n"
        return corpo


_COMANDOS = {
    "HELO": _SmtpHandler._helo,
    "EHLO": _SmtpHandler._ehlo,
    "AUTH": _SmtpHandler._auth,
    "MAIL": _SmtpHandler._mail,
    "RCPT": _SmtpHandler._rcpt,
    "DATA": _SmtpHandler._data,
    "RSET": _SmtpHandler._rset,
    "NOOP": _SmtpHandler._noop,
    "QUIT": _SmtpHandler._quit,
}


class _ThreadingServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    state: Any = None


class SmtpSink:
    """Context manager: sobe um servidor SMTP em loopback (porta efêmera).

    ``capturadas`` guarda cada mensagem aceita ({} usamos quando nada chega);
    ``recusar_rcpt`` e ``auth_invalida`` ligam os ramos de recusa permanente.
    """

    def __init__(self) -> None:
        self.capturadas: list[dict[str, Any]] = []
        self.recusar_rcpt = False
        self.auth_invalida = False
        self._server: _ThreadingServer | None = None

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.server_address[1])

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}"

    def __enter__(self) -> SmtpSink:
        self._server = _ThreadingServer((self.host, 0), _SmtpHandler)
        self._server.state = self
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()


def porta_livre() -> int:
    """Porta efêmera já liberada (para o ramo 'conexão recusada')."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
