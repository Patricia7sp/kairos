"""Leitura do config.yaml por descritor regular, com ausência explícita."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def read_config_text(home: Path) -> str | None:
    """Retorna None só na ausência da entrada; aliases regulares são válidos."""
    path = Path(home) / "config.yaml"
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"config.yaml ilegível: {type(exc).__name__}") from None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("config.yaml deve ser um arquivo regular")
            with open(descriptor, encoding="utf-8", closefd=False) as source:
                return source.read()
        finally:
            os.close(descriptor)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"config.yaml ilegível: {type(exc).__name__}") from None
