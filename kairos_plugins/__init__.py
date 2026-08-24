"""Plugins: manifesto, hooks e armazenamento isolado."""

from kairos_plugins.hooks import (
    HOOK_FAMILIES,
    VALID_HOOKS,
    HookRegistry,
    HookResult,
    UnknownHook,
    redact_provider_error,
)
from kairos_plugins.manifest import (
    VALID_PLUGIN_KINDS,
    Manifest,
    ManifestError,
    PluginKind,
    PluginState,
    load_manifest,
    resolve_state,
)
from kairos_plugins.storage import plugin_data_dir, plugin_db, plugin_root

__all__ = [
    "HOOK_FAMILIES",
    "VALID_HOOKS",
    "VALID_PLUGIN_KINDS",
    "HookRegistry",
    "HookResult",
    "Manifest",
    "ManifestError",
    "PluginKind",
    "PluginState",
    "UnknownHook",
    "load_manifest",
    "plugin_data_dir",
    "plugin_db",
    "plugin_root",
    "redact_provider_error",
    "resolve_state",
]
