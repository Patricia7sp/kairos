"""Login, reinício e requisições reais dos adapters com segredos fictícios."""

from __future__ import annotations

import io
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from test_credential_vault import MemoryKeyring

from kairos_cli.main import main
from kairos_integration import InteractionEnvelope, build_interaction_service
from kairos_providers.adapter_contract import AdapterRequest, ProviderError, ProviderErrorKind
from kairos_providers.composition import build_provider_gateway
from kairos_providers.contracts import ProviderModelRef
from kairos_providers.curated_catalog import curated_models
from kairos_security.credentials import (
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    LegacyCredentialMigration,
    VaultError,
    build_credential_service,
)

OPENAI_REF = next(
    model.ref
    for model in curated_models()
    if model.ref.provider == "openai" and model.capabilities.chat
)


@contextmanager
def credential_home(backend):
    with TemporaryDirectory() as directory:
        home = Path(directory)
        passphrase = home / "passphrase"
        passphrase.write_text("fictitious-passphrase")
        passphrase.chmod(0o600)
        client = MemoryKeyring()
        with (
            patch.dict(
                os.environ,
                {
                    "KAIROS_HOME": str(home),
                    "KAIROS_DISABLE_KEYRING": "1" if backend == "encrypted" else "0",
                    "KAIROS_VAULT_PASSPHRASE_FILE": str(passphrase),
                },
            ),
            patch("keyring.get_keyring", return_value=client),
        ):
            yield home, client


def put(home, provider, credential_id, value):
    build_credential_service(home).put(
        CredentialRef(provider, credential_id), CredentialSecret({"api_key": value})
    )


def login(provider="openai", value="fictitious-new"):
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = main(["login", "--provider", provider, "--api-key", value])
    assert value not in output.getvalue(), "login expôs a chave"
    return code


