"""Credenciais: lock cross-process, herança somente-leitura e failover.

`_reversa_sdd/hermes-cli/` §3 (Tarefa 15).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "PLACEHOLDER_SECRETS",
    "AuthStore",
    "CredentialPool",
    "LockBusy",
    "auth_lock",
    "has_usable_secret",
]

#: Reexportado do domínio (Tarefa 02) para manter uma definição só.
from kairos_domain.credentials import PLACEHOLDER_SECRETS, has_usable_secret


class LockBusy(RuntimeError):
    """Outro processo — ou outra thread — detém o lock."""


#: `threading.local` porque **o flock do kernel não separa threads do mesmo
#: processo**. Duas threads do mesmo processo passariam as duas pelo
#: `flock(LOCK_EX)` e escreveriam por cima uma da outra; o holder por thread é
#: o que fecha essa lacuna.
_holder = threading.local()


@contextmanager
def auth_lock(path: Path) -> Iterator[None]:
    """Lock consultivo cross-process **e** entre threads.

    Duas camadas porque cada uma cobre o que a outra não cobre: `flock` separa
    processos, o holder por thread separa threads.
    """
    ja_detem = getattr(_holder, "paths", None) or set()
    if str(path) in ja_detem:
        raise LockBusy(f"esta thread já detém o lock de {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = open(lock_path, "a+")  # noqa: SIM115 — fechado no finally
    try:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            # Windows: `msvcrt.locking` é a primitiva equivalente. Sem
            # nenhuma das duas, degradamos para o lock só por thread — pior,
            # mas melhor que não ter lock nenhum.
            pass
        except OSError as exc:
            raise LockBusy(f"outro processo detém o lock de {path}") from exc

        _holder.paths = ja_detem | {str(path)}
        yield
    finally:
        _holder.paths = (getattr(_holder, "paths", set()) or set()) - {str(path)}
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        fh.close()


class Origin(StrEnum):
    PROFILE = "profile"
    GLOBAL_READONLY = "global_readonly"


@dataclass
class AuthStore:
    """`auth.json` do perfil, com herança somente-leitura do global.

    Em modo perfil, o `auth.json` do perfil é **a autoridade**. Provedores não
    configurados nele herdam do global em modo **somente-leitura**, e toda
    escrita nova vai para o perfil ativo.

    A assimetria é o ponto: sem ela, configurar um provedor dentro de um
    perfil escreveria no global e vazaria para os outros perfis — que é
    exatamente o isolamento que o perfil existe para dar.
    """

    profile: dict[str, list[dict]] = field(default_factory=dict)
    global_store: dict[str, list[dict]] = field(default_factory=dict)
    vault: Any | None = None

    def credentials_for(self, provider: str) -> tuple[list[dict], Origin]:
        if provider in self.profile:
            return self.profile[provider], Origin.PROFILE
        if provider in self.global_store:
            return self.global_store[provider], Origin.GLOBAL_READONLY
        return [], Origin.PROFILE

    def add_credential(self, provider: str, credential: dict) -> Origin:
        """Escreve **sempre** no perfil ativo."""
        self.profile.setdefault(provider, []).append(credential)
        return Origin.PROFILE

    def is_readonly(self, provider: str) -> bool:
        return provider not in self.profile and provider in self.global_store

    def write_atomically(self, path: Path) -> None:
        """Escrita transacional: `tmp` + `replace`, sob lock.

        Um `auth.json` meio escrito é indistinguível de um sem credencial, e
        o sintoma aparece como falha de autenticação — não como arquivo
        corrompido.
        """
        if self.vault is not None and _contains_secret(self.profile):
            raise ValueError("segredo não pode ser persistido no auth.json com cofre configurado")
        with auth_lock(path):
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps({"credential_pool": self.profile}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        if {str(key).lower() for key in value} & {"api_key", "token", "secret", "password"}:
            return True
        return any(_contains_secret(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


@dataclass
class CredentialPool:
    """Rotação após erro **transitório** de cota ou autenticação.

    O que conta como transitório vem do domínio (Tarefa 02): só 401/403
    confirmado é autenticação; timeout e rede são conectividade. Rotacionar
    por timeout gastaria todas as chaves num blip de rede.
    """

    credentials: list[dict] = field(default_factory=list)
    _index: int = 0
    _exhausted: set[int] = field(default_factory=set)

    def current(self) -> dict | None:
        usaveis = [i for i in range(len(self.credentials)) if i not in self._exhausted]
        if not usaveis:
            return None
        if self._index not in usaveis:
            self._index = usaveis[0]
        return self.credentials[self._index]

    def rotate(self) -> dict | None:
        """Marca a atual como esgotada e passa à próxima."""
        if not self.credentials:
            return None
        self._exhausted.add(self._index)
        return self.current()

    def reset(self) -> None:
        self._exhausted.clear()
        self._index = 0

    @property
    def exhausted(self) -> bool:
        return len(self._exhausted) >= len(self.credentials) > 0
