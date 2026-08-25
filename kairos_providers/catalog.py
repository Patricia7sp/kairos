"""Catálogo híbrido de modelos com precedência explícita de fontes."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ProviderModelRef,
)

_ORIGIN_PRIORITY = {
    CatalogOrigin.CACHE: 0,
    CatalogOrigin.CURATED: 1,
    CatalogOrigin.DYNAMIC: 2,
}


def _complete_capabilities(
    primary: ModelCapabilities, fallback: ModelCapabilities
) -> ModelCapabilities:
    return ModelCapabilities(
        chat=primary.chat if primary.chat is not None else fallback.chat,
        tools=primary.tools if primary.tools is not None else fallback.tools,
        vision=primary.vision if primary.vision is not None else fallback.vision,
        streaming=(primary.streaming if primary.streaming is not None else fallback.streaming),
        context_length=(
            primary.context_length
            if primary.context_length is not None
            else fallback.context_length
        ),
        max_output_tokens=(
            primary.max_output_tokens
            if primary.max_output_tokens is not None
            else fallback.max_output_tokens
        ),
    )


@dataclass(frozen=True)
class CatalogSnapshot:
    models: tuple[CatalogModel, ...]
    fetched_at: float
    expires_at: float

    def is_valid(self, now: float) -> bool:
        return now <= self.expires_at


class UnknownModelError(LookupError):
    """Indica que a referência não existe no catálogo disponível."""


class ModelCatalog:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._models: dict[ProviderModelRef, CatalogModel] = {}
        self._priorities: dict[ProviderModelRef, int] = {}
        self._snapshot: CatalogSnapshot | None = None

    def merge(self, models: Iterable[CatalogModel], *, origin: CatalogOrigin) -> None:
        priority = _ORIGIN_PRIORITY[origin]
        for incoming in models:
            current = self._models.get(incoming.ref)
            origins = incoming.origins | {origin}
            if current is not None:
                origins |= current.origins
            if current is None or priority >= self._priorities[incoming.ref]:
                capabilities = (
                    incoming.capabilities
                    if current is None
                    else _complete_capabilities(incoming.capabilities, current.capabilities)
                )
                self._models[incoming.ref] = replace(
                    incoming,
                    capabilities=capabilities,
                    origins=frozenset(origins),
                )
                self._priorities[incoming.ref] = priority
            else:
                self._models[incoming.ref] = replace(
                    current,
                    capabilities=_complete_capabilities(
                        current.capabilities, incoming.capabilities
                    ),
                    origins=frozenset(origins),
                )

    def load_snapshot(self, snapshot: CatalogSnapshot) -> None:
        now = self._clock()
        if not snapshot.is_valid(now):
            return
        if (
            self._snapshot is not None
            and self._snapshot.is_valid(now)
            and snapshot.fetched_at < self._snapshot.fetched_at
        ):
            return
        self._snapshot = snapshot
        self.merge(snapshot.models, origin=CatalogOrigin.CACHE)

    def find(self, ref: ProviderModelRef) -> CatalogModel:
        try:
            return self._models[ref]
        except KeyError as exc:
            raise UnknownModelError(f"{ref.provider}/{ref.model}") from exc

    def list_models(
        self, provider: str | None = None, *, include_preview: bool = False
    ) -> list[CatalogModel]:
        models = (
            model
            for model in self._models.values()
            if (provider is None or model.ref.provider == provider)
            and model.is_selectable(include_preview=include_preview)
        )
        return sorted(
            models,
            key=lambda model: (
                model.display_name.casefold(),
                model.ref.provider,
                model.ref.model,
            ),
        )
