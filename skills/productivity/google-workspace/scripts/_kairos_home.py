"""Resolve KAIROS_HOME for standalone skill scripts.

Skill scripts may run outside the Kairos process (e.g. system Python,
nix env, CI) where ``kairos_constants`` is not importable.  This module
provides the same ``get_kairos_home()`` and ``display_kairos_home()``
contracts as ``kairos_constants`` without requiring it on ``sys.path``.

When ``kairos_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``kairos_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``KAIROS_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from kairos_constants import display_kairos_home as display_kairos_home
    from kairos_constants import get_kairos_home as get_kairos_home
except (ModuleNotFoundError, ImportError):

    def get_kairos_home() -> Path:
        """Return the Kairos home directory (default: ~/.kairos).

        Mirrors ``kairos_constants.get_kairos_home()``."""
        val = os.environ.get("KAIROS_HOME", "").strip()
        return Path(val) if val else Path.home() / ".kairos"

    def display_kairos_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``kairos_constants.display_kairos_home()``."""
        home = get_kairos_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
