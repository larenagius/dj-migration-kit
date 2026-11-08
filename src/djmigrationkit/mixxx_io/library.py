"""Read a full Mixxx library (tracks + cues + playlists + crates) for export.

Unlike :class:`~djmigrationkit.mixxx_io.db.MixxxDatabase` (which only needs what
the Traktor->Mixxx matcher uses), this reader pulls everything an export to
another DJ app needs. The database is opened **read-only** (``mode=ro``), so the
live ``mixxxdb.sqlite`` can never be modified. Close Mixxx first so its latest
edits are saved to disk.

Mixxx facts this relies on (checked against a real Mixxx 2.4 library):

* ``cues.position`` / ``cues.length`` are in **stereo samples**
  (seconds * samplerate * 2), the same unit the Traktor->Mixxx writer uses.
* ``cues.type``: 1 = hot cue, 2 = main cue, 4 = saved loop, 6/7 = intro/outro,
  8 = analysis "N60dB" marker. ``hotcue`` is the 0-based pad slot (-1 = none).
* ``cues.color`` is ``0xAARRGGBB``.
* ``Playlists.hidden``: 0 = normal playlist, 1 = Auto DJ queue, 2 = history.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Mixxx cue types
CUE_HOT = 1
CUE_MAIN = 2
CUE_LOOP = 4

PLAYLIST_NORMAL = 0


@dataclass
class MixxxCue:
    """A Mixxx cue, already converted to seconds."""

    type: int
    position_s: float
    length_s: float = 0.0
    hotcue: int = -1  # 0-based pad slot, -1 = not on a pad
    label: str = ""
    color_rgb: Optional[tuple[int, int, int]] = None


@dataclass
class LibraryTrack:
    """A Mixxx track with the metadata an export needs."""

    mixxx_id: int
    file_path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    composer: str = ""
    grouping: str = ""
    comment: str = ""
    year: str = ""
    track_number: str = ""
    key: str = ""
    bpm: Optional[float] = None
    duration_s: Optional[float] = None
    bitrate: Optional[int] = None
    samplerate: Optional[int] = None
    file_size: Optional[int] = None
    file_type: str = ""
    rating: int = 0  # Mixxx 0..5 stars
    play_count: int = 0
    date_added: str = ""
    cues: list[MixxxCue] = field(default_factory=list)


@dataclass
class LibraryPlaylist:
    """A Mixxx playlist or crate (flat; Mixxx has no folders)."""

    name: str
    track_ids: list[int] = field(default_factory=list)
    is_crate: bool = False


@dataclass
class MixxxLibrary:
    tracks: list[LibraryTrack]
    playlists: list[LibraryPlaylist]
    crates: list[LibraryPlaylist]
    cues_without_samplerate: int = 0  # cues we couldn't place (track never analyzed)


def argb_to_rgb(value) -> Optional[tuple[int, int, int]]:
    if value is None:
        return None
    v = int(value) & 0xFFFFFF
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def _columns(cur, table: str) -> set[str]:
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def _has_table(cur, table: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


# library column -> LibraryTrack attribute (only columns that exist are selected,
# so older/newer Mixxx schemas both work)
_TRACK_COLS = {
    "title": "title", "artist": "artist", "album": "album", "genre": "genre",
    "composer": "composer", "grouping": "grouping", "comment": "comment",
    "year": "year", "tracknumber": "track_number", "key": "key", "bpm": "bpm",
    "duration": "duration_s", "bitrate": "bitrate", "samplerate": "samplerate",
    "filetype": "file_type", "rating": "rating", "timesplayed": "play_count",
    "datetime_added": "date_added",
}


def read_library(db_path: str | Path) -> MixxxLibrary:
    """Read tracks, cues, playlists and crates from a Mixxx DB (read-only)."""
    path = Path(db_path)
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        lib_cols = _columns(cur, "library")
        tl_cols = _columns(cur, "track_locations")
        sel = [c for c in _TRACK_COLS if c in lib_cols]
        size_expr = "tl.filesize" if "filesize" in tl_cols else "NULL"
        where = ["1=1"]
        if "mixxx_deleted" in lib_cols:
            where.append("COALESCE(l.mixxx_deleted, 0) = 0")
        if "fs_deleted" in tl_cols:
            where.append("COALESCE(tl.fs_deleted, 0) = 0")
        rows = cur.execute(
            f"SELECT l.id, tl.location, {size_expr}"
            + "".join(f', l."{c}"' for c in sel)
            + " FROM library l JOIN track_locations tl ON l.location = tl.id"
            + " WHERE " + " AND ".join(where)
            + " ORDER BY l.id"
        ).fetchall()

        tracks: dict[int, LibraryTrack] = {}
        for row in rows:
            tid, location, size = row[0], row[1], row[2]
            t = LibraryTrack(mixxx_id=int(tid), file_path=location or "",
                             file_size=int(size) if size else None)
            for col, val in zip(sel, row[3:]):
                if val is None:
                    continue
                attr = _TRACK_COLS[col]
                if attr in ("bpm", "duration_s"):
                    val = float(val) or None
                elif attr in ("bitrate", "samplerate"):
                    val = int(val) or None
                elif attr in ("rating", "play_count"):
                    val = int(val)
                else:
                    val = str(val)
                setattr(t, attr, val)
            tracks[t.mixxx_id] = t

        skipped = 0
        if _has_table(cur, "cues"):
            for track_id, ctype, pos, length, hotcue, label, color in cur.execute(
                "SELECT track_id, type, position, length, hotcue, label, color "
                "FROM cues WHERE type IN (?,?,?) ORDER BY track_id, hotcue, position",
                (CUE_HOT, CUE_MAIN, CUE_LOOP),
            ):
                t = tracks.get(track_id)
                if t is None or pos is None or pos < 0:
                    continue
                if not t.samplerate:
                    skipped += 1
                    continue
                per_s = t.samplerate * 2.0  # stereo samples per second
                t.cues.append(MixxxCue(
                    type=int(ctype),
                    position_s=pos / per_s,
                    length_s=(length or 0) / per_s,
                    hotcue=int(hotcue) if hotcue is not None else -1,
                    label=label or "",
                    color_rgb=argb_to_rgb(color),
                ))

        playlists: list[LibraryPlaylist] = []
        if _has_table(cur, "Playlists"):
            pl_rows = cur.execute(
                "SELECT id, name FROM Playlists WHERE hidden = ? ORDER BY position, id",
                (PLAYLIST_NORMAL,),
            ).fetchall()
            for pid, name in pl_rows:
                ids = [r[0] for r in cur.execute(
                    "SELECT track_id FROM PlaylistTracks WHERE playlist_id = ? "
                    "ORDER BY position, id", (pid,))]
                playlists.append(LibraryPlaylist(
                    name=name or "", track_ids=[i for i in ids if i in tracks]))

        crates: list[LibraryPlaylist] = []
        if _has_table(cur, "crates") and _has_table(cur, "crate_tracks"):
            for cid, name in cur.execute("SELECT id, name FROM crates ORDER BY name").fetchall():
                ids = [r[0] for r in cur.execute(
                    "SELECT track_id FROM crate_tracks WHERE crate_id = ? ORDER BY track_id",
                    (cid,))]
                crates.append(LibraryPlaylist(
                    name=name or "", track_ids=[i for i in ids if i in tracks],
                    is_crate=True))
    finally:
        con.close()

    return MixxxLibrary(tracks=list(tracks.values()), playlists=playlists,
                        crates=crates, cues_without_samplerate=skipped)
