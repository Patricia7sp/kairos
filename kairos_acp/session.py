"""Métodos de sessão do ACP e conversão de blocos de conteúdo.

`_reversa_sdd/acp-adapter/` §2-3 (Tarefa 17).
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import unquote, urlparse

__all__ = [
    "PROTOCOL_VERSION",
    "SESSION_METHODS",
    "BlockKind",
    "Capabilities",
    "classify_resource",
    "content_blocks_to_parts",
    "negotiate_version",
    "path_from_file_uri",
]

PROTOCOL_VERSION = 1

#: Os 9 métodos de sessão, mais `set_config_option`.
SESSION_METHODS: tuple[str, ...] = (
    "new_session",
    "load_session",
    "resume_session",
    "fork_session",
    "list_sessions",
    "prompt",
    "cancel",
    "set_session_model",
    "set_session_mode",
    "set_config_option",
)


@dataclass(frozen=True)
class Capabilities:
    load_session: bool = True
    prompt_image: bool = True
    session_fork: bool = True
    session_list: bool = True
    session_resume: bool = True


def negotiate_version(client_version: int) -> int:
    """Aceita a versão do cliente, **responde sempre com a própria**.

    O valor recebido serve só para log. Espelhar a versão do cliente seria
    prometer um protocolo que o agente não implementa — e o editor
    silenciosamente usaria recursos que não existem.
    """
    return PROTOCOL_VERSION


class BlockKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    RESOURCE_LINK = "resource_link"
    EMBEDDED_RESOURCE = "embedded_resource"


def path_from_file_uri(uri: str) -> Path | None:
    """`file://` → `Path`. Devolve `None` para o que não é `file://`."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


_TEXT_PREFIXES = ("text/",)
_TEXT_EXACT = frozenset(
    {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/javascript",
        "application/toml",
    }
)


def classify_resource(mime: str | None, path: str | None = None) -> BlockKind:
    """Classifica por MIME, **adivinhando pela extensão quando o MIME falta**.

    Editores omitem o MIME com frequência. Sem o palpite, um `.png` anexado
    viraria texto binário no prompt — ruído caro em tokens e inútil para o
    modelo.
    """
    if mime:
        baixo = mime.lower()
        if baixo.startswith("image/"):
            return BlockKind.IMAGE
        if baixo.startswith(_TEXT_PREFIXES) or baixo in _TEXT_EXACT:
            return BlockKind.TEXT

    if path:
        adivinhado, _ = mimetypes.guess_type(path)
        if adivinhado and adivinhado.lower().startswith("image/"):
            return BlockKind.IMAGE

    return BlockKind.TEXT


def decode_text_bytes(data: bytes) -> str:
    """Decodificação **tolerante**.

    Um arquivo com um byte inválido não pode derrubar o turno: o usuário
    anexou o arquivo justamente para que o agente o olhasse.
    """
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def image_data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


@dataclass
class ContentBlock:
    kind: BlockKind
    text: str | None = None
    data: bytes | None = None
    mime: str | None = None
    uri: str | None = None


def content_blocks_to_parts(blocks: list[ContentBlock]) -> list[dict]:
    """Consolida as quatro formas de bloco no formato do provedor."""
    partes: list[dict] = []
    for b in blocks:
        if b.kind is BlockKind.IMAGE and b.data is not None:
            partes.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url(b.data, b.mime or "image/png")},
                }
            )
            continue

        if b.text is not None:
            partes.append({"type": "text", "text": b.text})
            continue

        if b.data is not None:
            partes.append({"type": "text", "text": decode_text_bytes(b.data)})
            continue

        if b.uri:
            # Link sem conteúdo: entra como referência textual, para o modelo
            # poder buscá-lo com as ferramentas que já tem.
            partes.append({"type": "text", "text": f"[recurso: {b.uri}]"})

    return partes
