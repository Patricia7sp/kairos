"""Gateway sintético instalado apenas no processo temporário explicitamente marcado."""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from kairos_providers import (
    CanonicalToolCall,
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ModelPrice,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    TokenUsage,
)
from kairos_providers.gateway import ProviderBillingMetadata

MODEL = ProviderModelRef("openrouter", "e03-synthetic")
_PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "KAIROS_WEB_TOKEN",
)


def require_temporary_profile(home: Path) -> dict:
    if os.environ.get("KAIROS_E03_TEMPORARY") != "1":
        raise ValueError("perfil temporário E0.3 não habilitado")
    home = Path(home).absolute()
    if home != home.resolve() or home == Path("/opt/data"):
        raise ValueError("home temporário inválido")
    info = home.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("home temporário não é privado")
    if any(os.environ.get(key) for key in _PROVIDER_KEYS):
        raise ValueError("credencial herdada recusada na temporária")
    marker = home / ".e03-temporary"
    if marker.is_symlink():
        raise ValueError("identidade temporária inválida")
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("identidade temporária ausente ou inválida") from None
    if not isinstance(record, dict):
        raise ValueError("identidade temporária inválida")
    deadline = record.get("expires_at")
    if (
        set(record) != {"profile", "run_id", "expires_at"}
        or record["profile"] != "e03-synthetic"
        or not isinstance(record["run_id"], str)
        or re.fullmatch(r"[a-f0-9]{32}", record["run_id"]) is None
        or type(deadline) not in (int, float)
        or not math.isfinite(deadline)
        or not time.time() < deadline <= time.time() + 3600
    ):
        raise ValueError("identidade temporária inválida ou expirada")
    for name in ("auth.json", "credentials.json", "profiles"):
        if (home / name).exists():
            raise ValueError("credencial ou perfil de provider recusado")
    config = home / "config.yaml"
    if config.is_symlink():
        raise ValueError("configuração temporária inválida")
    if config.exists() and config.read_text(encoding="utf-8").strip() not in ("", "{}"):
        raise ValueError("configuração de provider recusada")
    return record


class SyntheticGateway:
    def __init__(self):
        self.catalog = ModelCatalog()
        self.catalog.merge(
            [
                CatalogModel(
                    ref=MODEL,
                    display_name="E0.3 sintético",
                    capabilities=ModelCapabilities(chat=True, tools=True),
                )
            ],
            origin=CatalogOrigin.CURATED,
        )
        self.closed = False

    def prepare(self, ref):
        if self.closed or ref != MODEL:
            raise ProviderError(ProviderErrorKind.MODEL, retryable=False)
        return SimpleNamespace(
            credential_id="e03-synthetic-no-provider-credential",
            price=ModelPrice(prompt=Decimal(0), completion=Decimal(0), request=Decimal(0)),
            cost_source="synthetic",
            create_adapter=lambda: self,
            billing=ProviderBillingMetadata(MODEL.provider, "https://e03.invalid", "api_key"),
        )

    async def stream(self, request):
        if self.closed or request.model != MODEL:
            raise ProviderError(ProviderErrorKind.MODEL, retryable=False)
        users = [message for message in request.messages if message.role == "user"]
        if not users or not any(
            part.kind == "text"
            and isinstance(part.value, str)
            and part.value.startswith("e03-synthetic:")
            for part in users[-1].content
        ):
            raise ProviderError(ProviderErrorKind.POLICY, retryable=False)
        tool_round = bool(request.tools) and request.messages[-1].role != "tool"
        if tool_round:
            yield ProviderEvent(
                kind="tool_call",
                tool_call=CanonicalToolCall(
                    id="e03-" + uuid.uuid4().hex,
                    name="bash",
                    arguments='{"command":":"}',
                ),
            )
        else:
            yield ProviderEvent(kind="text_delta", text="e03-synthetic: término do turno")
        yield ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=0, output_tokens=0))
        yield ProviderEvent(kind="finish", finish_reason="tool_calls" if tool_round else "stop")

    async def aclose(self):
        self.closed = True


@contextmanager
def install_gateway_seam(home: Path):
    require_temporary_profile(home)
    modules = [
        importlib.import_module(name)
        for name in (
            "kairos_providers.composition",
            "kairos_integration.composition",
            "kairos_web.server",
        )
    ]
    original = [(module, module.build_provider_gateway) for module in modules]

    def factory(selected_home):
        if Path(selected_home).absolute() != Path(home).absolute():
            raise ValueError("home fora da temporária")
        require_temporary_profile(home)
        return SyntheticGateway()

    try:
        for module in modules:
            module.build_provider_gateway = factory
        yield
    finally:
        for module, previous in original:
            module.build_provider_gateway = previous
