"""Plugins: manifesto, descoberta, hooks e armazenamento isolado."""

from importlib import import_module

_EXPORTS = {
    "EMITTED_HOOKS": "kairos_plugins.emitter",
    "ENQUEUED_HOOKS": "kairos_plugins.emitter",
    "HookEmitter": "kairos_plugins.emitter",
    "HOOK_FAMILIES": "kairos_plugins.hooks",
    "VALID_HOOKS": "kairos_plugins.hooks",
    "HookRegistry": "kairos_plugins.hooks",
    "HookResult": "kairos_plugins.hooks",
    "UnknownHook": "kairos_plugins.hooks",
    "redact_provider_error": "kairos_plugins.hooks",
    "DiscoveredPlugin": "kairos_plugins.loader",
    "LoadedPlugins": "kairos_plugins.loader",
    "disabled_from_config": "kairos_plugins.loader",
    "load_plugins": "kairos_plugins.loader",
    "plugins_dir": "kairos_plugins.loader",
    "VALID_PLUGIN_KINDS": "kairos_plugins.manifest",
    "Manifest": "kairos_plugins.manifest",
    "ManifestError": "kairos_plugins.manifest",
    "PluginKind": "kairos_plugins.manifest",
    "PluginState": "kairos_plugins.manifest",
    "load_manifest": "kairos_plugins.manifest",
    "resolve_state": "kairos_plugins.manifest",
    "plugin_data_dir": "kairos_plugins.storage",
    "plugin_db": "kairos_plugins.storage",
    "plugin_root": "kairos_plugins.storage",
    "StreamHookDispatcher": "kairos_plugins.stream_dispatcher",
}


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


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
