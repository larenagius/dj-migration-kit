"""Match Rekordbox tracks to Traktor entries.

A correct match is the foundation of the whole tool: if we attach Rekordbox cues
to the wrong Traktor track we silently corrupt the user's library. So the cascade
goes from most-certain to least-certain, and anything below "exact" is surfaced in
the GUI for human confirmation rather than applied blindly.

Cascade:
    1. exact     -- normalized absolute path is identical
    2. filename  -- same file name AND same file size (handles libraries that
                    live on a different drive letter / moved folders)
    3. fuzzy     -- same title + artist + duration within a tolerance; never
                    auto-applied, only proposed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import RbTrack, TraktorEntry
from . import paths


DURATION_TOLERANCE_MS = 2000.0
# RB stores size in bytes, Traktor in KB (we normalize Traktor to KB*1024). The
# rounding means the byte counts differ by < 1 KB for the same file.
SIZE_TOLERANCE_BYTES = 2048


@dataclass
class Match:
    rb_track: RbTrack
    traktor_entry: Optional[TraktorEntry]
    confidence: str  # "exact" | "filename" | "fuzzy" | "none"


def _norm_name(s: str) -> str:
    return " ".join((s or "").casefold().split())


class TrackMatcher:
    """Indexes Traktor entries once, then matches Rekordbox tracks against them.

    In practice the Rekordbox and Traktor libraries often live on different drives
    (e.g. RB on a Google Drive G:, Traktor on a network Y:), so exact-path matches
    are rare and **basename** is the workhorse key. When several Traktor entries
    share a basename, file size (KB-tolerant) breaks the tie.
    """

    def __init__(self, traktor_entries: list[TraktorEntry]):
        self._entries = traktor_entries
        self._by_path: dict[str, TraktorEntry] = {}
        self._by_name: dict[str, list[TraktorEntry]] = {}
        self._by_meta: dict[tuple[str, str], list[TraktorEntry]] = {}

        for e in traktor_entries:
            key = paths.normalize(e.file_path)
            if key:
                self._by_path.setdefault(key, e)
            self._by_name.setdefault(paths.basename_key(e.file_path), []).append(e)
            meta = (_norm_name(e.title), _norm_name(e.artist))
            if any(meta):
                self._by_meta.setdefault(meta, []).append(e)

    def match(self, rb: RbTrack) -> Match:
        # 1. exact path
        hit = self._by_path.get(paths.normalize(rb.file_path))
        if hit is not None:
            return Match(rb, hit, "exact")

        # 2. basename, with size as a tiebreak / confirmation
        candidates = self._by_name.get(paths.basename_key(rb.file_path), [])
        if candidates:
            m = self._match_by_size(rb, candidates)
            if m is not None:
                return m

        # 3. fuzzy: title + artist + duration
        meta_candidates = self._by_meta.get((_norm_name(rb.title), _norm_name(rb.artist)), [])
        for e in meta_candidates:
            if rb.duration_ms is None or e.duration_ms is None:
                return Match(rb, e, "fuzzy")
            if abs(rb.duration_ms - e.duration_ms) <= DURATION_TOLERANCE_MS:
                return Match(rb, e, "fuzzy")

        return Match(rb, None, "none")

    def _match_by_size(self, rb: RbTrack, candidates: list[TraktorEntry]) -> Optional[Match]:
        """Resolve same-basename candidates using file size.

        - Sizes available + within tolerance -> confident "filename" match.
        - Single candidate, sizes missing -> "filename" (basenames are ~unique).
        - Single candidate, sizes present but off -> "fuzzy" (needs confirmation).
        - Multiple candidates, none within tolerance -> no basename match.
        """
        if rb.file_size is not None:
            sized = [e for e in candidates if e.file_size is not None]
            if sized:
                best = min(sized, key=lambda e: abs(e.file_size - rb.file_size))
                if abs(best.file_size - rb.file_size) <= SIZE_TOLERANCE_BYTES:
                    return Match(rb, best, "filename")
                if len(candidates) == 1:
                    return Match(rb, candidates[0], "fuzzy")
                return None
        # No usable size info on the RB side or none of the candidates have size.
        if len(candidates) == 1:
            return Match(rb, candidates[0], "filename")
        return None

    def match_all(self, rb_tracks: list[RbTrack]) -> list[Match]:
        return [self.match(t) for t in rb_tracks]
