"""Fallback reader: parse an exported rekordbox.xml into canonical models.

Used when master.db can't be opened (e.g. a future Rekordbox update rotates the
SQLCipher key before pyrekordbox catches up). The user exports
``File -> Export Collection in xml format`` and points the tool at the file.

rekordbox.xml is a stable, publicly documented format, so we parse it directly
with lxml. Relevant elements:

  <TRACK TrackID Name Artist AverageBpm TotalTime Size Location="file://localhost/...">
    <TEMPO Inizio="<sec>" Bpm Battito="<beat 1-4>"/>
    <POSITION_MARK Name Type Start="<sec>" Num="<-1 mem | 0..7 hot>"
                   Red Green Blue End="<sec for loop>"/>
  </TRACK>
  <PLAYLISTS><NODE Type="0" Name="ROOT"> ... folders (Type=0) / playlists (Type=1)
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from ..models import BeatGrid, BeatMarker, Cue, CueKind, RbPlaylist, RbTrack


def _rb_url_to_path(loc: str) -> str:
    if not loc:
        return ""
    p = unquote(loc)
    for prefix in ("file://localhost/", "file:///", "file://"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    return p


def _f(el, attr) -> float | None:
    v = el.get(attr)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


class RekordboxXmlReader:
    """Read-only reader over an exported rekordbox.xml."""

    def __init__(self, xml_path: str | Path):
        self.xml_path = Path(xml_path)
        parser = etree.XMLParser(huge_tree=True)
        self._tree = etree.parse(str(self.xml_path), parser)
        self._root = self._tree.getroot()

    # ---- tracks ----------------------------------------------------------- #
    def _cue_from_mark(self, mark) -> Cue | None:
        start = _f(mark, "Start")
        if start is None:
            return None
        num = int(mark.get("Num", "-1"))
        end = _f(mark, "End")
        r, g, b = mark.get("Red"), mark.get("Green"), mark.get("Blue")
        color = (int(r), int(g), int(b)) if (r and g and b) else None
        name = mark.get("Name", "") or ""
        if end is not None and end > start:
            return Cue(position_ms=start * 1000, kind=CueKind.LOOP,
                       length_ms=(end - start) * 1000, name=name, color_rgb=color)
        if num is None or num < 0:
            return Cue(position_ms=start * 1000, kind=CueKind.MEMORY, name=name, color_rgb=color)
        return Cue(position_ms=start * 1000, kind=CueKind.HOT, hotcue_index=num,
                   name=name, color_rgb=color)

    def _beatgrid_from_track(self, track) -> BeatGrid | None:
        markers = []
        for tempo in track.findall("TEMPO"):
            pos = _f(tempo, "Inizio")
            bpm = _f(tempo, "Bpm")
            if pos is None or bpm is None:
                continue
            markers.append(BeatMarker(position_ms=pos * 1000, bpm=bpm,
                                      beat_number=int(tempo.get("Battito", "1"))))
        return BeatGrid(markers=tuple(markers)) if markers else None

    def _track_from_el(self, track) -> RbTrack:
        cues = [c for c in (self._cue_from_mark(m) for m in track.findall("POSITION_MARK")) if c]
        total = _f(track, "TotalTime")
        size = track.get("Size")
        return RbTrack(
            rb_id=track.get("TrackID", ""),
            file_path=_rb_url_to_path(track.get("Location", "")),
            title=track.get("Name", "") or "",
            artist=track.get("Artist", "") or "",
            bpm=_f(track, "AverageBpm"),
            duration_ms=total * 1000 if total else None,
            file_size=int(size) if size and size.isdigit() else None,
            cues=cues,
            beatgrid=self._beatgrid_from_track(track),
        )

    def _track_elements(self):
        col = self._root.find("COLLECTION")
        return col.findall("TRACK") if col is not None else []

    def tracks(self, with_grid: bool = True, limit: int | None = None) -> list[RbTrack]:
        els = self._track_elements()
        if limit is not None:
            els = els[:limit]
        return [self._track_from_el(t) for t in els]

    def iter_tracks(self, with_grid: bool = True):
        for t in self._track_elements():
            yield self._track_from_el(t)

    # ---- playlists -------------------------------------------------------- #
    def playlists(self) -> list[RbPlaylist]:
        pl_root = self._root.find("PLAYLISTS")
        if pl_root is None:
            return []
        root_node = pl_root.find("NODE")
        if root_node is None:
            return []
        return [self._node(n) for n in root_node.findall("NODE")]

    def _node(self, node) -> RbPlaylist:
        is_folder = node.get("Type") == "0"
        pl = RbPlaylist(name=node.get("Name", "") or "", is_folder=is_folder)
        if is_folder:
            pl.children = [self._node(n) for n in node.findall("NODE")]
        else:
            pl.track_ids = [t.get("Key", "") for t in node.findall("TRACK") if t.get("Key")]
        return pl
