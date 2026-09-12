"""Terminal reader for the bounded operational event journal."""

import json
from pathlib import Path

from kairos_observability.service_events import read_service_events


def run_logs(home: Path, args) -> int:
    result = read_service_events(
        home,
        limit=args.limit,
        service=args.service,
        level=args.level,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["state"] == "ready":
        for event in result["events"]:
            counters = " ".join(f"{key}={value}" for key, value in event["counters"].items())
            print(
                f"{event['timestamp']} {event['level']} {event['service']}: {event['message']} {counters}".rstrip()
            )
        if not result["events"]:
            print("Nenhum evento corresponde aos filtros.")
    elif result["state"] == "unavailable":
        print("O diário operacional ainda não está disponível.")
    else:
        print("Não foi possível ler o diário operacional com segurança.")
    return 0 if result["state"] == "ready" else 1
