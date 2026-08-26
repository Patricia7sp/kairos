"""Armazenamento durável, atômico e sem credenciais para snapshots de catálogo."""

from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from kairos_providers.catalog import CatalogSnapshot
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelKind,
    ModelPrice,
    ModelStability,
    ProviderModelRef,
)


class CatalogSnapshotStore:
    """Persiste somente os dados públicos de descoberta de modelos."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, provider: str) -> CatalogSnapshot | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            data = raw["snapshots"][provider]
            snapshot = _snapshot_from_data(data)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if any(model.ref.provider != provider for model in snapshot.models):
            return None
        return snapshot

    def save(self, provider: str, snapshot: CatalogSnapshot) -> None:
        _validate_snapshot_provider(provider, snapshot)
        document = self._read_document()
        document.setdefault("snapshots", {})[provider] = _snapshot_to_data(snapshot)
        self._write_atomically(document)

    def _read_document(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"snapshots": {}}
        if not isinstance(data, dict):
            return {"snapshots": {}}
        raw_snapshots = data.get("snapshots")
        if not isinstance(raw_snapshots, dict):
            return {"snapshots": {}}

        snapshots: dict[str, dict[str, Any]] = {}
        for provider, raw_snapshot in raw_snapshots.items():
            if not isinstance(provider, str) or not provider.strip():
                continue
            try:
                snapshot = _snapshot_from_data(raw_snapshot)
            except (KeyError, TypeError, ValueError):
                continue
            if any(model.ref.provider != provider for model in snapshot.models):
                continue
            snapshots[provider] = _snapshot_to_data(snapshot)
        return {"snapshots": snapshots}

    def _write_atomically(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
            self._path.chmod(0o600)
            _fsync_directory(self._path.parent)
        except OSError:
            try:
                temporary_path.unlink(missing_ok=True)
            finally:
                raise


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_snapshot_provider(provider: str, snapshot: CatalogSnapshot) -> None:
    if any(model.ref.provider != provider for model in snapshot.models):
        raise ValueError("snapshot contém modelo de outro provider")


def _snapshot_to_data(snapshot: CatalogSnapshot) -> dict[str, Any]:
    return {
        "expires_at": snapshot.expires_at,
        "fetched_at": snapshot.fetched_at,
        "models": [_model_to_data(model) for model in snapshot.models],
    }


def _model_to_data(model: CatalogModel) -> dict[str, Any]:
    return {
        "capabilities": {
            "chat": model.capabilities.chat,
            "context_length": model.capabilities.context_length,
            "max_output_tokens": model.capabilities.max_output_tokens,
            "streaming": model.capabilities.streaming,
            "tools": model.capabilities.tools,
            "vision": model.capabilities.vision,
        },
        "display_name": model.display_name,
        "origins": sorted(origin.value for origin in model.origins),
        "price": {
            "completion": _decimal_to_string(model.price.completion),
            "prompt": _decimal_to_string(model.price.prompt),
            "request": _decimal_to_string(model.price.request),
        },
        "ref": {
            "kind": model.ref.kind.value,
            "model": model.ref.model,
            "provider": model.ref.provider,
        },
        "stability": model.stability.value,
    }


def _decimal_to_string(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _snapshot_from_data(data: object) -> CatalogSnapshot:
    if not isinstance(data, dict):
        raise ValueError("snapshot inválido")
    models = data.get("models")
    if not isinstance(models, list):
        raise ValueError("models inválido")
    return CatalogSnapshot(
        models=tuple(_model_from_data(item) for item in models),
        fetched_at=float(data["fetched_at"]),
        expires_at=float(data["expires_at"]),
    )


def _model_from_data(data: object) -> CatalogModel:
    if not isinstance(data, dict):
        raise ValueError("modelo inválido")
    ref = data["ref"]
    capabilities = data["capabilities"]
    price = data["price"]
    if not isinstance(ref, dict) or not isinstance(capabilities, dict) or not isinstance(price, dict):
        raise ValueError("modelo inválido")
    origins = data.get("origins", [])
    if not isinstance(origins, list) or not all(isinstance(origin, str) for origin in origins):
        raise ValueError("origins inválido")
    return CatalogModel(
        ref=ProviderModelRef(
            provider=str(ref["provider"]),
            model=str(ref["model"]),
            kind=ModelKind(str(ref.get("kind", ModelKind.MODEL))),
        ),
        display_name=str(data["display_name"]),
        capabilities=ModelCapabilities(
            chat=capabilities.get("chat"),
            tools=capabilities.get("tools"),
            vision=capabilities.get("vision"),
            streaming=capabilities.get("streaming"),
            context_length=capabilities.get("context_length"),
            max_output_tokens=capabilities.get("max_output_tokens"),
        ),
        stability=ModelStability(str(data.get("stability", ModelStability.STABLE))),
        origins=frozenset(CatalogOrigin(origin) for origin in origins),
        price=ModelPrice(
            prompt=_decimal_from_data(price.get("prompt")),
            completion=_decimal_from_data(price.get("completion")),
            request=_decimal_from_data(price.get("request")),
        ),
    )


def _decimal_from_data(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("preço inválido")
    return Decimal(value)
