"""The single safety chokepoint for writing Traktor data.

HARD RULE (see project memory / plan): we never write to a live ``collection.nml``.
Every write goes through :func:`resolve_output_path` + :func:`atomic_write_bytes`,
which together guarantee:

1. The output file name is derived as ``collection-merge.nml`` next to the source,
   never the source file itself. ``assert_not_live`` refuses any path whose name is
   exactly ``collection.nml``.
2. If a merge file already exists (a previous run the user may not have applied
   yet), we never clobber it -- we fall back to a timestamped variant.
3. Writes are atomic: serialize to ``<name>.tmp`` then ``os.replace`` so a crash
   mid-write can't leave a half-written file.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

LIVE_NAME = "collection.nml"
MERGE_NAME = "collection-merge.nml"


class LiveFileWriteError(RuntimeError):
    """Raised if anything attempts to write the live collection.nml."""


def assert_not_live(path: os.PathLike | str) -> None:
    """Guard: raise if ``path`` points at a live collection.nml."""
    p = Path(path)
    if p.name.casefold() == LIVE_NAME.casefold():
        raise LiveFileWriteError(
            f"Refusing to write to the live collection file: {p}. "
            f"Output must go to '{MERGE_NAME}'."
        )


def resolve_output_path(source_nml: os.PathLike | str) -> Path:
    """Given the live collection.nml path, return where the merge should be written.

    Returns ``<dir>/collection-merge.nml``. If that already exists, returns
    ``<dir>/collection-merge-YYYYMMDD-HHMMSS.nml`` so prior, unapplied merges are
    never overwritten.
    """
    src = Path(source_nml)
    out = src.with_name(MERGE_NAME)
    if out.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = src.with_name(f"collection-merge-{stamp}.nml")
        # In the unlikely event the timestamped name also exists, disambiguate.
        i = 1
        while out.exists():
            out = src.with_name(f"collection-merge-{stamp}-{i}.nml")
            i += 1
    return out


def atomic_write_bytes(path: os.PathLike | str, data: bytes) -> Path:
    """Atomically write ``data`` to ``path`` via a temp file + os.replace.

    Refuses to write the live collection file.
    """
    target = Path(path)
    assert_not_live(target)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    return target


def atomic_write_text(path: os.PathLike | str, text: str, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, text.encode(encoding))
