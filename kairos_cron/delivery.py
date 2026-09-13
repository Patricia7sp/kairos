"""Cron delivery: targets derived from registered adapters, delivered via the
gateway's durable obligation ledger.

The legacy scheduler kept a hardcoded notion of platforms. Kairos inverts it:
a delivery target is ``plataforma:destino`` and the only source of what is
deliverable is the set of *registered* adapters. Nothing here owns a platform
list. The dispatcher is the gateway (``kairos_gateway.service.GatewayService``);
this module only:

- derives the dropdown of targets from registered adapter names,
- shape-validates a job's requested delivery,
- records the completion as a durable obligation (``delivery_obligations`` in
  ``state.db``) once the scheduled turn finishes.

The gateway then picks the obligation up through the same ledger it uses for
any other delivery — cron output is not a second delivery system.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from kairos_state import connect, migrate

TARGET_MAX_CHARS = 200
DELIVERY_PAYLOAD_MAX_CHARS = 64_000

_CONTROL_CHARS = {ch for ch in range(32)} | {127}


class DeliveryTargetError(ValueError):
    """Target pedido no job não é um ``plataforma:destino`` válido (ou o
    prefixo não está entre os adapters registrados)."""


def cron_delivery_targets(adapters: Iterable[str]) -> list[dict]:
    """Deriva os alvos de delivery **exclusivamente** dos adapters registrados.

    Sem lista de plataforma hardcoded: o conjunto de entrada é a única fonte.
    Cada adapter registrado vira um alvo ``{"id": nome, "name": nome}``. O
    ``local`` implícito é decisão da superfície que lista (espelha o legado).
    """
    names = sorted(
        {a for a in adapters if isinstance(a, str) and a and not a.isspace()},
        key=str.casefold,
    )
    return [{"id": name, "name": name.replace("_", " ").strip().title()} for name in names]


def validate_delivery(delivery, adapters: Iterable[str] | None = None) -> dict | None:
    """Normaliza e valida o pedido de delivery de um job.

    A forma é ``plataforma:destino``: prefixo antes do primeiro ``:`` escolhe o
    adapter, o restante é o endereço no destino. Quando ``adapters`` vem
    preenchido (as chaves de adapters registrados), o prefixo precisa estar nele
    — é assim que a criação deriva do que está registrado, nunca de uma lista
    fixa. Com ``adapters`` vazio/ausente só a forma é verificada; a presença do
    adapter continua sendo decisão dinâmica do dispatcher.
    """
    if delivery is None:
        return None
    if not isinstance(delivery, dict):
        raise DeliveryTargetError("delivery deve ser {target: plataforma:destino}")
    target = delivery.get("target")
    if not isinstance(target, str) or not 1 <= len(target) <= TARGET_MAX_CHARS:
        raise DeliveryTargetError("entrega exige target text com 1 a 200 caracteres")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        raise DeliveryTargetError("target não pode conter caracteres de controle")
    if ":" not in target:
        raise DeliveryTargetError("target deve ser plataforma:destino")
    plataforma, _, destino = target.partition(":")
    if not plataforma or not destino:
        raise DeliveryTargetError("target deve ser plataforma:destino")
    adapters_registrados = {
        a for a in (adapters or ()) if isinstance(a, str) and a and not a.isspace()
    }
    if adapters_registrados and plataforma not in adapters_registrados:
        raise DeliveryTargetError(
            f"plataforma '{plataforma}' não está entre os adapters registrados"
        )
    return {"target": target}


def record_cron_delivery(home: Path, obligation_id: str, target: str, payload: str) -> dict:
    """Grava a saída completada como obrigação durável no ledger do gateway.

    Obrigação vira ``pending`` em ``delivery_obligations`` na mesma cadência que
    o dispatcher drena. O payload é o texto final do turno, truncado ao teto
    documentado. Chamado só quando o job pediu delivery; falha de persistência é
    do chamador tratar sem quebrar o turno.
    """
    from kairos_state.repositories.ledger import LedgerRepository

    if len(payload) > DELIVERY_PAYLOAD_MAX_CHARS:
        payload = payload[:DELIVERY_PAYLOAD_MAX_CHARS]
    db = connect(Path(home) / "state.db")
    try:
        migrate(db)
        obligation = LedgerRepository(db).record(obligation_id, target, payload)
        return {
            "obligation_id": obligation.obligation_id,
            "target": obligation.target,
            "state": "pending",
        }
    finally:
        db.close()
