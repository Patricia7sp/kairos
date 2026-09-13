"""Carrega o contexto de seleção sem resolver fallback nem tocar credenciais."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairos_integration.interaction_contract import InteractionEnvelope
from kairos_providers import ModelSelectionContext, ProviderModelRef
from kairos_state.repositories import SessionRepository

__all__ = ["SelectionContextLoader", "parse_config_ref", "parse_ref"]


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
        persisted = self._sessions.selection(envelope.conversation_id)
        profile_name = envelope.profile or (persisted.profile if persisted else None)
        profile_config = self._profile_configs.get(profile_name or "")
        if not isinstance(profile_config, Mapping):
            profile_config = {}

        return ModelSelectionContext(
            message=parse_ref(envelope.override),
            conversation=parse_ref(persisted.ref if persisted is not None else None),
            activity=_activity_ref(profile_config, envelope.activity),
            profile=parse_config_ref(profile_config),
            global_default=parse_config_ref(self._global_config),
            message_parameters=envelope.parameters or None,
            conversation_parameters=(
                persisted.parameters if persisted is not None and persisted.parameters else None
            ),
            activity_parameters=_activity_parameters(profile_config, envelope.activity),
            profile_parameters=_config_parameters(profile_config),
            global_parameters=_config_parameters(self._global_config),
        )


def parse_config_ref(config: Mapping[str, Any]) -> ProviderModelRef | None:
    """Parse a configured default without loading conversations or credentials."""
    model = config.get("model")
    if isinstance(model, str) and isinstance(config.get("provider"), str):
        return parse_ref(
            {
                "provider": config["provider"],
                "model": model,
            }
        )
    return parse_ref(model)


def _activity_ref(config: Mapping[str, Any], activity: str | None) -> ProviderModelRef | None:
    if activity is None:
        return None
    activity_models = config.get("auxiliary_models")
    if not isinstance(activity_models, Mapping):
        return None
    model = activity_models.get(activity)
    if isinstance(model, str) and "/" not in model and isinstance(config.get("provider"), str):
        model = {
            "provider": config["provider"],
            "model": model,
        }
    return parse_ref(model)


def _config_parameters(config: Mapping[str, Any]) -> dict[str, Any] | None:
    parameters = config.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    return dict(parameters)


def _activity_parameters(config: Mapping[str, Any], activity: str | None) -> dict[str, Any] | None:
    if activity is None:
        return None
    activity_models = config.get("auxiliary_models")
    if not isinstance(activity_models, Mapping):
        return None
    activity_config = activity_models.get(activity)
    if not isinstance(activity_config, Mapping):
        return None
    return _config_parameters(activity_config)
