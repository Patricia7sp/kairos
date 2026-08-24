"""Regras de credencial e segredo — ``_reversa_sdd/domain.md`` §2.4."""

from __future__ import annotations

from enum import StrEnum

from kairos_domain.message import DomainRuleViolation

__all__ = [
    "PLACEHOLDER_SECRETS",
    "AuthFailureKind",
    "NotACredentialError",
    "SecretError",
    "assert_env_is_for_secrets",
    "classify_auth_failure",
    "has_usable_secret",
    "should_reauthenticate",
]

#: Valores que *parecem* segredo e não são. Aceitar um destes produz uma
#: falha de autenticação confusa lá na frente, em vez de um erro claro aqui.
PLACEHOLDER_SECRETS = frozenset(
    {
        "changeme",
        "change_me",
        "your_api_key",
        "your-api-key",
        "yourapikey",
        "xxx",
        "***",
        "<your_key>",
        "todo",
        "tbd",
        "placeholder",
        "none",
        "null",
    }
)


class SecretError(DomainRuleViolation):
    """Violação de uma regra de credencial."""


class NotACredentialError(SecretError):
    """``.env`` é só para segredos; ajuste comportamental vai em config."""


def has_usable_secret(value: str | None) -> bool:
    """Um segredo utilizável não é vazio nem um placeholder conhecido."""
    if value is None:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    return stripped.lower() not in PLACEHOLDER_SECRETS


def assert_env_is_for_secrets(key: str, *, is_credential: bool) -> None:
    """*"Reject PRs that tell users to 'set X in your .env' unless X is a credential."*

    A regra existe porque misturar configuração e segredo no mesmo arquivo
    faz o arquivo inteiro herdar o tratamento de segredo: não versionável,
    não documentável, não diffável em review.
    """
    if not is_credential:
        raise NotACredentialError(
            f"{key!r} não é credencial e não pertence ao .env; "
            "ajuste comportamental vai em config.yaml"
        )


class AuthFailureKind(StrEnum):
    AUTHENTICATION = "authentication"  # 401/403 confirmado
    CONNECTIVITY = "connectivity"  # timeout, rede, 5xx


def classify_auth_failure(
    status_code: int | None,
    *,
    timed_out: bool = False,
    network_error: bool = False,
) -> AuthFailureKind:
    """**Só 401/403 confirmado** significa reautenticar.

    Tratar timeout como falha de autenticação é o caminho mais curto para
    derrubar a sessão de um usuário por causa de um blip de rede — e para
    ensiná-lo a reautenticar por reflexo, que é exatamente o hábito que um
    phishing explora.
    """
    if timed_out or network_error:
        return AuthFailureKind.CONNECTIVITY
    if status_code in (401, 403):
        return AuthFailureKind.AUTHENTICATION
    return AuthFailureKind.CONNECTIVITY


def should_reauthenticate(
    status_code: int | None,
    *,
    timed_out: bool = False,
    network_error: bool = False,
) -> bool:
    return (
        classify_auth_failure(status_code, timed_out=timed_out, network_error=network_error)
        is AuthFailureKind.AUTHENTICATION
    )
