"""Local diagnostic command; does not repair state or test remote generation."""

import json
from pathlib import Path

from kairos_observability.diagnostics import read_diagnostics


async def run_debug(home: Path, args) -> int:
    report = await read_diagnostics(home)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    else:
        labels = {
            "configuration": "Configuração",
            "provider": "Provedor",
            "model": "Modelo",
            "database": "Banco",
            "events": "Registros",
            "runtime": "Runtime",
        }
        states = {
            "available": "disponível localmente",
            "unavailable": "indisponível",
            "missing": "ausente",
            "unknown": "desconhecido",
            "not_configured": "não configurado",
            "not_in_catalog": "ausente do catálogo local",
            "not_selectable": "não selecionável",
            "outdated": "schema desatualizado",
            "newer": "schema mais novo que esta versão",
            "schema_incomplete": "schema incompleto",
            "ready": "pronto",
            "disabled": "desativado",
            "error": "falha na leitura",
        }
        outcome = "completo" if report["state"] == "complete" else "incompleto"
        print(f"Diagnóstico local {outcome}")
        for key, label in labels.items():
            print(f"{label}: {states[report[key]['state']]}")
        print("Autenticação e geração não testadas; credenciais não inspecionadas.")
        print("OpenRouter: data_collection=deny no padrão da aplicação.")
        print("A política da conta e de cada conversa não foi inspecionada.")
        print("Integridade completa do banco não testada. Nenhum reparo foi executado.")
        print("Runtime ou registros ausentes podem não impedir o Chat.")
    return 0 if report["state"] == "complete" else 1
