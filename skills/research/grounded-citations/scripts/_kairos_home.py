"""Resolve KAIROS_HOME for standalone skill scripts.

Skill scripts may run outside the Kairos process (system Python, nix env,
CI) where ``kairos_constants`` is not importable.  This module provides the
same ``get_kairos_home()`` contract without requiring it on ``sys.path``.

When ``kairos_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from kairos_constants import get_kairos_home as get_kairos_home
except (ModuleNotFoundError, ImportError):

    def get_kairos_home() -> Path:
        """Return the Kairos home directory (default: ``~/.kairos``)."""
        val = os.environ.get("KAIROS_HOME", "").strip()
        return Path(val) if val else Path.home() / ".kairos"
