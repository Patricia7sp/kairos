"""Terminal report of persisted token usage and known billing amounts."""

import json
from pathlib import Path

from kairos_state.usage_summary import read_usage_summary


def run_insights(home: Path, args) -> int:
    report = read_usage_summary(home)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    elif report["availability"] != "available":
        print("As métricas de uso persistido estão indisponíveis.")
    else:
        totals = report["totals"]
        print("Uso acumulado persistido — todo o histórico disponível")
        print(f"Chamadas: {totals['requests']}")
        print(
            f"Tokens: {totals['tokens']} "
            f"(entrada: {totals['input_tokens']}; saída: {totals['output_tokens']})"
        )
        print(
            f"Cache: leitura {totals['cache_read_tokens']}; gravação {totals['cache_write_tokens']}"
        )
        print(f"Tokens de raciocínio: {totals['reasoning_tokens']}")
        for label, key in (
            ("Custos reais conhecidos", "actual_cost_usd"),
            ("Custos estimados conhecidos", "estimated_cost_usd"),
        ):
            value = totals[key]
            amount = "desconhecidos" if value is None else f"US$ {value:.6f}"
            print(f"{label}: {amount}")
        if not totals["cost_totals_complete"]:
            print("Custos incompletos: valores conhecidos não representam o custo total.")
        print("Distribuição por período e série diária indisponíveis.")
    return 0 if report["availability"] == "available" else 1
