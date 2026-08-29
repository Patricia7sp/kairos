"""Fallback curado de modelos agentic conhecidos pelo Kairos."""

from __future__ import annotations

from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelKind,
    ModelStability,
    ProviderModelRef,
)


def _model(
    provider: str,
    model: str,
    display_name: str,
    *,
    kind: ModelKind = ModelKind.MODEL,
    stability: ModelStability = ModelStability.STABLE,
) -> CatalogModel:
    return CatalogModel(
        ref=ProviderModelRef(provider, model, kind),
        display_name=display_name,
        capabilities=ModelCapabilities(chat=True, tools=True),
        stability=stability,
        origins=frozenset({CatalogOrigin.CURATED}),
    )


_CURATED_MODELS = (
    # Mantidos enquanto ``ProviderManager`` ainda atende superfícies legadas.
    _model("openai", "gpt-4o", "GPT-4o"),
    _model("openai", "gpt-5.6-sol", "GPT 5.6 Sol"),
    _model("openai", "gpt-5.6-terra", "GPT 5.6 Terra"),
    _model("openai", "gpt-5.6-luna", "GPT 5.6 Luna"),
    _model("anthropic", "claude-fable-5", "Claude Fable 5"),
    _model("anthropic", "claude-3-7-sonnet-20250219", "Claude 3.7 Sonnet"),
    _model("anthropic", "claude-opus-5", "Claude Opus 5"),
    _model("anthropic", "claude-sonnet-5", "Claude Sonnet 5"),
    _model(
        "anthropic",
        "claude-haiku-4-5-20251001",
        "Claude Haiku 4.5",
    ),
    _model("gemini", "gemini-3.7-flash", "Gemini 3.7 Flash"),
    _model("gemini", "gemini-2.0-flash", "Gemini 2.0 Flash"),
    _model("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro"),
    _model("deepseek", "deepseek-v4-flash", "DeepSeek V4 Flash"),
    _model("openrouter", "openrouter/free", "OpenRouter Free"),
    _model(
        "gemini",
        "antigravity",
        "Antigravity",
        kind=ModelKind.REMOTE_AGENT,
        stability=ModelStability.PREVIEW,
    ),
)


def curated_models() -> tuple[CatalogModel, ...]:
    return _CURATED_MODELS
