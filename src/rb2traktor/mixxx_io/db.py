"""Read the Mixxx SQLite library and write cues/playlists to a safe copy.

The live ``mixxxdb.sqlite`` is only ever opened **read-only**. Writes go to a
sibling ``mixxxdb-merged.sqlite`` (timestamped if one already exists) that the
user swaps in with Mixxx closed. :func:`_assert_not_live` guarantees we never
target the live database.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import MixxxTrack

LIVE_NAME = "mixxxdb.sqlite"
MERGE_NAME = "mixxxdb-merged.sqlite"


def find_mixxx_db() -> Optional[Path]:
    """Best-effort location of the user's Mixxx database (override: MIXXX_DB env)."""
    env = os.environ.get("MIXXX_DB")
    if env and Path(env).exists():
        return Path(env)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Mixxx" / LIVE_NAME,  # Windows
        Path.home() / "Library" / "Application Support" / "Mixxx" / LIVE_NAME,  # macOS
        Path.home() / ".mixxx" / LIVE_NAME,  # Linux
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


class MixxxLiveWriteError(RuntimeError):
    """Raised if anything attempts to write the live mixxxdb.sqlite."""


def _assert_not_live(path: Path) -> None:
    if path.name.casefold() == LIVE_NAME.casefold():
        raise MixxxLiveWriteError(
            f"Refusing to write the live Mixxx database: {path}. Output must be '{MERGE_NAME}'."
        )


class MixxxDatabase:
    """Read-only view of a Mixxx library for matching."""

    def __init__(self, source_path: str | Path):
        self.source_path = Path(source_path)

    def tracks(self) -> list[MixxxTrack]:
        con = sqlite3.connect(f"file:{self.source_path.as_posix()}?mode=ro", uri=True)
        try:
            cur = con.cursor()
            rows = cur.execute(
                """
                SELECT l.id, l.title, l.artist, l.duration, l.samplerate,
                       tl.location, tl.filesize
                FROM library l
                JOIN track_locations tl ON l.location = tl.id
                WHERE COALESCE(l.mixxx_deleted, 0) = 0
                  AND COALESCE(tl.fs_deleted, 0) = 0
                """
            ).fetchall()
        finally:
            con.close()
        out: list[MixxxTrack] = []
        for tid, title, artist, duration, samplerate, location, filesize in rows:
            out.append(
                MixxxTrack(
                    mixxx_id=int(tid),
                    file_path=location or "",
                    title=title or "",
                    artist=artist or "",
                    duration_ms=float(duration) * 1000.0 if duration else None,
                    file_size=int(filesize) if filesize else None,
                    samplerate=int(samplerate) if samplerate else 44100,
                )
            )
        return out


@dataclass
class MixxxWriteResult:
    output_path: Path
    tracks_with_cues: int = 0
    cues_written: int = 0
    playlists_written: int = 0
    playlist_entries_written: int = 0


def resolve_output_path(source: str | Path) -> Path:
    src = Path(source)
    out = src.with_name(MERGE_NAME)
    if out.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = src.with_name(f"mixxxdb-merged-{stamp}.sqlite")
        i = 1
        while out.exists():
            out = src.with_name(f"mixxxdb-merged-{stamp}-{i}.sqlite")
            i += 1
    return out


@dataclass
class CueAssignment:
    track_id: int
    rows: list[dict]  # from mapping.mixxx_cues.build_cue_rows


@dataclass
class PlaylistSpec:
    name: str
    track_ids: list[int] = field(default_factory=list)


class MixxxMergeWriter:
    """Copy the live DB, apply cues + playlists to the copy, and save it."""

    def __init__(self, source_path: str | Path):
        self.source_path = Path(source_path)

    def apply_and_write(
        self,
        cue_assignments: list[CueAssignment],
        playlist_specs: list[PlaylistSpec],
        replace_hotcues: bool = True,
    ) -> MixxxWriteResult:
        out = resolve_output_path(self.source_path)
        _assert_not_live(out)
        # Work on a byte copy of the live DB; the live file is never opened rw.
        shutil.copy2(self.source_path, out)

        result = MixxxWriteResult(output_path=out)
        con = sqlite3.connect(str(out))
        try:
            cur = con.cursor()
            cur.execute("PRAGMA foreign_keys = OFF")

            for asg in cue_assignments:
                if not asg.rows:
                    continue
                if replace_hotcues:
                    # remove existing hot cues for this track so re-runs are clean
                    cur.execute(
                        "DELETE FROM cues WHERE track_id=? AND type=?",
                        (asg.track_id, 1),
                    )
                for r in asg.rows:
                    cur.execute(
                        "INSERT INTO cues (track_id, type, position, length, hotcue, label, color) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (asg.track_id, r["type"], r["position"], r["length"],
                         r["hotcue"], r["label"], r["color"]),
                    )
                    result.cues_written += 1
                result.tracks_with_cues += 1

            self._write_playlists(cur, playlist_specs, result)
            con.commit()
        finally:
            con.close()
        return result

    @staticmethod
    def _write_playlists(cur, playlist_specs: list[PlaylistSpec], result: MixxxWriteResult):
        if not playlist_specs:
            return
        base_pos = (cur.execute("SELECT COALESCE(MAX(position), 0) FROM Playlists").fetchone()[0]) or 0
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        for i, spec in enumerate(playlist_specs, start=1):
            if not spec.track_ids:
                continue
            cur.execute(
                "INSERT INTO Playlists (name, position, hidden, date_created, date_modified, locked) "
                "VALUES (?,?,?,?,?,?)",
                (spec.name, base_pos + i, 0, now, now, 0),
            )
            playlist_id = cur.lastrowid
            for pos, track_id in enumerate(spec.track_ids, start=1):
                cur.execute(
                    "INSERT INTO PlaylistTracks (playlist_id, track_id, position, pl_datetime_added) "
                    "VALUES (?,?,?,?)",
                    (playlist_id, track_id, pos, now),
                )
                result.playlist_entries_written += 1
            result.playlists_written += 1
