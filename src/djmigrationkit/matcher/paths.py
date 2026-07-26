r"""Path normalization for matching tracks across Rekordbox and Traktor.

The two apps store file locations very differently:

* **Rekordbox** stores a normal-ish absolute path. From master.db it is split into
  ``FolderPath`` (a file URL / posix-ish path) plus the file name; pyrekordbox
  gives you the joined path. It is URL-decoded already in our reader.

* **Traktor** stores location as three attributes on a ``LOCATION`` element::

      <LOCATION DIR="/:Users/:dj/:Music/:House/:" FILE="track.mp3" VOLUME="C:"/>

  Traktor uses ``/:`` as its path separator and keeps the volume (drive) separate.
  So a Windows file ``C:\Users\dj\Music\House\track.mp3`` becomes
  VOLUME=``C:``, DIR=``/:Users/:dj/:Music/:House/:``, FILE=``track.mp3``.

This module turns both into a single canonical key so they can be compared.
"""

from __future__ import annotations

import os
import re
from urllib.parse import unquote


def traktor_location_to_path(volume: str, dir_attr: str, file_attr: str) -> str:
    """Reassemble a Traktor LOCATION (VOLUME/DIR/FILE) into an OS path string.

    >>> traktor_location_to_path("C:", "/:Users/:dj/:Music/:", "track.mp3")
    'C:/Users/dj/Music/track.mp3'
    """
    # Traktor's separator is "/:". The DIR starts and ends with it.
    parts = [p for p in dir_attr.split("/:") if p != ""]
    dir_path = "/".join(parts)
    volume = volume.strip()
    # On Windows the volume is a drive like "C:"; on macOS it's a volume name.
    if volume and not volume.endswith(":"):
        # macOS volume label -> we can't reconstruct a real mount path reliably;
        # fall back to just dir+file, normalization below still gives a usable key.
        full = f"{dir_path}/{file_attr}"
    else:
        full = f"{volume}/{dir_path}/{file_attr}" if dir_path else f"{volume}/{file_attr}"
    return full


def normalize(path: str) -> str:
    """Reduce any path string to a canonical comparison key.

    - URL-decodes (Rekordbox file URLs may contain %20 etc.)
    - strips a leading ``file://`` scheme
    - converts backslashes to forward slashes
    - collapses duplicate slashes
    - case-folds (Windows + macOS default filesystems are case-insensitive)

    The result is NOT a valid filesystem path; it is only a stable key for
    equality comparison between the two libraries.
    """
    if not path:
        return ""
    p = unquote(path)
    if p.startswith("file://"):
        p = p[len("file://") :]
        # file:///C:/... -> strip the leading slash before the drive letter
        p = re.sub(r"^/([A-Za-z]:)", r"\1", p)
    p = p.replace("\\", "/")
    p = re.sub(r"/{2,}", "/", p)
    return p.casefold().rstrip("/")


def basename_key(path: str) -> str:
    """Just the file name, normalized -- the secondary match key."""
    p = normalize(path)
    return os.path.basename(p)
