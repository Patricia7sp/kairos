"""Ferramentas nativas do núcleo do Kairos (Terminal, Sistema de Arquivos e Busca)."""

from __future__ import annotations

import asyncio
import os
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from kairos_tools.registry import ToolRegistry, registry
from kairos_tools.search_results import parse_search_results


async def bash_tool(command: str, cwd: str | None = None, timeout: int = 60) -> dict[str, Any]:
    """Executa um comando shell no sistema operacional."""
    work_dir = os.path.expanduser(cwd) if cwd else os.getcwd()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=float(timeout))
        except TimeoutError:
            proc.kill()
            return {"error": f"Comando expirou após {timeout}s", "exit_code": -1}

        out_str = stdout.decode("utf-8", errors="replace")
        err_str = stderr.decode("utf-8", errors="replace")
        return {
            "stdout": out_str,
            "stderr": err_str,
            "exit_code": proc.returncode,
            "success": proc.returncode == 0,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "exit_code": -1}


async def read_file_tool(path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
    """Lê o conteúdo de um arquivo de texto no sistema de arquivos."""
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return {"error": f"Arquivo não encontrado: {path}"}
    if not p.is_file():
        return {"error": f"O caminho não é um arquivo: {path}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        selected_lines = lines[offset : offset + limit]
        return {
            "path": str(p),
            "content": "\n".join(selected_lines),
            "total_lines": len(lines),
            "offset": offset,
            "returned_lines": len(selected_lines),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Erro ao ler arquivo: {exc}"}


async def write_file_tool(path: str, content: str) -> dict[str, Any]:
    """Escreve conteúdo em um arquivo, criando diretórios pais se necessário."""
    p = Path(os.path.expanduser(path))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"path": str(p), "bytes_written": len(content.encode("utf-8")), "success": True}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Erro ao escrever arquivo: {exc}", "success": False}


async def edit_file_tool(path: str, old_str: str, new_str: str) -> dict[str, Any]:
    """Substitui um trecho exato de texto dentro de um arquivo existente."""
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return {"error": f"Arquivo não encontrado: {path}"}
    try:
        text = p.read_text(encoding="utf-8")
        if old_str not in text:
            return {"error": f"Trecho alvo não encontrado no arquivo {path}", "success": False}
        occurrences = text.count(old_str)
        if occurrences > 1:
            return {
                "error": f"O trecho alvo aparece {occurrences} vezes no arquivo. Forneça mais contexto para que a substituição seja única.",
                "success": False,
            }
        new_text = text.replace(old_str, new_str, 1)
        p.write_text(new_text, encoding="utf-8")
        return {"path": str(p), "replaced": True, "success": True}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Erro ao editar arquivo: {exc}", "success": False}


async def list_dir_tool(path: str = ".") -> dict[str, Any]:
    """Lista os arquivos e subdiretórios dentro de um diretório."""
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return {"error": f"Diretório não encontrado: {path}"}
    if not p.is_dir():
        return {"error": f"O caminho não é um diretório: {path}"}
    try:
        entries = []
        for item in sorted(p.iterdir()):
            entries.append(
                {
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                }
            )
        return {"path": str(p), "entries": entries, "count": len(entries)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Erro ao listar diretório: {exc}"}


async def web_search_tool(query: str, max_results: int = 5) -> dict[str, Any]:
    """Busca fontes no HTML do DuckDuckGo, com resposta limitada a 1 MiB."""
    if not isinstance(query, str) or not query.strip():
        return {"error": "Informe um termo de busca não vazio.", "status": "invalid_arguments"}
    if type(max_results) is not int or not 1 <= max_results <= 20:
        return {
            "error": "max_results deve ser um inteiro entre 1 e 20.",
            "status": "invalid_arguments",
        }
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    unavailable = {"query": query, "status": "unavailable", "results": []}
    try:
        async with (
            httpx.AsyncClient(timeout=10.0) as client,
            client.stream("GET", url, headers=headers) as res,
        ):
            if res.status_code != 200:
                return {**unavailable, "error": f"Status HTTP {res.status_code} ao buscar"}
            body = bytearray()
            async for chunk in res.aiter_bytes(chunk_size=16_384):
                if len(body) + len(chunk) > 1_048_576:
                    return {**unavailable, "error": "A resposta da busca excedeu o limite."}
                body.extend(chunk)
        results = parse_search_results(body.decode("utf-8", errors="replace"), max_results)
        if results is None:
            return {
                **unavailable,
                "error": "O serviço retornou uma página inesperada ou um desafio de acesso.",
            }
        return {"query": query, "status": "ok", "results": results}
    except Exception as exc:  # noqa: BLE001
        return {**unavailable, "error": f"Falha na busca web: {exc}"}


def register_builtin_tools(reg: ToolRegistry | None = None) -> None:
    """Registra todas as ferramentas nativas no registro padrão."""
    r = reg or registry

    # 1. bash
    r.register(
        name="bash",
        handler=bash_tool,
        schema={
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Executa um comando shell no terminal.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "O comando bash a executar"},
                        "cwd": {
                            "type": "string",
                            "description": "Diretório de trabalho (opcional)",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout em segundos (padrão 60)",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        toolset="core",
    )

    # 2. read_file
    r.register(
        name="read_file",
        handler=read_file_tool,
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Lê o conteúdo de um arquivo de texto.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Caminho do arquivo"},
                        "offset": {"type": "integer", "description": "Linha inicial (padrão 0)"},
                        "limit": {
                            "type": "integer",
                            "description": "Número de linhas a ler (padrão 2000)",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        toolset="core",
    )

    # 3. write_file
    r.register(
        name="write_file",
        handler=write_file_tool,
        schema={
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Cria ou sobrescreve um arquivo de texto com o conteúdo fornecido.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Caminho do arquivo"},
                        "content": {"type": "string", "description": "Conteúdo textual a gravar"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        toolset="core",
    )

    # 4. edit_file
    r.register(
        name="edit_file",
        handler=edit_file_tool,
        schema={
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Substitui um trecho exato em um arquivo existente.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Caminho do arquivo"},
                        "old_str": {
                            "type": "string",
                            "description": "Texto exato a ser substituído",
                        },
                        "new_str": {
                            "type": "string",
                            "description": "Novo texto que substituirá o antigo",
                        },
                    },
                    "required": ["path", "old_str", "new_str"],
                },
            },
        },
        toolset="core",
    )

    # 5. list_dir
    r.register(
        name="list_dir",
        handler=list_dir_tool,
        schema={
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "Lista arquivos e pastas dentro de um diretório.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Diretório a listar (padrão .)"},
                    },
                },
            },
        },
        toolset="core",
    )

    # 6. web_search
    r.register(
        name="web_search",
        handler=web_search_tool,
        schema={
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Pesquisa na web por informações atualizadas.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Termo de busca"},
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 5,
                            "description": "Máximo de resultados, entre 1 e 20 (padrão 5)",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        toolset="core",
    )


# Registra por padrão
register_builtin_tools()
