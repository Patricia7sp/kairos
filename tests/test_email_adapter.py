"""O adapter de e-mail provado no fio SMTP de verdade.

`EmailAdapter` fala smtplib contra um `SmtpSink` em loopback: HELO/EHLO, AUTH
PLAIN, MAIL FROM, RCPT TO, DATA, QUIT. Sem mocks de biblioteca — um envio que
alega sucesso aqui teve o corpo recebido de verdade. `verify()` conecta e faz
EHLO reais e nunca envia; `send()` mapeia recusas do servidor para os ramos
permanente/transitório do `SendResult`.
"""

from __future__ import annotations

import unittest

from smtp_sink import SmtpSink, porta_livre

from kairos_gateway.adapters.email_adapter import EmailAdapter


class EmailAdapterTests(unittest.TestCase):
    def _adapter(self, sink: SmtpSink, **kwargs) -> EmailAdapter:
        opcoes = {
            "host": sink.host,
            "port": sink.port,
            "tls": False,
            "from_addr": "remetente@test",
            "password": "senha-smtp",
        }
        opcoes.update(kwargs)
        return EmailAdapter(**opcoes)

    def test_envio_entrega_corpo_e_cabecalhos_no_fio_smtp(self):
        with SmtpSink() as sink:
            resultado = self._adapter(sink).send("email:destino@test", "mensagem a entregar")
            self.assertTrue(resultado.ok, resultado.error_kind)
            self.assertEqual(len(sink.capturadas), 1)
            entrega = sink.capturadas[0]
            self.assertIn("destino@test", entrega["rcpt"])
            self.assertIn("From: remetente@test", entrega["data"])
            self.assertIn("To: destino@test", entrega["data"])
            self.assertIn("Subject: Kairos", entrega["data"])
            self.assertIn("mensagem a entregar", entrega["data"])

    def test_verify_conecta_de_verdade_mas_nunca_envia(self):
        with SmtpSink() as sink:
            resultado = self._adapter(sink).verify()
            self.assertTrue(resultado["ok"], resultado["message"])
            self.assertEqual(sink.capturadas, [], "verify não pode gerar mensagem no fio")
            self.assertNotIn("AUTH", sink.comandos)

    def test_verify_com_tls_exige_starttls_sem_autenticar_ou_enviar(self):
        with SmtpSink() as sink:
            resultado = self._adapter(sink, tls=True).verify()
            self.assertFalse(resultado["ok"])
            self.assertNotIn("AUTH", sink.comandos)
            self.assertEqual(sink.capturadas, [])
            self.assertIn("QUIT", sink.comandos)

    def test_verify_recusa_ehlo_negativo(self):
        with SmtpSink() as sink:
            sink.recusar_ehlo = True
            resultado = self._adapter(sink).verify()
            self.assertFalse(resultado["ok"])
            self.assertNotIn("EHLO recusado", resultado["message"])
            self.assertEqual(sink.capturadas, [])

    def test_send_recusa_ehlo_inicial_sem_auth_e_fecha_conexao(self):
        with SmtpSink() as sink:
            sink.recusar_ehlo = True
            resultado = self._adapter(sink).send("email:destino@test", "x")
            self.assertFalse(resultado.ok)
            self.assertEqual(resultado.error_kind, "smtp")
            self.assertNotIn("AUTH", sink.comandos)
            self.assertEqual(sink.capturadas, [])
            self.assertIn("QUIT", sink.comandos)
            self.assertTrue(sink.conexao_fechada.wait(1))

    def test_falha_de_tls_fecha_conexao_sem_auth_ou_envio(self):
        with SmtpSink() as sink:
            resultado = self._adapter(sink, tls=True).send("email:destino@test", "x")
            self.assertFalse(resultado.ok)
            self.assertEqual(resultado.error_kind, "smtp")
            self.assertNotIn("AUTH", sink.comandos)
            self.assertEqual(sink.capturadas, [])
            self.assertIn("QUIT", sink.comandos)

    def test_autenticacao_recusada_fecha_conexao(self):
        with SmtpSink() as sink:
            sink.auth_invalida = True
            resultado = self._adapter(sink).send("email:destino@test", "x")
            self.assertFalse(resultado.ok)
            self.assertEqual(resultado.error_kind, "auth")
            self.assertIn("QUIT", sink.comandos)
            self.assertTrue(sink.conexao_fechada.wait(1))

    def test_quit_recusado_fecha_socket_apos_envio_aceito(self):
        with SmtpSink() as sink:
            sink.recusar_quit = True
            resultado = self._adapter(sink).send("email:destino@test", "x")
            self.assertTrue(resultado.ok, resultado.error_kind)
            self.assertEqual(len(sink.capturadas), 1)
            self.assertTrue(sink.conexao_fechada.wait(1))

    def test_alvo_sem_arroba_recusa_antes_de_conectar(self):
        with SmtpSink() as sink:
            resultado = self._adapter(sink).send("email:sem-arroba", "x")
            self.assertFalse(resultado.ok)
            self.assertFalse(resultado.retryable)
            self.assertEqual(resultado.error_kind, "destino_invalido")
            self.assertEqual(sink.capturadas, [], "alvo inválido não conversa com o servidor")

    def test_destinatario_rejeitado_e_permanente(self):
        with SmtpSink() as sink:
            sink.recusar_rcpt = True
            resultado = self._adapter(sink).send("email:bloqueado@test", "x")
            self.assertFalse(resultado.ok)
            self.assertFalse(resultado.retryable)
            self.assertEqual(resultado.error_kind, "destino_rejeitado")

    def test_autenticacao_recusada_e_permanente(self):
        with SmtpSink() as sink:
            sink.auth_invalida = True
            resultado = self._adapter(sink).send("email:destino@test", "x")
            self.assertFalse(resultado.ok)
            self.assertFalse(resultado.retryable)
            self.assertEqual(resultado.error_kind, "auth")

    def test_servidor_fora_do_ar_e_transitorio(self):
        porta = porta_livre()
        resultado = EmailAdapter(
            host="127.0.0.1",
            port=porta,
            tls=False,
            from_addr="r@test",
            password="x",  # noqa: S106 - fixture sintética de teste
        ).send("email:destino@test", "x")
        self.assertFalse(resultado.ok)
        self.assertTrue(resultado.retryable)
        self.assertEqual(resultado.error_kind, "rede")

    def test_assunto_personalizado_nos_cabecalhos(self):
        with SmtpSink() as sink:
            adapter = self._adapter(sink, subject_default="Aviso do Kairos")
            adapter.send("email:d@test", "aviso")
            self.assertIn("Subject: Aviso do Kairos", sink.capturadas[0]["data"])


if __name__ == "__main__":
    unittest.main()
