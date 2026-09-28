"""Envio direto com obrigação durável — a única cadeia de "enviar agora".

O envio nunca é um efeito sem rastro: a obrigação entra no ledger
(`delivery_obligations`) **antes** de o adapter ser chamado; um ACK do adapter
confirma, falha transitória devolve à fila (o gateway reentrega) e falha
permanente abandona. É o mesmo fluxo que o painel usa
(`kairos_web/messaging_api.py`), e o CLI `kairos gateway send` — uma única
cadeia, sem segundo caminho de entrega.

`_reversa_sdd/providers-gateway/requirements.md` RF-07: mensagens assíncronas
são gravadas e persistem até confirmação.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from kairos_cron.delivery import DeliveryTargetError

__all__ = ["DeliveryTargetError", "send_now"]


def send_now(home: Path, target: str, payload: str) -> dict:
    """Envia agora via adapter registrado, com obrigação no ledger.

    Alvo: ``plataforma:destino``. Target malformado (sem separador, parte vazia
    ou além do teto) levanta `ValueError` — erro de uso. Plataforma não
    entregável (desabilitada ou sem segredo no cofre) levanta
    `DeliveryTargetError` — recusa honesta; o chamador decide o código de
    saída. Resultado:

    - ``{"delivered": True, ...}``: obrigação confirmada pelo adapter;
    - ``{"delivered": False, "pending": True, ...}``: falha transitória,
      ``pending`` para o gateway reentregar;
    - ``{"delivered": False, "pending": False, "abandoned": True, ...}``:
      falha permanente, obrigação abandonada com rastro.
    """
    plataforma, separador, destino = target.partition(":")
    if not separador or not plataforma or not destino:
        raise ValueError("target deve ser plataforma:destino")
    if len(plataforma) > 64 or len(destino) > 200:
        raise ValueError("target com plataforma ou destino além do teto")

    from kairos_gateway.adapters import build_platform_adapters
    from kairos_gateway.service import SendResult
    from kairos_state import connect
    from kairos_state.migrations import migrate
    from kairos_state.repositories.ledger import LedgerRepository

    adapter = build_platform_adapters(Path(home)).get(plataforma)
    if adapter is None:
        raise DeliveryTargetError(
            f"plataforma '{plataforma}' não está entregável: habilite-a e salve a credencial no cofre"
        )

    obligation_id = uuid.uuid4().hex
    db = connect(Path(home) / "state.db")
    try:
        migrate(db)
        repo = LedgerRepository(db)
        repo.record(obligation_id, target, payload)
        pid, started = _pid(), _started_at()
        if not repo.claim(obligation_id, pid=pid, started_at=started):
            return {"delivered": False, "pending": True, "obligation_id": obligation_id}

        try:
            resultado = adapter.send(target, payload)
        except Exception:  # noqa: BLE001 - adapter honesto é esperado, mas a cadeia não pode morrer com ele
            resultado = SendResult(ok=False, retryable=True, error_kind="excecao")
        if resultado.ok:
            repo.confirm(obligation_id)
            return {"delivered": True, "pending": False, "obligation_id": obligation_id}
        if resultado.retryable:
            repo.release(obligation_id)
            return {
                "delivered": False,
                "pending": True,
                "obligation_id": obligation_id,
                "detail": f"falha temporária ({resultado.error_kind})",
            }
        repo.abandon(obligation_id)
        return {
            "delivered": False,
            "pending": False,
            "abandoned": True,
            "obligation_id": obligation_id,
            "detail": f"falha permanente ({resultado.error_kind})",
        }
    finally:
        db.close()


def _pid() -> int:
    import os

    return os.getpid()


def _started_at() -> int:
    import time

    return int(time.time())
