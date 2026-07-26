"""Rekordbox readers.

``RekordboxDbReader`` (master.db, primary) and ``RekordboxXmlReader``
(rekordbox.xml, fallback) both yield the same canonical models. Imported lazily so
that importing the package doesn't require pyrekordbox unless a reader is used.
"""

from .db import RekordboxDbReader

__all__ = ["RekordboxDbReader"]

try:  # XML reader is optional / fallback
    from .xml import RekordboxXmlReader  # noqa: F401

    __all__.append("RekordboxXmlReader")
except Exception:  # pragma: no cover
    pass
