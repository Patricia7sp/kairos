"""Registry interno de descritores e factories de providers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kairos_providers.contracts import ProviderDescriptor


class UnknownProviderError(LookupError):
    """Indica que não existe factory registrada para o provider solicitado."""


@dataclass(frozen=True)
class RegisteredProvider:
    descriptor: ProviderDescriptor
    factory: Callable[..., Any] = field(repr=False, compare=False)


class ProviderAdapterRegistry:
    """Associa metadados públicos a factories internas sem guardar segredos."""

    def __init__(self) -> None:
        self._entries: dict[str, RegisteredProvider] = {}

    def register(
        self, descriptor: ProviderDescriptor, factory: Callable[..., Any]
    ) -> None:
        self._entries[descriptor.id] = RegisteredProvider(descriptor, factory)

    def describe(self, provider: str) -> ProviderDescriptor:
        try:
            return self._entries[provider].descriptor
        except KeyError as exc:
            raise UnknownProviderError(provider) from exc

    def create(self, provider: str, **kwargs: Any) -> Any:
        self.describe(provider)
        return self._entries[provider].factory(**kwargs)

    def list_descriptors(self) -> list[ProviderDescriptor]:
        return [self._entries[key].descriptor for key in sorted(self._entries)]
