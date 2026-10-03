"""Descoberta e registro dos plugins instalados.

`_reversa_sdd/plugins/` RF-01/RF-02 (Tarefa 10). Este módulo é a peça que
faltava: o manifesto (`manifest.py`) e o registro de hooks (`hooks.py`) existiam
testados, sem nenhum consumidor no build — um plugin podia declarar
`provides_hooks` e nunca ser chamado, que é capacidade declarada sem efeito.

**Convenção de entrada.** O plugin é um diretório com `plugin.yaml` e, se quiser
disparar alguma coisa, `plugin.py` ao lado. Os callbacks são funções de módulo
com o **mesmo nome do hook declarado** no manifesto:

    # <home>/plugins/meu-plugin/plugin.yaml
    name: meu-plugin
    version: "1.0.0"
    provides_hooks: [post_tool_call]

    # <home>/plugins/meu-plugin/plugin.py
    def post_tool_call(*, tool, is_error, **_):
        ...

O manifesto é a **única** fonte: callback cujo nome não esteja em
`provides_hooks` não é registrado. O caminho inverso — um plugin registrar
hook que não declarou — é o `hooks.json` que a D-CLI.9 apagou por ser efeito
fictício.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import re
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kairos_plugins.hooks import HookRegistry
from kairos_plugins.manifest import (
    Manifest,
    ManifestError,
    PluginState,
    load_manifest,
    resolve_state,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_NAME",
    "MODULE_NAME",
    "DiscoveredPlugin",
    "LoadedPlugins",
    "load_plugins",
    "plugins_dir",
]

#: Nome do manifesto dentro do diretório do plugin.
MANIFEST_NAME = "plugin.yaml"

#: Módulo de entrada do plugin, ao lado do manifesto.
MODULE_NAME = "plugin.py"

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_]+")


def plugins_dir(home: Path) -> Path:
    """`<kairos home>/plugins` — o diretório que o usuário controla."""
    return Path(home).expanduser() / "plugins"


@dataclass(frozen=True)
class DiscoveredPlugin:
    """O que o scanner descobriu de um diretório de plugin.

    `state` e `error` são o relatório honesto: um plugin que não carregou
    continua na lista, dizendo por quê. Sumir da lista seria indistinguível de
    "não existe".
    """

    name: str
    version: str = ""
    kind: str = ""
    state: PluginState = PluginState.DISABLED
    path: Path | None = None
    declared_hooks: tuple[str, ...] = ()
    registered_hooks: tuple[str, ...] = ()
    missing_env: tuple[str, ...] = ()
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.state is PluginState.ACTIVE


@dataclass(frozen=True)
class LoadedPlugins:
    """O registro construido e o inventário que o produziu.

    Os dois andam juntos de propósito: `hooks list` precisa saber **quais
    plugins** registraram callback em **qual** hook, e essa resposta só existe
    com o registro ao lado do inventário.
    """

    registry: HookRegistry
    plugins: tuple[DiscoveredPlugin, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.plugins


def _candidate_dirs(home: Path, *, bundled: Path | None) -> list[Path]:
    """Diretórios candidatos, na ordem de precedência da spec.

    O bundled vem primeiro: um plugin nativo não pode ser sombreado por uma
    cópia do usuário com o mesmo nome.
    """
    raizes: list[Path] = []
    if bundled is not None:
        raizes.append(Path(bundled).expanduser())
    raizes.append(plugins_dir(home))
    candidatos: list[Path] = []
    for raiz in raizes:
        if not raiz.is_dir():
            continue
        try:
            entradas = sorted(raiz.iterdir())
        except OSError as exc:
            logger.warning("não foi possível listar plugins em %s: %s", raiz, exc)
            continue
        candidatos.extend(p for p in entradas if p.is_dir())
    return candidatos


def _import_entry_module(path: Path, nome: str) -> Any | None:
    """Importa `plugin.py` por caminho, com nome de módulo isolado.

    O nome leva o slug **e** um resumo do caminho absoluto. Só o slug colidiria:
    o `sys.modules` devolveria o módulo do outro homônimo e o `plugin.py` da
    cópia jamais executaria — um plugin "carregado" sem nenhum callback próprio,
    que é pior que não carregar. Com o caminho no nome, dois homônimos coexistem
    e reimportar o **mesmo** caminho reaproveita o corpo já executado.
    """
    resumo = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    module_name = f"kairos_plugin_{_SLUG_RE.sub('_', nome)}_{resumo}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"não foi possível preparar o módulo {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _register_callbacks(registry: HookRegistry, manifest: Manifest, module: Any) -> tuple[str, ...]:
    """Registra o que o manifesto declarou **e** o módulo implementa.

    Só o declarado entra: `provides_hooks` é o contrato auditável (RF-01), e o
    callback ausente não é erro — o plugin simplesmente não escuta esse hook.
    """
    if manifest.requires_raw_error:
        registry.allow_raw_error(manifest.name)

    callbacks: dict[str, Any] = {}
    for hook in manifest.provides_hooks:
        callback = getattr(module, hook, None)
        if callable(callback):
            callbacks[hook] = callback

    for hook, callback in callbacks.items():
        registry.register(hook, callback, plugin=manifest.name)

    return tuple(callbacks)


def _scan_dir(
    path: Path,
    registry: HookRegistry,
    *,
    disabled: Collection[str],
    env: dict[str, str] | None,
) -> DiscoveredPlugin:
    manifesto = path / MANIFEST_NAME
    nome = path.name
    try:
        manifest = load_manifest(manifesto, name_hint=nome)
    except ManifestError as exc:
        logger.warning("plugin em %s ignorado: %s", path, exc)
        return DiscoveredPlugin(name=nome, state=PluginState.DISABLED, path=path, error=str(exc))
    nome = manifest.name

    estado, faltando = resolve_state(manifest, env)
    if manifest.name in disabled and estado is PluginState.ACTIVE:
        estado = PluginState.DISABLED

    module_path = path / MODULE_NAME
    if estado is not PluginState.ACTIVE:
        return DiscoveredPlugin(
            name=nome,
            version=manifest.version,
            kind=manifest.kind,
            state=estado,
            path=path,
            declared_hooks=manifest.provides_hooks,
            missing_env=faltando,
        )

    if not module_path.is_file():
        # Ativo e sem módulo: nada dispara. Declarado assim no relatório em vez
        # de fingir que o plugin escuta algo.
        logger.warning(
            "plugin %r declara %s hook(s) mas não tem %s; nenhum callback será registrado",
            nome,
            len(manifest.provides_hooks),
            MODULE_NAME,
        )
        return _sem_callbacks(
            manifest,
            estado,
            path,
            faltando,
            f"sem {MODULE_NAME}: nenhum callback registrado",
        )

    try:
        module = _import_entry_module(module_path, nome)
    except Exception as exc:  # noqa: BLE001 - um plugin ruim não derruba o host
        logger.warning("plugin %r não carregou: %s", nome, exc)
        return _sem_callbacks(manifest, estado, path, faltando, f"{type(exc).__name__}: {exc}")

    hooks: tuple[str, ...] = ()
    if module is not None:
        try:
            hooks = _register_callbacks(registry, manifest, module)
        except Exception as exc:  # noqa: BLE001 - registrar não pode derrubar o host
            logger.warning("plugin %r não registrou callbacks: %s", nome, exc)
            return _sem_callbacks(manifest, estado, path, faltando, f"{type(exc).__name__}: {exc}")

    return DiscoveredPlugin(
        name=nome,
        version=manifest.version,
        kind=manifest.kind,
        state=estado,
        path=path,
        declared_hooks=manifest.provides_hooks,
        registered_hooks=hooks,
        missing_env=faltando,
    )


def _sem_callbacks(
    manifest: Manifest,
    estado: PluginState,
    path: Path,
    faltando: tuple[str, ...],
    erro: str,
) -> DiscoveredPlugin:
    """Plugin ativo whose callbacks could not be registered, with the reason."""
    return DiscoveredPlugin(
        name=manifest.name,
        version=manifest.version,
        kind=manifest.kind,
        state=estado,
        path=path,
        declared_hooks=manifest.provides_hooks,
        registered_hooks=(),
        missing_env=faltando,
        error=erro,
    )


def disabled_from_config(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Nomes em `plugins.disabled`. **Parse fail-closed.**

    `disabled` malformado levanta `ValueError` em vez de virar lista vazia: quem
    escreveu `"disabled: sim"` acreditando que desligou tudo não pode receber um
    "nenhum plugin desabilitado" calado. A composição e a CLI leem por aqui para
    que o interruptor tenha uma regra só.
    """
    secao = config.get("plugins")
    if secao is None:
        return ()
    if not isinstance(secao, Mapping):
        raise ValueError("plugins deve ser um mapeamento em config.yaml")
    bruto = secao.get("disabled")
    if bruto is None:
        return ()
    if isinstance(bruto, str) or not isinstance(bruto, Sequence):
        raise ValueError("plugins.disabled deve ser uma lista de nomes em config.yaml")
    if not all(isinstance(nome, str) for nome in bruto):
        raise ValueError("plugins.disabled deve ser uma lista de nomes em config.yaml")
    return tuple(bruto)


def load_plugins(
    home: Path,
    *,
    disabled: Collection[str] = (),
    bundled: Path | None = None,
    env: dict[str, str] | None = None,
) -> LoadedPlugins:
    """Lê os plugins instalados e constrói o registro de hooks.

    Não levanta por plugin ruim: manifesto inválido, módulo quebrado ou hook sem
    implementação viram `error` no inventário. Só levanta se o `home` for
    ilegível de um jeito que impeça a própria leitura — e, mesmo assim, o erro
    nomeia o caminho.
    """
    registro = HookRegistry()
    vistos: dict[str, Path] = {}
    descobertos: list[DiscoveredPlugin] = []
    for path in _candidate_dirs(home, bundled=bundled):
        if path.name in vistos:
            logger.warning(
                "plugin %r em %s ignorado: %s tem precedência",
                path.name,
                path,
                vistos[path.name],
            )
            continue
        vistos[path.name] = path
        descobertos.append(_scan_dir(path, registro, disabled=disabled, env=env))
    return LoadedPlugins(registry=registro, plugins=tuple(descobertos))
