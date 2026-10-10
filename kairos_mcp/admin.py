"""Administração explícita de servidores MCP, sem boot do catálogo."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

import yaml

from kairos_mcp.client import MCPServerConfig, Transport, validate_server_config
from kairos_mcp.runtime import fetch_tool_manifests


class McpAdminError(ValueError):
    """Falha administrativa com mensagem própria, sem dados do servidor."""


class _StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            if key in result:
                raise McpAdminError("Configuração contém chaves duplicadas.")
            result[key] = loader.construct_object(value_node, deep=True)
        except TypeError:
            raise McpAdminError("Configuração contém chave inválida.") from None
    return result


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[\w-][\w. -]*", name) or name in {".", ".."}:
        raise McpAdminError("Nome de servidor inválido.")


def _safe_path(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise McpAdminError("Caminho simbólico recusado.")


def _read(home):
    path = Path(home).absolute() / "config.yaml"
    _safe_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return path, {}, None, None
    except OSError:
        raise McpAdminError("Não foi possível ler a configuração MCP.") from None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise McpAdminError("Configuração deve ser arquivo regular.")
            raw = stream.read()
        loader = _StrictLoader(raw.decode("utf-8"))
        try:
            data = loader.get_single_data()
        finally:
            loader.dispose()
        if not isinstance(data, dict):
            raise McpAdminError("Configuração deve ser um mapeamento.")
        servers = data.get("mcp_servers", {})
        if not isinstance(servers, dict) or not all(isinstance(k, str) for k in servers):
            raise McpAdminError("mcp_servers deve mapear nomes textuais para entradas.")
        return path, data, raw, info
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        raise McpAdminError("Não foi possível ler a configuração MCP.") from None


def _env(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError
    env = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key or "=" in key or "\0" in key:
            raise ValueError
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError
        text = str(value)
        if "\0" in text:
            raise ValueError
        env[key] = text
    return env


def _config(name, entry):
    _name(name)
    try:
        if not isinstance(entry, dict):
            raise ValueError
        raw_transport = entry.get("transport", "stdio")
        if not isinstance(raw_transport, str):
            raise ValueError
        transport = Transport(raw_transport.lower())
        command = entry.get("command")
        url = entry.get("url")
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError
        env = _env(entry.get("env"))
        if command is not None and (
            not isinstance(command, str) or not command.strip() or "\0" in command
        ):
            raise ValueError
        if url is not None and not isinstance(url, str):
            raise ValueError
        if any("\0" in arg for arg in args):
            raise ValueError
        cfg = MCPServerConfig(name, transport, command, tuple(args), url, env)
        validate_server_config(cfg)
        return cfg
    except (ValueError, TypeError):
        raise McpAdminError("Entrada de servidor MCP inválida.") from None


def list_servers(home: Path) -> list[dict]:
    """Lista configuração sem iniciar processos nem revelar conexão."""
    _, data, _, _ = _read(home)
    rows = []
    for name, entry in sorted(data.get("mcp_servers", {}).items()):
        row = {"nome": name, "transporte": "desconhecido", "valido": False}
        try:
            cfg = _config(name, entry)
            row["transporte"] = cfg.transport.value
            if cfg.transport is not Transport.STDIO:
                raise McpAdminError("Transporte não suportado: somente stdio.")
            row["valido"] = True
        except McpAdminError as exc:
            transport = entry.get("transport", "stdio") if isinstance(entry, dict) else None
            if isinstance(transport, str) and transport.lower() in ("stdio", "http", "sse"):
                row["transporte"] = transport.lower()
            row["erro"] = str(exc)
        rows.append(row)
    return rows


def test_server(home: Path, name: str) -> list[dict]:
    """Consulta somente initialize/tools/list do servidor escolhido."""
    _name(name)
    _, data, _, _ = _read(home)
    if name not in data.get("mcp_servers", {}):
        raise McpAdminError("Servidor MCP não encontrado.")
    cfg = _config(name, data["mcp_servers"][name])
    if cfg.transport is not Transport.STDIO:
        raise McpAdminError("Transporte não suportado: somente stdio.")
    try:
        return fetch_tool_manifests(cfg)
    except (OSError, RuntimeError, ValueError):
        raise McpAdminError("Falha ao consultar o servidor MCP.") from None


def remove_server(home: Path, name: str) -> bool:
    """Remove uma entrada e seu cache, preservando o restante da configuração."""
    _name(name)
    path, data, raw, info = _read(home)
    if name not in data.get("mcp_servers", {}):
        raise McpAdminError("Servidor MCP não encontrado.")
    cache = path.parent / "mcp" / f"{name}.json"
    _safe_path(cache)
    if cache.exists() and not cache.is_file():
        raise McpAdminError("Cache deve ser arquivo regular.")
    servers = dict(data["mcp_servers"])
    del servers[name]
    data["mcp_servers"] = servers
    tmp = None
    try:
        payload = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=".mcp-config-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _, _, current, current_info = _read(home)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        if current != raw or any(
            getattr(current_info, field, None) != getattr(info, field) for field in fields
        ):
            raise McpAdminError("Configuração alterada durante a remoção; tente novamente.")
        _safe_path(cache)
        os.replace(tmp, path)
        tmp = None
        try:
            cache.unlink()
        except FileNotFoundError:
            return False
        return True
    except (OSError, yaml.YAMLError):
        raise McpAdminError("Falha ao persistir a remoção MCP.") from None
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
