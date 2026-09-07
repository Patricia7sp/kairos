"""Validate bounded, opaque worker checkpoints; never extract them on the host."""

from __future__ import annotations

import io
import tarfile

MAX_ARCHIVE_BYTES = 80 * 1024**2
MAX_CONTENT_BYTES = 64 * 1024**2
MAX_ENTRIES = 10_000


def _member_path(member: tarfile.TarInfo, blob: bytes) -> list[str]:
    path = member.name
    # tarfile truncates legacy header names at NUL. Reject hidden suffixes,
    # instead of validating a different name from what was submitted.
    header = blob[member.offset_data - 512 : member.offset_data]
    fields = (header[:100], header[157:257])
    if header[257:263] == b"ustar\x00":
        fields += (header[345:500],)
    if any(b"\x00" in field and any(field.split(b"\x00", 1)[1]) for field in fields):
        raise ValueError("checkpoint name contains embedded NUL")
    if (
        not path
        or len(path.encode("utf-8", "surrogateescape")) > 4096
        or "\\" in path
        or "\x00" in path
    ):
        raise ValueError("checkpoint path invalid")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("checkpoint path is not canonical")
    return parts


def validate_archive(blob: bytes) -> bytes:
    """Accept uncompressed tar containing only canonical regular files/directories.

    PAX long names produced by snapshot_project are supported. Link, sparse and
    special entries are forbidden, including conflicts with implicit directories.
    """
    if not isinstance(blob, bytes) or not 1024 <= len(blob) <= MAX_ARCHIVE_BYTES:
        raise ValueError("checkpoint archive size invalid")
    entries: dict[str, bool] = {}
    directories: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
            for count, member in enumerate(archive, 1):
                if count > MAX_ENTRIES:
                    raise ValueError("checkpoint entry limit exceeded")
                path = member.name
                parts = _member_path(member, blob)
                if path in entries:
                    raise ValueError("checkpoint path invalid or duplicated")
                is_directory = member.isdir()
                if (not is_directory and not member.isreg()) or member.sparse is not None:
                    raise ValueError("checkpoint contains links or special files")
                if member.size < 0 or (is_directory and member.size != 0):
                    raise ValueError("checkpoint entry size invalid")
                total += member.size
                if total > MAX_CONTENT_BYTES:
                    raise ValueError("checkpoint content limit exceeded")
                ancestors = ["/".join(parts[:index]) for index in range(1, len(parts))]
                if any(entries.get(parent) is False for parent in ancestors):
                    raise ValueError("checkpoint file used as directory")
                if not is_directory and path in directories:
                    raise ValueError("checkpoint directory replaced by file")
                entries[path] = is_directory
                directories.update(ancestors)
                # Require the complete padded content, even when tarfile would
                # seek over a truncated last entry without reading its contents.
                end = member.offset_data + ((member.size + 511) // 512) * 512
                if end > len(blob):
                    raise ValueError("checkpoint content truncated")
            end = archive.offset
            if len(blob) - end < 1024 or any(blob[end:]):
                raise ValueError("checkpoint terminator or trailing content invalid")
    except (tarfile.TarError, OSError, EOFError, UnicodeError) as exc:
        raise ValueError("checkpoint archive invalid") from exc
    return blob
