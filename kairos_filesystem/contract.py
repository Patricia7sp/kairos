"""Contratos de inventário e snapshot de árvores locais."""

from dataclasses import dataclass


class FilesystemError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class TreeLimits:
    total_bytes: int
    file_bytes: int
    entries: int
    depth: int


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str
    mode: int
    size: int
    sha256: str | None
    device: int
    inode: int
    mount_id: int


@dataclass(frozen=True)
class TreeCapture:
    entries: tuple[TreeEntry, ...]
    contents: dict[str, bytes] | None
