"""DJ Migration Kit — sync cues, beatgrids and playlists between DJ libraries.

Supports Traktor, Rekordbox and Mixxx.

Safety contract: this package NEVER writes to a live DJ library. All output goes
to a sibling merge file (``collection-merge.nml``, ``mixxxdb-merged.sqlite``,
``rekordbox-export.xml``) that the user swaps in / imports manually.
"""

__version__ = "0.2.0.dev0"
