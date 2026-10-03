"""Plugins: manifesto, descoberta, hooks e armazenamento isolado."""

from kairos_plugins.emitter import EMITTED_HOOKS, ENQUEUED_HOOKS, HookEmitter
from kairos_plugins.hooks import (
    HOOK_FAMILIES,
    VALID_HOOKS,
    HookRegistry,
    HookResult,
    UnknownHook,
    redact_provider_error,
)
from kairos_plugins.loader import (
    DiscoveredPlugin,
    LoadedPlugins,
    disabled_from_config,
    load_plugins,
    plugins_dir,
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
from kairos_plugins.stream_dispatcher import StreamHookDispatcher

__all__ = [
    "EMITTED_HOOKS",
    "ENQUEUED_HOOKS",
    "HOOK_FAMILIES",
    "VALID_HOOKS",
    "VALID_PLUGIN_KINDS",
    "DiscoveredPlugin",
    "HookEmitter",
    "HookRegistry",
    "HookResult",
    "LoadedPlugins",
    "Manifest",
    "ManifestError",
    "PluginKind",
    "PluginState",
    "StreamHookDispatcher",
    "UnknownHook",
    "disabled_from_config",
    "load_manifest",
    "load_plugins",
    "plugin_data_dir",
    "plugin_db",
    "plugin_root",
    "plugins_dir",
    "redact_provider_error",
    "resolve_state",
]
