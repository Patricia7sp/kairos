"""Comando `kairos remind QUANDO MENSAGEM...` — lembrete de propósito geral.

Cria um job `once` (`kairos_cron.remind.parse_when`) com a mensagem como
instrução e, opcionalmente, entrega a saída no horário marcado via `--deliver
plataforma:destino`. Sem `--deliver`, vale o alvo home do e-mail quando
configurado (o envio de propósito geral por padrão escolhe o canal de `home`).
Plataforma pedida mas não entregável = recusa honesta (69), não lembrete mudo.
"""

from __future__ import annotations

import json
from pathlib import Path

_EMAIL_HOME = "__email_home__"


def _emit(dados, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(dados, ensure_ascii=False, indent=2, default=str))
    else:
        for k, v in dados.items():
            print(f"{k}: {v}")


def _alvo_email_home(home: Path) -> str | None:
    from kairos_gateway.adapters.config import load_config

    alvo = load_config(home).get("email", {}).get("address_default", "")
    return f"email:{alvo}" if alvo else None


def run_remind(home: Path, args) -> int:
    from datetime import UTC, datetime

    from kairos_cron.jobs import JobStore
    from kairos_cron.remind import parse_when
    from kairos_gateway.adapters import build_platform_adapters

    home = Path(home)
    as_json = bool(getattr(args, "json", False))

    quando = getattr(args, "quando", "").strip()
    mensagem = " ".join(getattr(args, "mensagem", [])).strip()
    if not quando or not mensagem:
        return _erro("kairos remind exige QUANDO e MENSAGEM", as_json)

    try:
        schedule = parse_when(quando)
    except ValueError as exc:
        return _erro(str(exc), as_json)

    pedido = getattr(args, "deliver", None)
    alvo = None
    if pedido is not None:
        if pedido == _EMAIL_HOME:
            alvo = _alvo_email_home(home)
            if alvo is None:
                return _erro(
                    "delivery ao email: configure 'address_default' (alvo home) primeiro",
                    as_json,
                    codigo=69,
                )
        else:
            alvo = pedido.strip()
        plataforma, separador, _ = alvo.partition(":")
        if not separador or not plataforma:
            return _erro(f"alvo inválido: {alvo!r} (use plataforma:destino)", as_json)
        if build_platform_adapters(home).get(plataforma) is None:
            return _erro(
                f"plataforma '{plataforma}' não está entregável: habilite-a e salve a credencial",
                as_json,
                codigo=69,
            )

    delivery = {"target": alvo} if alvo else None
    job = JobStore(home).create(
        name=f"lembrete {quando}",
        prompt=mensagem,
        schedule=schedule,
        now=datetime.now(UTC),
        delivery=delivery,
    )
    _emit(
        {
            "job_id": job["id"],
            "run_at": job["next_run_at"],
            "delivery": f"ao {alvo}" if alvo else "sem entrega",
        },
        as_json=as_json,
    )
    return 0


def _erro(mensagem: str, as_json: bool, *, codigo: int = 2) -> int:
    _emit({"erro": mensagem}, as_json=as_json)
    print(f"kairos remind: {mensagem}")
    return codigo
