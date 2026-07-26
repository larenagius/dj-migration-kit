"""Mixxx database I/O (read tracks, write cues + playlists to a safe copy).

Mixxx stores its library in a plain SQLite file, ``mixxxdb.sqlite``. Safety rule
(same as the rest of the project): we never modify the live database — all writes
go to a sibling ``mixxxdb-merged.sqlite`` the user swaps in with Mixxx closed.
"""

from .db import MixxxDatabase, MixxxMergeWriter, find_mixxx_db

__all__ = ["MixxxDatabase", "MixxxMergeWriter", "find_mixxx_db", "build_plan", "migrate"]


def __getattr__(name):  # lazy to avoid importing migrate (and its deps) eagerly
    if name in ("build_plan", "migrate"):
        from . import migrate as _m
        return getattr(_m, name)
    raise AttributeError(name)
