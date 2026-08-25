"""Resolução pura da seleção efetiva de provider e modelo."""

from __future__ import annotations

from dataclasses import dataclass

from kairos_providers.catalog import ModelCatalog, UnknownModelError
from kairos_providers.contracts import (
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
)


class ModelSelectionUnavailableError(LookupError):
    """Indica que nenhuma seleção utilizável pôde ser resolvida."""


@dataclass(frozen=True)
class ModelSelectionContext:
    message: ProviderModelRef | None = None
    conversation: ProviderModelRef | None = None
    activity: ProviderModelRef | None = None
    profile: ProviderModelRef | None = None
    global_default: ProviderModelRef | None = None


class ModelSelectionResolver:
    _ORDER = (
        ("message", SelectionReason.MESSAGE_OVERRIDE),
        ("conversation", SelectionReason.CONVERSATION_OVERRIDE),
        ("activity", SelectionReason.ACTIVITY_RULE),
        ("profile", SelectionReason.PROFILE_DEFAULT),
        ("global_default", SelectionReason.GLOBAL_DEFAULT),
    )

    def __init__(self, catalog: ModelCatalog, *, include_preview: bool = False) -> None:
        self._catalog = catalog
        self._include_preview = include_preview

    def resolve(self, context: ModelSelectionContext) -> ResolvedModelSelection:
        for field, reason in self._ORDER:
            ref = getattr(context, field)
            if ref is None:
                continue
            try:
                model = self._catalog.find(ref)
            except UnknownModelError as exc:
                raise ModelSelectionUnavailableError(
                    f"seleção indisponível: {ref.provider}/{ref.model}"
                ) from exc
            if not model.is_selectable(include_preview=self._include_preview):
                raise ModelSelectionUnavailableError(
                    f"seleção indisponível: {ref.provider}/{ref.model}"
                )
            return ResolvedModelSelection(ref=ref, reason=reason)
        raise ModelSelectionUnavailableError("nenhuma seleção configurada")
