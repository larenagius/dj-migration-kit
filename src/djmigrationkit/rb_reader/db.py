"""Read the Rekordbox 6 master.db into canonical models via pyrekordbox.

Strictly read-only. We open the database, project DjmdContent + DjmdCue +
beatgrid (from ANLZ files) + DjmdPlaylist into the source-agnostic models the rest
of the pipeline consumes.

Field facts discovered against a real library (see scripts/spike_rb*.py):
  * DjmdContent.FolderPath holds the **full** file path (not just the folder).
  * DjmdContent.BPM is stored as BPM * 100 (e.g. 7268 -> 72.68).
  * DjmdContent.FileSize is in **bytes**.
  * DjmdCue.Kind: 0 == memory cue; 1..8 == hot cue slots A..H (index = Kind-1).
  * DjmdCue.InMsec is the cue position in ms; OutMsec > 0 marks a loop.
  * DjmdCue.ColorTableIndex indexes rekordbox's cue color palette (see rb_colors).
  * beatgrid = anlz.get("beat_grid") -> (beat_numbers, bpms, times_seconds) arrays.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..mapping.rb_colors import color_index_to_rgb
from ..models import BeatGrid, BeatMarker, Cue, CueKind, RbPlaylist, RbTrack


def _to_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class RekordboxDbReader:
    """Read-only reader over a Rekordbox 6 database."""

    def __init__(self, db=None, key: Optional[str] = None):
        """Open the database.

        Args:
            db: an already-open pyrekordbox Rekordbox6Database (for tests); if
                None, one is constructed (auto-detecting install + key).
            key: optional SQLCipher key override.
        """
        if db is not None:
            self._db = db
        else:
            from pyrekordbox import Rekordbox6Database

            self._db = Rekordbox6Database(key=key) if key else Rekordbox6Database()

    # ---- cues ------------------------------------------------------------- #
    def _cue_from_row(self, row) -> Optional[Cue]:
        out = _to_int(getattr(row, "OutMsec", -1))
        in_ms = getattr(row, "InMsec", None)
        if in_ms is None:
            return None
        kind = _to_int(getattr(row, "Kind", 0)) or 0
        color = color_index_to_rgb(_to_int(getattr(row, "ColorTableIndex", None)))
        comment = getattr(row, "Comment", "") or ""

        if out is not None and out > 0:
            # A saved loop. Parsed for completeness; not written in v1.
            return Cue(
                position_ms=float(in_ms),
                kind=CueKind.LOOP,
                length_ms=float(out) - float(in_ms),
                name=comment,
                color_rgb=color,
            )
        if kind == 0:
            return Cue(position_ms=float(in_ms), kind=CueKind.MEMORY, name=comment, color_rgb=color)
        # hot cue: Kind 1..8 -> index 0..7
        return Cue(
            position_ms=float(in_ms),
            kind=CueKind.HOT,
            hotcue_index=kind - 1,
            name=comment,
            color_rgb=color,
        )

    def _cues_for(self, content_id: str) -> list[Cue]:
        rows = self._db.get_cue(ContentID=content_id).all()
        cues = [self._cue_from_row(r) for r in rows]
        return [c for c in cues if c is not None]

    # ---- beatgrid --------------------------------------------------------- #
    def _beatgrid_for(self, content) -> Optional[BeatGrid]:
        try:
            files = self._db.read_anlz_files(content.ID)
        except Exception:
            return None
        for _path, anlz in (files or {}).items():
            try:
                bg = anlz.get("beat_grid")
            except Exception:
                bg = None
            if bg is None:
                continue
            try:
                beat_numbers, bpms, times_s = bg[0], bg[1], bg[2]
            except (TypeError, IndexError):
                continue
            markers = tuple(
                BeatMarker(
                    position_ms=float(times_s[i]) * 1000.0,
                    bpm=float(bpms[i]),
                    beat_number=int(beat_numbers[i]),
                )
                for i in range(len(times_s))
            )
            if markers:
                return BeatGrid(markers=markers)
        return None

    # ---- tracks ----------------------------------------------------------- #
    def _track_from_content(self, content, with_grid: bool = True) -> RbTrack:
        bpm_raw = _to_int(getattr(content, "BPM", None))
        bpm = bpm_raw / 100.0 if bpm_raw else None
        length_s = _to_int(getattr(content, "Length", None))
        duration_ms = float(length_s) * 1000.0 if length_s else None
        try:
            artist = content.Artist.Name if content.Artist else ""
        except Exception:
            artist = ""

        track = RbTrack(
            rb_id=str(content.ID),
            file_path=getattr(content, "FolderPath", "") or "",
            title=getattr(content, "Title", "") or "",
            artist=artist or "",
            bpm=bpm,
            duration_ms=duration_ms,
            file_size=_to_int(getattr(content, "FileSize", None)),
            cues=self._cues_for(content.ID),
            beatgrid=self._beatgrid_for(content) if with_grid else None,
        )
        return track

    def iter_tracks(self, with_grid: bool = True) -> Iterable[RbTrack]:
        for content in self._db.get_content():
            yield self._track_from_content(content, with_grid=with_grid)

    def tracks(self, with_grid: bool = True, limit: Optional[int] = None) -> list[RbTrack]:
        out: list[RbTrack] = []
        for i, content in enumerate(self._db.get_content()):
            if limit is not None and i >= limit:
                break
            out.append(self._track_from_content(content, with_grid=with_grid))
        return out

    # ---- playlists -------------------------------------------------------- #
    def playlists(self) -> list[RbPlaylist]:
        """Return top-level playlist nodes with nested folders/playlists.

        Rekordbox stores a flat DjmdPlaylist table with ParentID ('root' at top)
        and an Attribute flag for folders. We rebuild the tree here.
        """
        rows = self._db.get_playlist().all()
        nodes: dict[str, RbPlaylist] = {}
        children_of: dict[str, list[str]] = {}
        order: dict[str, int] = {}

        for r in rows:
            pid = str(r.ID)
            is_folder = bool(getattr(r, "is_folder", False)) or _to_int(getattr(r, "Attribute", 0)) == 1
            node = RbPlaylist(name=getattr(r, "Name", "") or "", is_folder=is_folder)
            if not is_folder:
                node.track_ids = self._playlist_track_ids(r)
            nodes[pid] = node
            parent = str(getattr(r, "ParentID", "root") or "root")
            children_of.setdefault(parent, []).append(pid)
            order[pid] = _to_int(getattr(r, "Seq", 0)) or 0

        def attach(pid: str) -> RbPlaylist:
            node = nodes[pid]
            child_ids = sorted(children_of.get(pid, []), key=lambda c: order.get(c, 0))
            node.children = [attach(c) for c in child_ids]
            return node

        roots = sorted(children_of.get("root", []), key=lambda c: order.get(c, 0))
        return [attach(pid) for pid in roots]

    def _playlist_track_ids(self, playlist_row) -> list[str]:
        try:
            songs = playlist_row.Songs
        except Exception:
            return []
        ids: list[str] = []
        for s in songs:
            try:
                ids.append(str(s.Content.ID))
            except Exception:
                cid = getattr(s, "ContentID", None)
                if cid is not None:
                    ids.append(str(cid))
        return ids
