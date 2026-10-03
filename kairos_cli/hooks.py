"""Comando `kairos hooks` — hooks de plugin.

`hooks list` é **leitura de inventário**: mostra quais hooks existem no
sistema, quais têm emissor de verdade no runtime e quais plugins registram
callback em cada um. Os três números são deliberadamente separados — 37 hooks
declarados sem um único emissor é exatamente o tipo de capacidade declarada sem
efeito que este comando existia para esconder.

`hooks use` continua recusando com 69: não há `hooks.json` que ele governaria.
Os hooks vêm do registro dos plugins, e o interruptor de um plugin é
`plugins.disabled` no `config.yaml`.
"""

import json
import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_hooks(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "hooks_command", None) or getattr(args, "subcommand", None)

    if subcommand == "list":
        return _listar(home, as_json=bool(getattr(args, "json", False)))
    if subcommand == "use":
        print(
            "kairos: hooks use não implementado — não existe hooks.json para "
            "governar. Os hooks são registrados pelos plugins em <home>/plugins; "
            "para desligar um plugin, use plugins.disabled no config.yaml.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: hooks list")
    return ExitCode.USAGE


def _listar(home: Path, *, as_json: bool) -> int:
    from kairos_cli.config import load_config
    from kairos_plugins import (
        EMITTED_HOOKS,
        ENQUEUED_HOOKS,
        HOOK_FAMILIES,
        VALID_HOOKS,
        disabled_from_config,
        load_plugins,
    )

    try:
        disabled = disabled_from_config(load_config())
    except ValueError as exc:
        print(f"kairos: config.yaml inválido: {exc}", file=sys.stderr)
        return ExitCode.USAGE

    plugins = load_plugins(home, disabled=disabled)
    hooked = plugins.registry.hooks_with_callbacks()

    # D-PLUG.7: os dois modos de despacho contam separado. "emite" (awaited) e
    # "enfileira" (observador de stream) são contratos diferentes — fundir num
    # número só diria que a família stream está no caminho do token, que é
    # justamente o que ela não está.
    disparados = EMITTED_HOOKS | ENQUEUED_HOOKS
    linhas = {
        "hooks declarados": len(VALID_HOOKS),
        "hooks que o runtime emite (await)": len(EMITTED_HOOKS),
        "hooks que o runtime enfileira (stream)": len(ENQUEUED_HOOKS),
        "hooks sem emissor": len(VALID_HOOKS) - len(disparados),
        "hooks com callback registrado": len(hooked),
        "plugins descobertos": len(plugins.plugins),
    }
    if as_json:
        print(
            json.dumps(
                {
                    **linhas,
                    "emissores": sorted(EMITTED_HOOKS),
                    "enfileirados": sorted(ENQUEUED_HOOKS),
                    "sem_emissor": sorted(VALID_HOOKS - disparados),
                    "familias": sorted(HOOK_FAMILIES),
                    "callbacks": hooked,
                    "plugins": [
                        {
                            "nome": p.name,
                            "estado": str(p.state),
                            "declarados": list(p.declared_hooks),
                            "registrados": list(p.registered_hooks),
                            **({"erro": p.error} if p.error else {}),
                        }
                        for p in plugins.plugins
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return ExitCode.OK

    largura = max(len(k) for k in linhas)
    for chave, valor in linhas.items():
        print(f"{chave:<{largura}}  {valor}")
    if hooked:
        print("\ncallbacks registrados:")
        for hook, nomes in hooked.items():
            if hook in EMITTED_HOOKS:
                marca = "emite"
            elif hook in ENQUEUED_HOOKS:
                marca = "enfileira"
            else:
                marca = "SEM EMISSOR"
            print(f"  {hook}  [{marca}]  <- {', '.join(nomes)}")
    else:
        print("\nnenhum callback registrado: nenhum plugin ativo em plugins/")
    return ExitCode.OK
