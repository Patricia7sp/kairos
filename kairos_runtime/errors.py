"""Erros públicos e seguros do Agent Runtime."""

from __future__ import annotations

__all__ = ["RUNTIME_ERROR_CODES", "RuntimeErrorInfo"]


RUNTIME_ERROR_CODES = frozenset(
    {
        "baseline_missing",
        "review_too_large",
        "unavailable",
        "incompatible",
        "thread_missing",
        "invalid_directory",
        "invalid_policy",
        "session_busy",
        "idempotency_conflict",
        "lease_lost",
        "approval_denied",
        "approval_stale",
        "cancel_partial",
        "transport",
        "invalid_event",
        "sequence_gap",
        "runtime_internal",
    }
)


class RuntimeErrorInfo(Exception):
    """Falha serializável sem detalhes internos do runtime."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        if code not in RUNTIME_ERROR_CODES:
            raise ValueError(f"código de erro de runtime desconhecido: {code!r}")
        if not isinstance(message, str) or not message:
            raise ValueError("message é obrigatório")
        if type(retryable) is not bool:
            raise TypeError("retryable deve ser booleano")
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message}")
