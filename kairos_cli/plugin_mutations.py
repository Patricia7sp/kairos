"""Apresentação CLI dos resultados reais de retirada de plugins."""

import argparse
import json
import sys
from pathlib import Path

from kairos_plugins.removal import PluginRemovalError, remove_plugin


def run_plugin_removal(home: Path, args: argparse.Namespace) -> int:
    try:
        result = remove_plugin(home, args.name, confirmed=getattr(args, "yes", False))
    except PluginRemovalError as exc:
        code = {"confirmation": 77, "input": 2, "unavailable": 69}.get(exc.kind, 1)
        failure = {"erro": str(exc), "retirado": exc.retired}
        if exc.operation_id is not None:
            failure["residuo_id"] = exc.operation_id
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return code
    value = {
        "nome": result.name,
        "removido": result.removed,
        "requer_reinicio": result.requires_restart,
    }
    if getattr(args, "json", False):
        print(json.dumps(value, ensure_ascii=False))
    else:
        print(f"Plugin {result.name} removido. Reinicie processos com callbacks ativos.")
    return 0
