"""Mixxx -> rekordbox.xml export (for Pioneer CDJ USBs via Rekordbox).

Workflow: run this (on Linux or anywhere) -> open the XML in Rekordbox on a
Mac/Windows machine -> import playlists -> Export to USB as usual.

Cue mapping (Mixxx -> Rekordbox):

* hot cue on pad 1-8        -> hot cue A-H (same slot, colour, label)
* hot cue on pad 9-36       -> memory cue (CDJs only have 8 hot cues; nothing lost)
* saved loop on pad 1-8     -> hot loop A-H
* other saved loops         -> memory loop
* main cue                  -> memory cue (so the CDJ shows it on the waveform)
* intro/outro/analysis cues -> skipped (Mixxx-only concepts)

Beatgrids are deliberately *not* exported: Mixxx usually derives its grid from
the BPM tag with the first beat at 0 s, which would be off-phase in Rekordbox.
Rekordbox analyses the tracks on import instead. Cue points are absolute times,
so they don't depend on the grid.

Playlists: Mixxx has no folders, so names like "Gigs - Played / 18.01" (how the
Traktor->Mixxx migrator flattened Traktor folders) are rebuilt into real folders.
Crates go under a "Mixxx Crates" folder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..mapping.path_remap import PathMapper
from ..rekordbox_xml.writer import (
    MARK_CUE,
    MARK_LOOP,
    MAX_HOT_CUES,
    PositionMark,
    XmlNode,
    XmlTrack,
    write_xml,
)
from .library import CUE_HOT, CUE_LOOP, CUE_MAIN, LibraryTrack, MixxxLibrary, read_library

FOLDER_SEP = " / "
CRATES_FOLDER = "Mixxx Crates"

_KIND = {
    "mp3": "MP3 File", "m4a": "M4A File", "aac": "M4A File", "mp4": "M4A File",
    "flac": "FLAC File", "wav": "WAV File", "aif": "AIFF File", "aiff": "AIFF File",
    "ogg": "OGG File", "opus": "OPUS File",
}


@dataclass
class ExportStats:
    tracks: int = 0
    tracks_with_cues: int = 0
    hot_cues: int = 0
    memory_cues: int = 0
    loops: int = 0
    hot_cues_demoted: int = 0  # pads 9-36 -> memory cues
    playlists: int = 0
    playlist_entries: int = 0
    folders: int = 0
    crates: int = 0
    paths_unmapped: int = 0
    cues_without_samplerate: int = 0


@dataclass
class ExportPlan:
    tracks: list[XmlTrack]
    playlists: list[XmlNode]
    stats: ExportStats
    unmapped_examples: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# cues
# --------------------------------------------------------------------------- #
def cues_to_marks(track: LibraryTrack, hot_as_memory: bool = False,
                  stats: Optional[ExportStats] = None) -> list[PositionMark]:
    stats = stats or ExportStats()
    marks: list[PositionMark] = []
    memory_positions: set[int] = set()

    def add_memory(start: float, name: str, rgb, end: Optional[float] = None,
                   kind: int = MARK_CUE) -> None:
        key = round(start * 100)  # de-duplicate within 10 ms
        if key in memory_positions:
            return
        memory_positions.add(key)
        marks.append(PositionMark(start_s=start, num=-1, type=kind, end_s=end,
                                  name=name, rgb=rgb))
        if kind == MARK_LOOP:
            stats.loops += 1
        else:
            stats.memory_cues += 1

    for c in track.cues:
        is_loop = c.type == CUE_LOOP and c.length_s > 0
        kind = MARK_LOOP if is_loop else MARK_CUE
        end = c.position_s + c.length_s if is_loop else None
        on_pad = c.type in (CUE_HOT, CUE_LOOP) and 0 <= c.hotcue < MAX_HOT_CUES

        if on_pad:
            marks.append(PositionMark(start_s=c.position_s, num=c.hotcue, type=kind,
                                      end_s=end, name=c.label, rgb=c.color_rgb))
            if is_loop:
                stats.loops += 1
            else:
                stats.hot_cues += 1
            if hot_as_memory and not is_loop:
                add_memory(c.position_s, c.label, c.color_rgb)
        elif c.type in (CUE_HOT, CUE_LOOP):
            if c.hotcue >= MAX_HOT_CUES:
                stats.hot_cues_demoted += 1
            add_memory(c.position_s, c.label, c.color_rgb, end, kind)
        elif c.type == CUE_MAIN:
            add_memory(c.position_s, c.label, None)

    marks.sort(key=lambda m: (m.num < 0, m.num, m.start_s))
    return marks


# --------------------------------------------------------------------------- #
# track attributes
# --------------------------------------------------------------------------- #
def track_attrs(t: LibraryTrack) -> dict[str, str]:
    a: dict[str, str] = {
        "Name": t.title or Path(t.file_path.replace("\\", "/")).stem,
        "Artist": t.artist, "Composer": t.composer, "Album": t.album,
        "Grouping": t.grouping, "Genre": t.genre, "Comments": t.comment,
        "Tonality": t.key,
    }
    ext = (t.file_type or Path(t.file_path).suffix.lstrip(".")).casefold()
    if ext in _KIND:
        a["Kind"] = _KIND[ext]
    if t.file_size:
        a["Size"] = str(t.file_size)
    if t.duration_s:
        a["TotalTime"] = str(int(round(t.duration_s)))
    if t.year and t.year[:4].isdigit():
        a["Year"] = t.year[:4]
    num = (t.track_number or "").split("/")[0].strip()
    if num.isdigit():
        a["TrackNumber"] = num
    if t.bpm:
        a["AverageBpm"] = f"{t.bpm:.2f}"
    if t.date_added and len(t.date_added) >= 10:
        a["DateAdded"] = t.date_added[:10]
    if t.bitrate:
        a["BitRate"] = str(t.bitrate)
    if t.samplerate:
        a["SampleRate"] = str(t.samplerate)
    if t.play_count:
        a["PlayCount"] = str(t.play_count)
    if t.rating:
        a["Rating"] = str(min(max(t.rating, 0), 5) * 51)  # 0..5 stars -> 0..255
    return a


# --------------------------------------------------------------------------- #
# playlists
# --------------------------------------------------------------------------- #
def build_playlist_tree(lib: MixxxLibrary, folders: bool = True,
                        stats: Optional[ExportStats] = None) -> list[XmlNode]:
    stats = stats or ExportStats()
    top: list[XmlNode] = []

    def folder_in(children: list[XmlNode], name: str) -> XmlNode:
        for n in children:
            if n.is_folder and n.name == name:
                return n
        f = XmlNode(name=name, is_folder=True)
        children.append(f)
        stats.folders += 1
        return f

    for pl in lib.playlists:
        parts = [p.strip() for p in pl.name.split(FOLDER_SEP)] if folders else [pl.name]
        parts = [p for p in parts if p] or [pl.name or "Untitled"]
        level = top
        for folder_name in parts[:-1]:
            level = folder_in(level, folder_name).children
        level.append(XmlNode(name=parts[-1], track_ids=list(pl.track_ids)))
        stats.playlists += 1
        stats.playlist_entries += len(pl.track_ids)

    crates = [c for c in lib.crates if c.track_ids]
    if crates:
        cf = folder_in(top, CRATES_FOLDER)
        for c in crates:
            cf.children.append(XmlNode(name=c.name, track_ids=list(c.track_ids)))
            stats.crates += 1
            stats.playlist_entries += len(c.track_ids)
    return top


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def build_export(mixxx_db: str | Path, mapper: Optional[PathMapper] = None,
                 folders: bool = True, hot_as_memory: bool = False,
                 include_playlists: bool = True) -> ExportPlan:
    mapper = mapper or PathMapper()
    lib = read_library(mixxx_db)
    stats = ExportStats(cues_without_samplerate=lib.cues_without_samplerate)
    unmapped: list[str] = []

    tracks: list[XmlTrack] = []
    for t in lib.tracks:
        path, matched = mapper.map(t.file_path)
        if mapper.rules and not matched:
            stats.paths_unmapped += 1
            if len(unmapped) < 5:
                unmapped.append(t.file_path)
        marks = cues_to_marks(t, hot_as_memory=hot_as_memory, stats=stats)
        if marks:
            stats.tracks_with_cues += 1
        tracks.append(XmlTrack(track_id=t.mixxx_id, path=path,
                               attrs=track_attrs(t), marks=marks))
    stats.tracks = len(tracks)

    playlists = build_playlist_tree(lib, folders=folders, stats=stats) if include_playlists else []
    return ExportPlan(tracks=tracks, playlists=playlists, stats=stats,
                      unmapped_examples=unmapped)


def write_export(plan: ExportPlan, output_path: Path) -> Path:
    return write_xml(output_path, plan.tracks, plan.playlists)
