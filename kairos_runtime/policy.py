"""Políticas de diretório, sandbox e negociação do Agent Runtime.

Identidade de diretório é capturada com
``capture_directory_identity(raw, allowed) -> DirectoryIdentity`` e deve ser
revalidada imediatamente antes de cada lease com
``revalidate_directory_identity(identity) -> str``. A identidade conserva o
caminho pedido, o caminho canônico e ``st_dev/st_ino`` para detectar tanto
substituição do diretório quanto redirecionamento de symlink.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import RuntimeCapabilities, Sandbox
from .errors import RuntimeErrorInfo

__all__ = [
    "RUNTIME_V1_FEATURES",
    "DirectoryIdentity",
    "authorize_directory",
    "capture_directory_identity",
    "negotiate",
    "revalidate_directory_identity",
    "validate_sandbox",
]


RUNTIME_V1_FEATURES = frozenset(
    {"text", "reasoning", "tools", "approvals", "cancel", "resume", "snapshot_reconcile"}
)


def _invalid_directory() -> RuntimeErrorInfo:
    return RuntimeErrorInfo("invalid_directory", "diretório não autorizado", False)


@dataclass(frozen=True)
class DirectoryIdentity:
    """Token estável para revalidar uma raiz autorizada antes de obter lease."""

    requested_path: str
    canonical_path: str
    device: int
    inode: int

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.requested_path, self.canonical_path)
        ):
            raise _invalid_directory()
        if type(self.device) is not int or type(self.inode) is not int:
            raise _invalid_directory()


def authorize_directory(raw: str, allowed: tuple[str, ...]) -> str:
    """Return an authorized canonical root; descendants are not implicitly allowed."""

    try:
        candidate = Path(raw).resolve(strict=True)
        roots = {Path(value).resolve(strict=True) for value in allowed}
    except (OSError, RuntimeError, TypeError) as exc:
        raise _invalid_directory() from exc
    if not candidate.is_dir() or candidate not in roots:
        raise _invalid_directory()
    return str(candidate)


def capture_directory_identity(raw: str, allowed: tuple[str, ...]) -> DirectoryIdentity:
    """Authorize ``raw`` and capture canonical path, device and inode."""

    if not isinstance(raw, str):
        raise _invalid_directory()
    path = Path(raw)
    requested_path = str(path if path.is_absolute() else Path.cwd() / path)
    canonical_path = authorize_directory(requested_path, allowed)
    try:
        stat = Path(canonical_path).stat()
    except OSError as exc:
        raise _invalid_directory() from exc
    identity = DirectoryIdentity(
        requested_path=requested_path,
        canonical_path=canonical_path,
        device=stat.st_dev,
        inode=stat.st_ino,
    )
    revalidate_directory_identity(identity)
    return identity


def revalidate_directory_identity(identity: DirectoryIdentity) -> str:
    """Revalidate the original path and filesystem identity before a future lease."""

    if not isinstance(identity, DirectoryIdentity):
        raise _invalid_directory()
    try:
        resolved = Path(identity.requested_path).resolve(strict=True)
        stat = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise _invalid_directory() from exc
    if (
        not resolved.is_dir()
        or str(resolved) != identity.canonical_path
        or stat.st_dev != identity.device
        or stat.st_ino != identity.inode
    ):
        raise _invalid_directory()
    return identity.canonical_path


def validate_sandbox(profile: str, broad_enabled: bool, consent: bool) -> Sandbox:
    """Validate an explicit sandbox profile, including broad-access gates."""

    if type(broad_enabled) is not bool or type(consent) is not bool:
        raise RuntimeErrorInfo("invalid_policy", "política de sandbox inválida", False)
    if profile not in {"read_only", "workspace_write", "broad_access"}:
        raise RuntimeErrorInfo("invalid_policy", "política de sandbox inválida", False)
    if profile == "broad_access" and not (broad_enabled and consent):
        raise RuntimeErrorInfo(
            "invalid_policy", "acesso amplo exige configuração e consentimento", False
        )
    return profile


def negotiate(caps: RuntimeCapabilities) -> RuntimeCapabilities:
    """Validate v1 and suppress unsupported capability announcements."""

    if caps.protocol_version != 1:
        raise RuntimeErrorInfo("incompatible", "runtime incompatível com o contrato v1", False)
    return RuntimeCapabilities(protocol_version=1, features=caps.features & RUNTIME_V1_FEATURES)