class ActiveCredentialTests(unittest.IsolatedAsyncioTestCase):
    def gateway(self, home, *, status=200, on_request=None):
        observed = []
        statuses = iter(status) if isinstance(status, tuple) else None

        def handle(request):
            if request.headers.get("authorization") is not None:
                observed.append(request.headers.get("authorization"))
            if on_request is not None:
                on_request()
            response_status = next(statuses) if statuses is not None else status
            if response_status != 200:
                return httpx.Response(response_status)
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "local-model"}]})
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": OPENAI_REF.model}]})
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(
                    200,
                    text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
                )
            event = {"type": "response.completed", "response": {"status": "completed"}}
            return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

        gateway = build_provider_gateway(
            home,
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )
        self.addAsyncCleanup(gateway.aclose)
        return gateway, observed

    async def request(self, adapter):
        return [
            event
            async for event in adapter.stream(
                AdapterRequest(ProviderModelRef(adapter.descriptor.id, "gpt-4.1"), ())
            )
        ]

    async def do_login(self, **kwargs):
        import asyncio

        self.assertEqual(await asyncio.to_thread(login, **kwargs), 0)

    async def test_login_entrega_primary_ao_adapter_e_persiste_apos_reinicio(self):
        for backend in ("keyring", "encrypted"):
            with self.subTest(backend=backend), credential_home(backend) as (home, _client):
                put(home, "openai", "legacy-1", "fictitious-old")
                put(home, "openai", "other", "fictitious-other")
                gateway, observed = self.gateway(home)
                # Abre o serviço antes do login: ele também precisa observar a atualização.
                gateway.credential_management_state("openai")
                await self.do_login()
                prepared = gateway.prepare(OPENAI_REF)
                self.assertEqual(prepared.credential_id, "primary")
                await self.request(prepared.create_adapter())
                self.assertTrue(
                    observed == ["Bearer fictitious-new"], "adapter recebeu conta antiga"
                )
                await gateway.aclose()
                restarted, observed = self.gateway(home)
                await self.request(restarted.prepare(OPENAI_REF).create_adapter())
                self.assertTrue(observed == ["Bearer fictitious-new"], "reinício perdeu seleção")
                self.assertEqual(
                    {
                        entry.ref.credential_id
                        for entry in build_credential_service(home).list("openai")
                    },
                    {"legacy-1", "other", "primary"},
                )

    async def test_criacao_descoberta_e_conexao_usam_a_mesma_primary(self):
        with credential_home("encrypted") as (home, _client):
            put(home, "openai", "legacy-1", "fictitious-old")
            put(home, "openai", "other", "fictitious-other")
            await self.do_login()
            gateway, observed = self.gateway(home)
            await self.request(gateway.create_adapter(OPENAI_REF))
            await gateway.refresh("openai")
            self.assertTrue((await gateway.test_connection("openai")).ok)
            self.assertTrue((await gateway.test_all_connections())["openai"].ok)
            self.assertTrue(
                observed and all(value == "Bearer fictitious-new" for value in observed),
                "caminhos divergiram na conta",
            )

    async def test_migracao_seguida_de_login_e_requisicao_em_ambos_os_cofres(self):
        for backend in ("keyring", "encrypted"):
            with self.subTest(backend=backend), credential_home(backend) as (home, _client):
                path = home / "auth.json"
                path.write_text(
                    json.dumps({"credential_pool": {"openai": [{"key": "fictitious-legacy"}]}})
                )
                migration = LegacyCredentialMigration(path, build_credential_service(home))
                self.assertEqual(migration.import_and_verify().credentials_verified, 1)
                migration.finalize(confirm=True)
                await self.do_login()
                gateway, observed = self.gateway(home)
                await self.request(gateway.prepare(OPENAI_REF).create_adapter())
                self.assertTrue(
                    observed == ["Bearer fictitious-new"], "requisição usou a credencial migrada"
                )
                self.assertTrue(
                    build_credential_service(home)
                    .get(CredentialRef("openai", "legacy-1"))
                    .reveal()["api_key"]
                    == "fictitious-legacy"
                )

    async def test_uma_legada_e_aceita_sem_primary(self):
        with credential_home("encrypted") as (home, _client):
            put(home, "openai", "legacy-1", "fictitious-only")
            gateway, observed = self.gateway(home)
            prepared = gateway.prepare(OPENAI_REF)
            self.assertEqual(prepared.credential_id, "legacy-1")
            await self.request(prepared.create_adapter())
            await gateway.refresh("openai")
            self.assertTrue((await gateway.test_connection("openai")).ok)
            self.assertTrue(all(value == "Bearer fictitious-only" for value in observed))

    async def test_multiplas_legadas_recusam_sem_escolher_conta(self):
        with credential_home("encrypted") as (home, _client):
            put(home, "openai", "legacy-1", "fictitious-old")
            put(home, "openai", "other", "fictitious-other")
            gateway, observed = self.gateway(home)
            for operation in (
                lambda: gateway.prepare(OPENAI_REF),
                lambda: gateway.create_adapter(OPENAI_REF),
            ):
                with self.assertRaisesRegex(VaultError, "sele.*login"):
                    operation()
            with self.assertRaisesRegex(VaultError, "sele.*login"):
                await gateway.refresh("openai")
            status = await gateway.test_connection("openai")
            self.assertFalse(status.ok)
            self.assertRegex(status.message, "sele.*login")
            self.assertEqual(observed, [])

    async def test_primary_indisponivel_nao_usa_a_legada(self):
        with credential_home("keyring") as (home, client):
            put(home, "openai", "legacy-1", "fictitious-old")
            await self.do_login()
            del client.passwords[("kairos/openai", "primary")]
            gateway, observed = self.gateway(home)
            with self.assertRaises(CredentialNotFoundError):
                gateway.prepare(OPENAI_REF)
            with self.assertRaises(CredentialNotFoundError):
                gateway.create_adapter(OPENAI_REF)
            with self.assertRaises(CredentialNotFoundError):
                await gateway.refresh("openai")
            self.assertFalse((await gateway.test_connection("openai")).ok)
            self.assertEqual(observed, [])

    async def test_selecao_persistida_nao_cai_na_legada_se_primary_desaparecer(self):
        for backend in ("keyring", "encrypted"):
            with self.subTest(backend=backend), credential_home(backend) as (home, _client):
                put(home, "openai", "legacy-1", "fictitious-old")
                await self.do_login()
                build_credential_service(home).delete(CredentialRef("openai", "primary"))
                gateway, observed = self.gateway(home)
                with self.assertRaises(CredentialNotFoundError):
                    gateway.prepare(OPENAI_REF)
                self.assertFalse((await gateway.test_connection("openai")).ok)
                self.assertEqual(observed, [])

    async def test_keyring_indisponivel_nao_troca_para_primary_antiga_de_outro_cofre(self):
        from kairos_security.credentials import EncryptedFileVault

        with credential_home("keyring") as (home, client):
            encrypted = EncryptedFileVault(home / "credentials.vault")
            encrypted.initialize("fictitious-passphrase")
            encrypted.put(
                CredentialRef("openai", "primary"),
                CredentialSecret({"api_key": "fictitious-shadow"}),
            )
            await self.do_login()
            client.fail = True
            restarted, observed = self.gateway(home)
            with self.assertRaises(VaultError):
                restarted.prepare(OPENAI_REF)
            self.assertFalse((await restarted.test_connection("openai")).ok)
            self.assertEqual(observed, [])
            client.fail = False
            restored, observed = self.gateway(home)
            await self.request(restored.prepare(OPENAI_REF).create_adapter())
            self.assertTrue(
                observed == ["Bearer fictitious-new"], "recuperação selecionou outra conta"
            )

    async def test_keyring_recuperado_nao_substitui_conta_selecionada_no_criptografado(self):
        from kairos_security.credentials import SystemKeyringVault

        with credential_home("encrypted") as (home, client):
            keyring = SystemKeyringVault(client, index_path=home / "credential-index.json")
            keyring.put(
                CredentialRef("openai", "primary"),
                CredentialSecret({"api_key": "fictitious-shadow"}),
            )
            await self.do_login()
            os.environ["KAIROS_DISABLE_KEYRING"] = "0"
            restarted, observed = self.gateway(home)
            with self.assertRaises(VaultError):
                restarted.prepare(OPENAI_REF)
            self.assertFalse((await restarted.test_connection("openai")).ok)
            self.assertEqual(observed, [])
            os.environ["KAIROS_DISABLE_KEYRING"] = "1"
            restored, observed = self.gateway(home)
            await self.request(restored.prepare(OPENAI_REF).create_adapter())
            self.assertTrue(
                observed == ["Bearer fictitious-new"], "recuperação selecionou outra conta"
            )

    async def test_remocao_recusa_backend_diferente_sem_apagar_contas_ou_selecao(self):
        from kairos_security.credentials import EncryptedFileVault, SystemKeyringVault
        from kairos_security.credentials.profile import ProfileCredentialService

        for backend in ("keyring", "encrypted"):
            for operation in ("logout_provider", "logout_all", "remove_primary"):
                with (
                    self.subTest(backend=backend, operation=operation),
                    credential_home(backend) as (home, client),
                ):
                    if backend == "keyring":
                        alternate = EncryptedFileVault(home / "credentials.vault")
                        alternate.initialize("fictitious-passphrase")
                    else:
                        alternate = SystemKeyringVault(
                            client, index_path=home / "credential-index.json"
                        )
                    alternate.put(
                        CredentialRef("openai", "primary"),
                        CredentialSecret({"api_key": "fictitious-shadow"}),
                    )
                    await self.do_login()
                    original = (home / "auth.json").read_text()
                    if backend == "keyring":
                        client.fail = True
                    else:
                        os.environ["KAIROS_DISABLE_KEYRING"] = "0"
                    profile = ProfileCredentialService(home)
                    with self.assertRaises(VaultError):
                        if operation == "logout_provider":
                            profile.logout("openai")
                        elif operation == "logout_all":
                            profile.logout()
                        else:
                            profile.remove_primary("openai")
                    self.assertEqual((home / "auth.json").read_text(), original)
                    self.assertTrue(
                        alternate.get(CredentialRef("openai", "primary")).reveal()["api_key"]
                        == "fictitious-shadow",
                        "remoção apagou outra conta",
                    )
                    client.fail = False
                    os.environ["KAIROS_DISABLE_KEYRING"] = "1" if backend == "encrypted" else "0"
                    gateway, observed = self.gateway(home)
                    await self.request(gateway.prepare(OPENAI_REF).create_adapter())
                    self.assertTrue(
                        observed == ["Bearer fictitious-new"], "remoção perdeu a seleção original"
                    )

    async def test_falha_auth_e_tentativas_mantem_a_conta_fixada(self):
        with credential_home("encrypted") as (home, _client):
            put(home, "openai", "legacy-1", "fictitious-old")
            await self.do_login()
            gateway, observed = self.gateway(home, status=401)
            prepared = gateway.prepare(OPENAI_REF)
            for _attempt in range(2):
                with self.assertRaises(ProviderError) as caught:
                    await self.request(prepared.create_adapter())
                self.assertEqual(caught.exception.kind, ProviderErrorKind.AUTH)
                self.assertFalse(caught.exception.retryable)
                put(home, "openai", "primary", "fictitious-replacement")
            self.assertTrue(observed == ["Bearer fictitious-new"] * 2, "tentativa trocou de conta")
            self.assertEqual(prepared.credential_id, "primary")

    async def test_provedor_local_nao_abre_cofre_e_provedores_nao_herdam_conta(self):
        with credential_home("encrypted") as (home, _client):
            await self.do_login(provider="groq", value="fictitious-groq")
            await self.do_login()
            gateway, observed = self.gateway(home)
            with patch(
                "kairos_providers.composition.build_credential_service",
                side_effect=AssertionError("cofre local consultado"),
            ):
                local = gateway.prepare(ProviderModelRef("ollama", "local-model"))
                self.assertIsNone(local.credential_id)
                gateway.create_adapter(ProviderModelRef("ollama", "local-model"))
                await gateway.refresh("ollama")
                self.assertTrue((await gateway.test_connection("ollama")).ok)
            groq = gateway.prepare(ProviderModelRef("groq", "local-test"))
            self.assertEqual(groq.ref.provider, "groq")
            await self.request(groq.create_adapter())
            await self.request(gateway.prepare(OPENAI_REF).create_adapter())
            self.assertTrue(
                observed == ["Bearer fictitious-groq", "Bearer fictitious-new"],
                "houve herança entre provedores",
            )
            self.assertEqual(build_credential_service(home).list("anthropic"), [])

    async def test_rollback_do_login_preserva_a_primary_efetivamente_usada(self):
        for backend in ("keyring", "encrypted"):
            with self.subTest(backend=backend), credential_home(backend) as (home, _client):
                put(home, "openai", "legacy-1", "fictitious-old")
                await self.do_login()
                original = (home / "auth.json").read_text()
                from kairos_security.credentials import io as credential_io

                writer = credential_io.secure_atomic_write_text
                failed = False

                def fail_after_write(path, content, writer=writer):
                    nonlocal failed
                    writer(path, content)
                    if path.name == "auth.json" and not failed:
                        failed = True
                        raise OSError("synthetic metadata failure")

                import asyncio

                with patch.object(credential_io, "secure_atomic_write_text", fail_after_write):
                    self.assertNotEqual(
                        await asyncio.to_thread(login, value="fictitious-rejected"), 0
                    )
                self.assertEqual((home / "auth.json").read_text(), original)
                gateway, observed = self.gateway(home)
                await self.request(gateway.prepare(OPENAI_REF).create_adapter())
                self.assertTrue(
                    observed == ["Bearer fictitious-new"], "rollback alterou conta efetiva"
                )

    async def test_ambiguidade_e_publicada_como_erro_claro_do_turno(self):
        with credential_home("encrypted") as (home, _client):
            put(home, "openai", "legacy-1", "fictitious-old")
            put(home, "openai", "other", "fictitious-other")
            service = build_interaction_service(home)
            self.addAsyncCleanup(service.aclose)
            events = [
                event
                async for event in service.stream(
                    InteractionEnvelope("ambiguous", "test", "hello", override=OPENAI_REF)
                )
            ]
            self.assertEqual([event.kind for event in events], ["turn_error"])
            self.assertRegex(events[0].error, "sele.*login")
            self.assertFalse(events[0].retryable)

    async def test_ativa_ausente_ou_selecao_corrompida_publica_erro_sem_retry(self):
        for unavailable in ("missing", "invalid"):
            with (
                self.subTest(unavailable=unavailable),
                credential_home("encrypted") as (home, _client),
            ):
                put(home, "openai", "legacy-1", "fictitious-old")
                await self.do_login()
                if unavailable == "missing":
                    build_credential_service(home).delete(CredentialRef("openai", "primary"))
                else:
                    document = json.loads((home / "auth.json").read_text())
                    document["active_credentials"] = []
                    (home / "auth.json").write_text(json.dumps(document))
                service = build_interaction_service(home)
                self.addAsyncCleanup(service.aclose)
                events = [
                    event
                    async for event in service.stream(
                        InteractionEnvelope("unavailable", "test", "hello", override=OPENAI_REF)
                    )
                ]
                self.assertEqual([event.kind for event in events], ["turn_error"])
                self.assertEqual(events[0].error_kind, "auth")
                self.assertRegex(events[0].error, "indisponível.*login")
                self.assertFalse(events[0].retryable)

    async def test_preparacao_concorrente_nao_congela_login_que_sofre_rollback(self):
        import asyncio

        from kairos_security.credentials import CredentialService
        from kairos_security.credentials.profile import ProfileCredentialService

        for backend in ("keyring", "encrypted"):
            with self.subTest(backend=backend), credential_home(backend) as (home, _client):
                await self.do_login()
                gateway, observed = self.gateway(home)
                writing = threading.Event()
                release = threading.Event()
                selected = threading.Event()
                allow_get = threading.Event()
                active_ref = CredentialService.active_ref

                def fail_write(_service, _document, writing=writing, release=release):
                    writing.set()
                    if not release.wait(5):
                        raise TimeoutError("test release missing")
                    raise OSError("synthetic metadata failure")

                def pause_selection(
                    service, provider, active_ref=active_ref, selected=selected, allow_get=allow_get
                ):
                    ref = active_ref(service, provider)
                    selected.set()
                    if not allow_get.wait(5):
                        raise TimeoutError("test selection release missing")
                    return ref

                with (
                    patch.object(ProfileCredentialService, "_write", fail_write),
                    patch.object(CredentialService, "active_ref", pause_selection),
                    ThreadPoolExecutor(2) as pool,
                ):
                    prepared = pool.submit(gateway.prepare, OPENAI_REF)
                    try:
                        self.assertTrue(await asyncio.to_thread(selected.wait, 2))
                        rejected = pool.submit(login, value="fictitious-rejected")
                        # Sem a transação de leitura, o login alcança o write enquanto
                        # prepare está entre a seleção da conta e a leitura do segredo.
                        await asyncio.to_thread(writing.wait, 0.2)
                        allow_get.set()
                        frozen = await asyncio.to_thread(prepared.result, 2)
                    finally:
                        allow_get.set()
                        release.set()
                    self.assertNotEqual(await asyncio.to_thread(rejected.result, 2), 0)
                await self.request(frozen.create_adapter())
                self.assertTrue(
                    observed == ["Bearer fictitious-new"], "adapter reteve login rejeitado"
                )

    async def test_turno_real_fixa_primary_no_retry_e_na_falha_auth(self):
        for statuses, terminal in (((503, 200), "turn_end"), ((401,), "turn_error")):
            with self.subTest(statuses=statuses), credential_home("encrypted") as (home, _client):
                put(home, "openai", "legacy-1", "fictitious-old")
                await self.do_login()
                observed = []

                def factory(_home, statuses=statuses, observed=observed):
                    gateway, requests = self.gateway(
                        home,
                        status=statuses,
                        on_request=lambda: put(home, "openai", "primary", "fictitious-replacement"),
                    )
                    observed.append(requests)
                    return gateway

                with patch("kairos_integration.composition.build_provider_gateway", factory):
                    service = build_interaction_service(home)
                    try:
                        events = [
                            event
                            async for event in service.stream(
                                InteractionEnvelope(
                                    "fixed",
                                    "test",
                                    "hello",
                                    override=OPENAI_REF,
                                )
                            )
                        ]
                    finally:
                        await service.aclose()
                self.assertEqual(events[-1].kind, terminal)
                self.assertEqual(events[0].snapshot.credential_id, "primary")
                sent = [value for requests in observed for value in requests]
                self.assertTrue(
                    sent == ["Bearer fictitious-new"] * len(statuses),
                    "turno trocou de conta após login concorrente",
                )
