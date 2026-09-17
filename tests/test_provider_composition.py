"""Composição local do gateway moderno e ponte temporária do manager."""

from __future__ import annotations

import asyncio
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kairos_providers.base import ConnectionStatus
from kairos_providers.catalog import ModelCatalog
from kairos_providers.composition import (
    ProviderCompositionConfig,
    build_provider_gateway,
)
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelPrice,
    ProviderModelRef,
)
from kairos_providers.manager import ProviderManager
from kairos_providers.provider_profiles import custom_profile


class EmptyCredentials:
    def list(self, provider: str):
        del provider
        return []

    def get(self, ref):
        raise AssertionError(f"não deve consultar o cofre: {ref}")


class TrackingClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FailingOnceClient(TrackingClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_attempts = 0

    async def aclose(self) -> None:
        self.close_attempts += 1
        if self.close_attempts == 1:
            raise RuntimeError("falha transitória no close")
        await super().aclose()


class CancellingOnceClient(FailingOnceClient):
    async def aclose(self) -> None:
        self.close_attempts += 1
        if self.close_attempts == 1:
            raise asyncio.CancelledError
        self.closed = True


class BarrierFailingOnceClient(TrackingClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_attempts = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.close_attempts += 1
        attempt = self.close_attempts
        self.started.set()
        await self.release.wait()
        if attempt == 1:
            raise RuntimeError("falha compartilhada")
        self.closed = True


class ThreadBarrierClient(TrackingClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_attempts = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    async def aclose(self) -> None:
        with self._lock:
            self.close_attempts += 1
            attempt = self.close_attempts
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.001)
        if attempt == 1:
            raise RuntimeError("falha cross-loop compartilhada")
        self.closed = True


def test_composition_registra_todos_os_providers(tmp_path):
    """A omissão de qualquer factory torna o provider impossível de selecionar."""
    gateway = build_provider_gateway(tmp_path)

    ids = [descriptor.id for descriptor in gateway.registry.list_descriptors()]

    assert ids == [
        "anthropic",
        "custom",
        "deepseek",
        "gemini",
        "groq",
        "mistral",
        "ollama",
        "openai",
        "openrouter",
        "perplexity",
        "together",
        "xai",
    ]


def test_provedores_canonicos_novos_expostos_com_credencial_de_api(tmp_path):
    """Mistral, xAI, Together e Perplexity aparecem no painel e exigem chave no cofre."""
    from kairos_web.provider_api import list_providers_payload

    gateway = build_provider_gateway(tmp_path)
    payload = list_providers_payload(gateway)
    by_id = {item["id"]: item for item in payload["providers"]}

    for provider, name in {
        "mistral": "Mistral AI",
        "xai": "xAI (Grok)",
        "together": "Together AI",
        "perplexity": "Perplexity",
    }.items():
        entry = by_id[provider]
        assert entry["name"] == name
        assert entry["requires_credential"] is True
        assert entry["auth_methods"] == ["api_key"]
        assert entry["configured"] is False


def test_catalogo_curado_declara_ferramentas_por_provider(tmp_path):
    """Perplexity (sonar) não anuncia tool calling; os demais novos anunciam."""
    gateway = build_provider_gateway(tmp_path)
    curated = {
        model.ref.provider: model
        for model in gateway.catalog.list_models()
        if model.ref.provider in {"mistral", "xai", "together", "perplexity"}
    }

    assert curated["mistral"].capabilities.tools is True
    assert curated["xai"].capabilities.tools is True
    assert curated["together"].capabilities.tools is True
    assert curated["perplexity"].capabilities.tools is False
    assert all(CatalogOrigin.CURATED in model.origins for model in curated.values())


def test_preparo_congela_rotas_de_billing_openrouter_e_custom(tmp_path, monkeypatch):
    """Usar uma base vazia coalesceria endpoints custom e esconderia o rateio real."""
    custom = custom_profile(
        base_url="https://models.example.test/v1",
        trusted_remote=True,
    )
    passphrase_file = tmp_path / "vault-passphrase"
    passphrase_file.write_text("senha-mestra-de-teste\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase_file))
    gateway = build_provider_gateway(
        tmp_path,
        config=ProviderCompositionConfig(custom=custom),
    )
    models = (
        CatalogModel(
            ref=ProviderModelRef("openrouter", "acme/chat"),
            display_name="Router",
            capabilities=ModelCapabilities(chat=True),
            price=ModelPrice(
                prompt=Decimal("0.000001"),
                completion=Decimal("0.000002"),
                request=Decimal("0"),
            ),
        ),
        CatalogModel(
            ref=ProviderModelRef("custom", "private-chat"),
            display_name="Custom",
            capabilities=ModelCapabilities(chat=True),
            price=ModelPrice(
                prompt=Decimal("0.000003"),
                completion=Decimal("0.000004"),
                request=Decimal("0.01"),
            ),
        ),
    )
    gateway.catalog.merge(models, origin=CatalogOrigin.DYNAMIC)

    openrouter = gateway.prepare(models[0].ref)
    prepared_custom = gateway.prepare(models[1].ref)

    assert openrouter.billing.provider == "openrouter"
    assert openrouter.billing.base_url == "https://openrouter.ai/api/v1"
    assert openrouter.billing.mode == "api_key"
    assert openrouter.price == models[0].price
    assert prepared_custom.billing.provider == "custom"
    assert prepared_custom.billing.base_url == "https://models.example.test/v1"
    assert prepared_custom.billing.mode == "api_key"
    assert prepared_custom.price == models[1].price


def test_manager_converte_catalogo_moderno_somente_na_borda_legada(tmp_path):
    """A conversão protege clientes legados de conhecerem CatalogModel."""
    gateway = build_provider_gateway(tmp_path)
    gateway.catalog.merge(
        [
            CatalogModel(
                ref=ProviderModelRef("openai", "gpt-edge"),
                display_name="GPT Edge",
                capabilities=ModelCapabilities(
                    chat=True,
                    tools=True,
                    vision=False,
                    streaming=True,
                    context_length=32_000,
                    max_output_tokens=4_000,
                ),
                origins=frozenset({CatalogOrigin.CURATED}),
                price=ModelPrice(prompt=Decimal("1.25"), completion=Decimal("2.50")),
            )
        ],
        origin=CatalogOrigin.CURATED,
    )

    models = ProviderManager(gateway=gateway).list_all_models()
    edge = next(model for model in models if model.id == "gpt-edge")

    assert edge.provider == "openai"
    assert edge.context_length == 32_000
    assert edge.max_output_tokens == 4_000
    assert edge.supports_tools is True
    assert edge.cost_input_per_million == 1.25
    assert edge.cost_output_per_million == 2.5


def test_listagem_nao_cria_clientes_nem_inicializa_cofre(tmp_path, monkeypatch):
    """Listar catálogo é leitura local e não deve abrir recursos de conexão."""
    passphrase_file = tmp_path / "vault-passphrase"
    passphrase_file.write_text("senha-mestra-de-teste\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    clients: list[TrackingClient] = []
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase_file))

    gateway = build_provider_gateway(
        tmp_path,
        client_factory=lambda: clients.append(TrackingClient()) or clients[-1],
    )
    ProviderManager(gateway=gateway).list_all_models()

    assert clients == []
    assert not (tmp_path / "credentials.vault").exists()
    asyncio.run(gateway.aclose())


class ProviderManagerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_sondagens_concorrentes_usam_gateways_efemeros_independentes(self):
        """Fechar uma sondagem não pode encerrar o cliente ainda usado pela outra."""

        class ProbeGateway:
            def __init__(self) -> None:
                self.client = TrackingClient()

            async def test_all_connections(self, **_kwargs):
                started.append(self)
                if len(started) == 1:
                    first_started.set()
                if len(started) == 2:
                    both_started.set()
                await release_probe.wait()
                returning.append(self)
                if len(returning) == 2:
                    both_returning.set()
                await allow_return.wait()
                if self.client.closed:
                    raise AssertionError("cliente fechado durante outra sondagem")
                return {"openai": ConnectionStatus(True, "openai", "ok", 1)}

            async def aclose(self):
                await self.client.aclose()

        created: list[ProbeGateway] = []
        started: list[ProbeGateway] = []
        returning: list[ProbeGateway] = []
        first_started = asyncio.Event()
        both_started = asyncio.Event()
        release_probe = asyncio.Event()
        both_returning = asyncio.Event()
        allow_return = asyncio.Event()

        def factory(_home: Path) -> ProbeGateway:
            gateway = ProbeGateway()
            created.append(gateway)
            return gateway

        with (
            TemporaryDirectory() as tmpdir,
            patch("kairos_providers.manager.build_provider_gateway", side_effect=factory),
        ):
            manager = ProviderManager(home=Path(tmpdir))
            first = asyncio.create_task(manager.test_all_connections())
            await first_started.wait()
            second = asyncio.create_task(manager.test_all_connections())
            await both_started.wait()

            self.assertEqual(len(created), 2)
            self.assertFalse(any(gateway.client.closed for gateway in created))
            release_probe.set()
            await both_returning.wait()
            self.assertFalse(any(gateway.client.closed for gateway in created))
            allow_return.set()
            results = await asyncio.gather(first, second)

        self.assertTrue(all(result["openai"].ok for result in results))
        self.assertTrue(all(gateway.client.closed for gateway in created))

    async def test_sondagens_sequenciais_criam_e_fecham_gateway_por_chamada(self):
        """Repetir a sondagem não reutiliza um gateway já fechado."""

        class Gateway:
            def __init__(self) -> None:
                self.closed = False

            async def test_all_connections(self, **_kwargs):
                return {}

            async def aclose(self):
                self.closed = True

        created: list[Gateway] = []

        def factory(_home: Path) -> Gateway:
            gateway = Gateway()
            created.append(gateway)
            return gateway

        with (
            TemporaryDirectory() as tmpdir,
            patch("kairos_providers.manager.build_provider_gateway", side_effect=factory),
        ):
            manager = ProviderManager(home=Path(tmpdir))
            await manager.test_all_connections()
            await manager.test_all_connections()

        self.assertEqual(len(created), 2)
        self.assertTrue(all(gateway.closed for gateway in created))
        self.assertIsNone(manager._gateway)

    async def test_sondagem_nao_fecha_gateway_guardado_para_listagem(self):
        """O gateway lazy de catálogo continua disponível após uma sondagem efêmera."""

        class ListingGateway:
            def __init__(self) -> None:
                self.catalog = ModelCatalog()
                self.closed = False

            async def test_all_connections(self, **_kwargs):
                raise AssertionError("gateway de listagem não deve ser usado para sondar")

            async def aclose(self):
                self.closed = True

        class ProbeGateway:
            closed = False

            async def test_all_connections(self, **_kwargs):
                return {}

            async def aclose(self):
                self.closed = True

        listing = ListingGateway()
        probe = ProbeGateway()
        with (
            TemporaryDirectory() as tmpdir,
            patch("kairos_providers.manager.build_provider_gateway", side_effect=[listing, probe]),
        ):
            manager = ProviderManager(home=Path(tmpdir))
            manager.list_all_models()
            await manager.test_all_connections()

        self.assertFalse(listing.closed)
        self.assertTrue(probe.closed)
        self.assertIs(manager._gateway, listing)

    async def test_fecha_gateway_criado_para_testar_conexoes(self):
        """Deixar o gateway efêmero aberto mantém clientes HTTP vivos após a sondagem."""

        class Gateway:
            closed = False

            async def test_all_connections(self, **_kwargs):
                return {}

            async def aclose(self):
                self.closed = True

        gateway = Gateway()
        with (
            TemporaryDirectory() as tmpdir,
            patch("kairos_providers.manager.build_provider_gateway", return_value=gateway),
        ):
            manager = ProviderManager(home=Path(tmpdir))
            await manager.test_all_connections()

        self.assertTrue(gateway.closed)
        self.assertIsNone(manager._gateway)

    async def test_nao_fecha_gateway_injetado(self):
        """O chamador continua dono de um gateway que ele injetou."""

        class Gateway:
            closed = False

            async def test_all_connections(self, **_kwargs):
                return {}

            async def aclose(self):
                self.closed = True

        gateway = Gateway()

        await ProviderManager(gateway=gateway).test_all_connections()

        self.assertFalse(gateway.closed)

    async def test_gateway_fecha_clientes_criados_e_suporta_context_manager(self):
        """Clientes criados pelas factories pertencem ao gateway composto."""
        clients: list[TrackingClient] = []

        def factory() -> TrackingClient:
            client = TrackingClient()
            clients.append(client)
            return client

        with TemporaryDirectory() as tmpdir:
            async with build_provider_gateway(Path(tmpdir), client_factory=factory) as gateway:
                gateway.create_adapter(ProviderModelRef("ollama", "llama3.3"))

        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].closed)

    async def test_gateway_tenta_todos_os_closes_e_permite_repetir_falhas(self):
        """Um cliente falhar não pode impedir os demais nem bloquear uma nova tentativa."""
        failing = FailingOnceClient()
        healthy = TrackingClient()
        clients = iter((failing, healthy))

        with TemporaryDirectory() as tmpdir:
            gateway = build_provider_gateway(
                Path(tmpdir),
                client_factory=lambda: next(clients),
            )
            gateway.registry.create("ollama")
            gateway.registry.create("openai", api_key="")

            with self.assertRaises(BaseExceptionGroup):
                await gateway.aclose()

            self.assertEqual(failing.close_attempts, 1)
            self.assertTrue(healthy.closed)
            with self.assertRaisesRegex(RuntimeError, "encerrado"):
                gateway.registry.create("ollama")

            await gateway.aclose()

        self.assertEqual(failing.close_attempts, 2)
        self.assertTrue(failing.closed)

    async def test_gateway_tenta_demais_clientes_apos_cancelamento(self):
        """Cancelamento de um close também deve preservar os demais e permitir retry."""
        cancelling = CancellingOnceClient()
        healthy = TrackingClient()
        clients = iter((cancelling, healthy))

        with TemporaryDirectory() as tmpdir:
            gateway = build_provider_gateway(
                Path(tmpdir),
                client_factory=lambda: next(clients),
            )
            gateway.registry.create("ollama")
            gateway.registry.create("openai", api_key="")

            with self.assertRaises(BaseExceptionGroup):
                await gateway.aclose()

            self.assertTrue(healthy.closed)
            await gateway.aclose()

        self.assertEqual(cancelling.close_attempts, 2)
        self.assertTrue(cancelling.closed)

    async def test_aclose_concorrente_compartilha_tentativa_e_retry(self):
        """Dois closes sobrepostos não podem fechar nem remover o mesmo cliente duas vezes."""
        client = BarrierFailingOnceClient()

        with TemporaryDirectory() as tmpdir:
            gateway = build_provider_gateway(
                Path(tmpdir),
                client_factory=lambda: client,
            )
            gateway.registry.create("ollama")
            start = asyncio.Event()

            async def close_after_barrier():
                await start.wait()
                await gateway.aclose()

            callers = [asyncio.create_task(close_after_barrier()) for _ in range(2)]
            start.set()
            await client.started.wait()
            await asyncio.sleep(0)
            attempts_during_overlap = client.close_attempts
            client.release.set()
            results = await asyncio.gather(*callers, return_exceptions=True)

            self.assertEqual(attempts_during_overlap, 1)
            self.assertTrue(all(isinstance(result, BaseExceptionGroup) for result in results))
            self.assertEqual(client.close_attempts, 1)
            await gateway.aclose()

        self.assertEqual(client.close_attempts, 2)
        self.assertTrue(client.closed)

    def test_aclose_em_loops_de_threads_compartilha_tentativa(self):
        """Waiters de loops distintos não podem aguardar Task estrangeira nem duplicar close."""
        client = ThreadBarrierClient()
        results: dict[str, object] = {}
        second_joined = threading.Event()

        with TemporaryDirectory() as tmpdir:
            gateway = build_provider_gateway(
                Path(tmpdir),
                client_factory=lambda: client,
            )
            gateway.registry.create("ollama")

            def run_close(name: str, joined: threading.Event | None = None) -> None:
                async def close() -> None:
                    task = asyncio.create_task(gateway.aclose())
                    await asyncio.sleep(0)
                    if joined is not None:
                        joined.set()
                    await task

                try:
                    results[name] = asyncio.run(close())
                except BaseException as exc:  # noqa: BLE001 - registra outcome cross-loop
                    results[name] = exc

            first = threading.Thread(target=run_close, args=("first",), daemon=True)
            first.start()
            self.assertTrue(client.started.wait(timeout=2))
            second = threading.Thread(
                target=run_close,
                args=("second", second_joined),
                daemon=True,
            )
            second.start()
            self.assertTrue(second_joined.wait(timeout=2))
            client.release.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(
            all(isinstance(results[name], BaseExceptionGroup) for name in ("first", "second"))
        )
        self.assertEqual(client.close_attempts, 1)
        asyncio.run(gateway.aclose())
        self.assertEqual(client.close_attempts, 2)
        self.assertTrue(client.closed)

    def test_falha_ao_agendar_cleanup_publica_resultado_e_permite_retry(self):
        """Falha de launch deve chegar ao caller e permitir nova tentativa global."""
        from kairos_providers import _async_cleanup

        client = TrackingClient()
        with TemporaryDirectory() as tmpdir:
            gateway = build_provider_gateway(
                Path(tmpdir),
                client_factory=lambda: client,
            )
            gateway.registry.create("ollama")
            with (
                patch.object(
                    _async_cleanup,
                    "_launch_cleanup_task",
                    side_effect=RuntimeError("falha ao agendar provider close"),
                ),
                self.assertRaisesRegex(RuntimeError, "falha ao agendar"),
            ):
                asyncio.run(gateway.aclose())
            with self.assertRaisesRegex(RuntimeError, "encerrado"):
                gateway.registry.create("openai", api_key="")
            asyncio.run(asyncio.wait_for(gateway.aclose(), timeout=1))

        self.assertTrue(client.closed)

    def test_cleanup_nao_usa_task_factory_eager_do_caller(self):
        """Task factory ambiente não participa do launch do cleanup reservado."""
        client = TrackingClient()

        async def close_with_hostile_factory(gateway) -> None:
            loop = asyncio.get_running_loop()

            def hostile_factory(_loop, coroutine, **_kwargs):
                coroutine.close()
                raise AssertionError("task factory do caller executou cleanup")

            loop.set_task_factory(hostile_factory)
            try:
                await gateway.aclose()
            finally:
                loop.set_task_factory(None)

        with TemporaryDirectory() as tmpdir:
            gateway = build_provider_gateway(
                Path(tmpdir),
                client_factory=lambda: client,
            )
            gateway.registry.create("ollama")
            asyncio.run(close_with_hostile_factory(gateway))

        self.assertTrue(client.closed)
