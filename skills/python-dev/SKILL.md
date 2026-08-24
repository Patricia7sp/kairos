---
name: python-dev
description: Desenvolvimento e depuração em Python
version: 1.0.0
tags: [python, pytest, ruff]
---

# Python Development Skill

## When to Use
Use para escrever código Python, criar testes com pytest e validar estilos com ruff.

## Prerequisites
- Python 3.11+ e uv instalados.

## How to Run
Execute testes com `uv run pytest` e linters com `uv run ruff check .`.

## Quick Reference
- `uv run pytest`: roda testes
- `uv run ruff check --fix .`: corrige lints

## Procedure
1. Escrever implementação modular
2. Criar testes unitários em `tests/`
3. Executar linters e formatadores

## Pitfalls
- Sempre garanta imports absolutos ou consistentes.

## Verification
Confirme 100% de testes verdes com pytest.
