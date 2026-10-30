"""STARTTLS real em loopback, com certificados temporários e smtplib real."""

from __future__ import annotations

import ipaddress
import os
import ssl
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from smtp_sink import SmtpSink

from kairos_gateway.adapters.email_adapter import EmailAdapter


class EmailTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        root = Path(cls._tmp.name)
        now = datetime.now(UTC)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SMTP test CA")])
        ca = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        cls.ca_file = root / "ca.pem"
        cls.ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        cls.contexts = {}
        for label, san in (
            ("valido", x509.IPAddress(ipaddress.ip_address("127.0.0.1"))),
            ("nome_invalido", x509.DNSName("outro.invalid")),
        ):
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            cert = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)]))
                .issuer_name(ca_name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                .add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
                )
                .sign(ca_key, hashes.SHA256())
            )
            cert_file = root / f"{label}.pem"
            cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_file = root / f"{label}.key"
            key_file.touch(mode=0o600)
            key_file.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_file, key_file)
            cls.contexts[label] = context

    def _adapter(self, sink):
        return EmailAdapter(
            host=sink.host,
            port=sink.port,
            from_addr="remetente@test",
            password="senha-smtp",  # noqa: S106 - fixture sintética de teste
            timeout=2,
        )

    def test_verify_negocia_tls_confiavel_sem_auth_ou_mensagem(self):
        with (
            patch.dict(os.environ, {"SSL_CERT_FILE": str(self.ca_file)}),
            SmtpSink(tls_context=self.contexts["valido"]) as sink,
        ):
            resultado = self._adapter(sink).verify()
            self.assertTrue(resultado["ok"], resultado["message"])
            self.assertTrue(sink.tls_estabelecido.is_set())
            self.assertEqual(sink.comandos, ["EHLO", "STARTTLS", "EHLO", "QUIT"])
            self.assertEqual(sink.capturadas, [])
            self.assertTrue(sink.conexao_fechada.wait(1))

    def test_send_autentica_e_entrega_apenas_apos_tls_confiavel(self):
        with (
            patch.dict(os.environ, {"SSL_CERT_FILE": str(self.ca_file)}),
            SmtpSink(tls_context=self.contexts["valido"]) as sink,
        ):
            resultado = self._adapter(sink).send("email:destino@test", "corpo protegido")
            self.assertTrue(resultado.ok, resultado.error_kind)
            self.assertTrue(sink.tls_estabelecido.is_set())
            self.assertLess(sink.comandos.index("STARTTLS"), sink.comandos.index("AUTH"))
            self.assertLess(sink.comandos.index("AUTH"), sink.comandos.index("DATA"))
            self.assertIn("corpo protegido", sink.capturadas[0]["data"])

    def test_certificado_nao_confiavel_e_recusado_sem_auth_ou_mensagem(self):
        for operacao in ("verify", "send"):
            with (
                self.subTest(operacao=operacao),
                SmtpSink(tls_context=self.contexts["valido"]) as sink,
            ):
                self._assert_recusa(sink, operacao)
                self.assertTrue(sink.conexao_fechada.wait(1))
                self.assertTrue(sink.falhas_tls)

    def test_nome_divergente_e_recusado_mesmo_com_ca_confiavel(self):
        for operacao in ("verify", "send"):
            with (
                self.subTest(operacao=operacao),
                patch.dict(os.environ, {"SSL_CERT_FILE": str(self.ca_file)}),
                SmtpSink(tls_context=self.contexts["nome_invalido"]) as sink,
            ):
                self._assert_recusa(sink, operacao)
                self.assertTrue(sink.conexao_fechada.wait(1))
                self.assertTrue(sink.falhas_tls)

    def test_starttls_anunciado_mas_recusado_nao_faz_downgrade(self):
        for operacao in ("verify", "send"):
            with (
                self.subTest(operacao=operacao),
                SmtpSink(tls_context=self.contexts["valido"]) as sink,
            ):
                sink.recusar_tls = True
                self._assert_recusa(sink, operacao)
                self.assertIn("STARTTLS", sink.comandos)
                self.assertIn("QUIT", sink.comandos)
                self.assertTrue(sink.conexao_fechada.wait(1))

    def test_ehlo_apos_tls_recusado_nao_verifica_nem_autentica(self):
        for operacao in ("verify", "send"):
            with (
                self.subTest(operacao=operacao),
                patch.dict(os.environ, {"SSL_CERT_FILE": str(self.ca_file)}),
                SmtpSink(tls_context=self.contexts["valido"]) as sink,
            ):
                sink.recusar_ehlo_tls = True
                self._assert_recusa(sink, operacao)
                self.assertIn("QUIT", sink.comandos)
                self.assertTrue(sink.conexao_fechada.wait(1))

    def _assert_recusa(self, sink, operacao):
        adapter = self._adapter(sink)
        if operacao == "verify":
            resultado = adapter.verify()
            self.assertFalse(resultado["ok"])
        else:
            resultado = adapter.send("email:destino@test", "x")
            self.assertFalse(resultado.ok)
            self.assertEqual(resultado.error_kind, "smtp")
        self.assertNotIn("AUTH", sink.comandos)
        self.assertEqual(sink.capturadas, [])


if __name__ == "__main__":
    unittest.main()
