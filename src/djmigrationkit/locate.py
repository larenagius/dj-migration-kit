"""Locate DJ library files without hardcoding personal paths.

Pure stdlib (no Qt) so both the GUI and the helper scripts can share it.
Override via environment variables where you don't want auto-detection:

    DJMK_TRAKTOR   full path to a Traktor collection.nml
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def find_traktor_collection(explicit: Optional[str] = None) -> Optional[Path]:
    """Return a Traktor collection.nml path, or None if not found.

    Resolution order:
      1. ``explicit`` argument (e.g. a CLI arg), if given and it exists.
      2. ``DJMK_TRAKTOR`` environment variable, if set and it exists.
      3. Auto-detect the newest ``Traktor */collection.nml`` under the user's
         Documents\\Native Instruments folder.
    """
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p

    env = os.environ.get("DJMK_TRAKTOR")
    if env:
        p = Path(env)
        if p.exists():
            return p

    base = Path.home() / "Documents" / "Native Instruments"
    if base.exists():
        candidates = list(base.glob("Traktor */collection.nml"))
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
    return None
