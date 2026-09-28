"""Adapter Email: entrega SMTP usando apenas a biblioteca padrão.

`_reversa_sdd/data-dictionary.md` (`email` em ``_KNOWN_DELIVERY_PLATFORMS``) —
adapter de plataforma registrado de fora do gateway, mesmo contrato dos demais
(`name` + `send` retornando `SendResult` estruturado, nunca exceção vazando).

Alvo: ``email:destino@exemplo.com``. Config não-secreta em ``messaging.json``
(``smtp_host``, ``smtp_port``, ``tls``, ``from_addr``, ``address_default``,
``subject_default``); a senha SMTP vive no cofre e o usuário de autenticação é
o próprio ``from_addr`` (decisão deliberada, ver `docs/decisoes.md` D-T28).

Divergências registradas:

- O legado dependia de ``EMAIL_HOME_ADDRESS`` (env var não documentada na
  spec); aqui o alvo home é ``address_default`` (config não-secreta) e se
  resolve pela superfície que testa/envia, nunca por variável de ambiente.
- ``verify()`` faz conexão + EHLO reais e **nunca envia**; provar autenticação
  é enviar um teste para o alvo home.
- Sem fonte de entrada (IMAP/POP3): este adapter é só o **canal de saída**.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from kairos_gateway.service import SendResult

__all__ = ["EmailAdapter"]


class EmailAdapter:
    name = "email"

    def __init__(
        self,
        host: str,
        *,
        password: str,
        from_addr: str,
        port: int = 587,
        tls: bool = True,
        subject_default: str = "Kairos",
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._password = password
        self._from_addr = from_addr
        self._subject_default = subject_default
        self._timeout = timeout

    def _destination(self, target: str) -> str | None:
        destino = target.split(":", 1)[1] if ":" in target else ""
        destino = destino.strip()
        if not destino or "@" not in destino:
            return None
        return destino

    def verify(self) -> dict[str, object]:
        """Conexão + EHLO reais. Não envia nada — credencial só se comprova
        com um envio de teste (alvo home), assumido pela superfície."""
        try:
            server = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        except (smtplib.SMTPException, OSError) as exc:
            return {"ok": False, "message": f"conexão SMTP falhou: {exc}"}
        try:
            server.ehlo()
        except (smtplib.SMTPException, OSError) as exc:
            return {"ok": False, "message": f"EHLO falhou: {exc}"}
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
        return {"ok": True, "message": f"SMTP {self._host}:{self._port} respondeu"}

    def send(self, target: str, payload: str) -> SendResult:
        destino = self._destination(target)
        if destino is None:
            return SendResult(ok=False, retryable=False, error_kind="destino_invalido")

        mensagem = EmailMessage()
        mensagem["From"] = self._from_addr
        mensagem["To"] = destino
        mensagem["Subject"] = self._subject_default
        mensagem.set_content(payload)

        try:
            server = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        except (smtplib.SMTPConnectError, ConnectionError):
            return SendResult(ok=False, retryable=True, error_kind="rede")
        except (smtplib.SMTPException, OSError):
            return SendResult(ok=False, retryable=True, error_kind="smtp")

        try:
            if self._tls:
                server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(self._from_addr, self._password)
        except smtplib.SMTPAuthenticationError:
            return SendResult(ok=False, retryable=False, error_kind="auth")
        except (smtplib.SMTPException, OSError):
            return SendResult(ok=False, retryable=True, error_kind="smtp")
        try:
            server.send_message(mensagem)
        except smtplib.SMTPRecipientsRefused:
            return SendResult(ok=False, retryable=False, error_kind="destino_rejeitado")
        except (smtplib.SMTPException, OSError):
            return SendResult(ok=False, retryable=True, error_kind="smtp")
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
        return SendResult(ok=True)
