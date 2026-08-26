"""Carrega o contexto de seleção sem resolver fallback nem tocar credenciais."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairos_integration.interaction_contract import InteractionEnvelope
from kairos_providers import ModelSelectionContext, ProviderModelRef
from kairos_state.repositories import SessionRepository

__all__ = ["SelectionContextLoader", "parse_ref"]


def parse_ref(value: Any) -> ProviderModelRef | None:
    if value is None:
        return None
    if isinstance(value, ProviderModelRef):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        provider, _, model = raw.partition("/")
        if not provider or not model:
            return None
        return ProviderModelRef(provider=provider, model=model)
    if not isinstance(value, Mapping):
        return None

    provider = value.get("provider")
    model = value.get("model", value.get("name"))
    if not isinstance(provider, str) or not provider.strip():
        return None
    if not isinstance(model, str) or not model.strip():
        return None
    return ProviderModelRef(provider=provider.strip(), model=model.strip())


class SelectionContextLoader:
    def __init__(
        self,
        sessions: SessionRepository,
        *,
        profile_configs: Mapping[str, Any] | None = None,
        global_config: Mapping[str, Any] | None = None,
    ) -> None:
        self._sessions = sessions
        self._profile_configs = profile_configs or {}
        self._global_config = global_config or {}

    def load(self, envelope: InteractionEnvelope) -> ModelSelectionContext:
        profile_config = self._profile_configs.get(envelope.profile or "")
        if not isinstance(profile_config, Mapping):
            profile_config = {}
        persisted = self._sessions.selection(envelope.conversation_id)
        activity_models = profile_config.get("auxiliary_models")
        activity_ref = None
        if isinstance(activity_models, Mapping) and envelope.activity is not None:
            activity_ref = parse_ref(activity_models.get(envelope.activity))

        return ModelSelectionContext(
            message=parse_ref(envelope.override),
            conversation=parse_ref(persisted.ref if persisted is not None else None),
            activity=activity_ref,
            profile=parse_ref(_config_ref(profile_config)),
            global_default=parse_ref(_config_ref(self._global_config)),
        )


def _config_ref(config: Mapping[str, Any]) -> Any:
    model = config.get("model")
    if isinstance(model, str) and isinstance(config.get("provider"), str):
        return {
            "provider": config["provider"],
            "model": model,
        }
    return model
