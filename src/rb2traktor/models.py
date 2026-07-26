"""Canonical, source-agnostic data models.

Everything downstream of the readers (matcher, mappers, sync engine, GUI) speaks
in terms of these dataclasses. The Rekordbox readers (master.db or rekordbox.xml)
and the Traktor reader both normalize their native formats into these types so the
rest of the pipeline never has to care where the data came from.

All cue/grid positions are stored in **milliseconds (float)** as the internal unit.
Rekordbox stores cue positions in ms; Traktor stores them in ms as well (the START
attribute on CUE_V2). Keeping a single unit avoids rounding drift between layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- #
# Cues
# --------------------------------------------------------------------------- #
class CueKind(Enum):
    """What a cue *is*, independent of either app's numbering scheme.

    Rekordbox distinguishes "hot cues" (assigned to pad slots A-H) from
    "memory cues" (unnumbered markers on the waveform). Traktor models both as
    CUE_V2 entries; a hot cue has HOTCUE 0..7, a memory cue has HOTCUE = -1.
    """

    HOT = "hot"
    MEMORY = "memory"
    LOOP = "loop"  # parsed but out of scope for writing in v1


@dataclass(frozen=True)
class Cue:
    """A single cue point, normalized.

    Attributes:
        position_ms: Start position from the track's beginning, in milliseconds.
        kind: HOT, MEMORY, or LOOP.
        hotcue_index: 0..7 for hot cues (A..H), None for memory cues.
        length_ms: Loop length in ms; None for point cues.
        name: User label, may be empty.
        color_rgb: (r, g, b) 0-255, or None if uncolored.
    """

    position_ms: float
    kind: CueKind
    hotcue_index: Optional[int] = None
    length_ms: Optional[float] = None
    name: str = ""
    color_rgb: Optional[tuple[int, int, int]] = None

    def signature(self) -> tuple:
        """Identity used to detect 'the same cue' across apps for conflict diffs.

        Position is rounded to 10ms so sub-frame jitter between apps doesn't make
        otherwise-identical cues look different.
        """
        return (self.kind, self.hotcue_index, round(self.position_ms / 10.0))


# --------------------------------------------------------------------------- #
# Beatgrid
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BeatMarker:
    """One anchor in a (possibly multi-tempo) beatgrid."""

    position_ms: float
    bpm: float
    beat_number: int = 1  # 1 == downbeat


@dataclass(frozen=True)
class BeatGrid:
    """A track's beatgrid as a list of tempo anchors.

    Most tracks have a single constant tempo (one effective region). Tracks with
    tempo changes have multiple distinct BPM regions; ``is_multi_region`` flags
    those so the sync engine can warn that only the dominant region transfers to
    Traktor's single-anchor grid model.
    """

    markers: tuple[BeatMarker, ...] = ()

    @property
    def first_downbeat(self) -> Optional[BeatMarker]:
        for m in self.markers:
            if m.beat_number == 1:
                return m
        return self.markers[0] if self.markers else None

    @property
    def dominant_bpm(self) -> Optional[float]:
        if not self.markers:
            return None
        # The first marker's BPM is Traktor's anchor tempo.
        anchor = self.first_downbeat
        return anchor.bpm if anchor else self.markers[0].bpm

    @property
    def is_multi_region(self) -> bool:
        bpms = {round(m.bpm, 2) for m in self.markers}
        return len(bpms) > 1


# --------------------------------------------------------------------------- #
# Tracks
# --------------------------------------------------------------------------- #
@dataclass
class RbTrack:
    """A Rekordbox track, normalized.

    file_path is the absolute local path to the audio file as Rekordbox stores it
    (already URL-decoded). The matcher uses it as the primary join key against
    Traktor entries.
    """

    rb_id: str
    file_path: str
    title: str = ""
    artist: str = ""
    bpm: Optional[float] = None
    musical_key: str = ""
    duration_ms: Optional[float] = None
    file_size: Optional[int] = None
    cues: list[Cue] = field(default_factory=list)
    beatgrid: Optional[BeatGrid] = None

    @property
    def hot_cues(self) -> list[Cue]:
        return [c for c in self.cues if c.kind is CueKind.HOT]

    @property
    def memory_cues(self) -> list[Cue]:
        return [c for c in self.cues if c.kind is CueKind.MEMORY]


@dataclass
class TraktorEntry:
    """A Traktor collection ENTRY, normalized for matching + diffing.

    ``raw`` holds the underlying parsed object (from traktor-nml-utils or an lxml
    element) so the writer can mutate the real node in place. The other fields are
    a convenience projection used by the matcher and diff view.
    """

    file_path: str
    title: str = ""
    artist: str = ""
    bpm: Optional[float] = None
    duration_ms: Optional[float] = None
    file_size: Optional[int] = None
    cues: list[Cue] = field(default_factory=list)
    has_beatgrid: bool = False
    traktor_key: str = ""  # VOLUME+DIR+FILE, matches playlist PRIMARYKEY KEY
    raw: object = None

    @property
    def hot_cues(self) -> list[Cue]:
        return [c for c in self.cues if c.kind is CueKind.HOT]

    @property
    def memory_cues(self) -> list[Cue]:
        return [c for c in self.cues if c.kind is CueKind.MEMORY]


@dataclass
class TraktorPlaylist:
    """A node in Traktor's PLAYLISTS tree (folder or leaf playlist).

    Leaf playlists carry ``track_keys`` (Traktor VOLUME+DIR+FILE strings, matching
    :attr:`TraktorEntry.traktor_key`); folders carry ``children``.
    """

    name: str
    is_folder: bool = False
    track_keys: list[str] = field(default_factory=list)
    children: list["TraktorPlaylist"] = field(default_factory=list)


@dataclass
class MixxxTrack:
    """A track already in the Mixxx library (target for cue/playlist migration).

    ``mixxx_id`` is ``library.id``; cue positions are computed in stereo samples
    using ``samplerate`` (Mixxx stores cue position = seconds * samplerate * 2).
    """

    mixxx_id: int
    file_path: str
    title: str = ""
    artist: str = ""
    duration_ms: Optional[float] = None
    file_size: Optional[int] = None
    samplerate: int = 44100


# --------------------------------------------------------------------------- #
# Playlists
# --------------------------------------------------------------------------- #
@dataclass
class RbPlaylist:
    """A Rekordbox playlist or folder node.

    Folders have ``is_folder=True`` and may contain ``children``; leaf playlists
    carry ``track_ids`` (Rekordbox content IDs, resolved to tracks later).
    """

    name: str
    is_folder: bool = False
    track_ids: list[str] = field(default_factory=list)
    children: list["RbPlaylist"] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Sync plan
# --------------------------------------------------------------------------- #
class ChangeType(Enum):
    NO_CHANGE = "no_change"
    NEW_CUES = "new_cues"  # Traktor had none / fewer; RB adds cues, no conflict
    CONFLICT = "conflict"  # both sides have cues -> user must choose
    UNMATCHED = "unmatched"  # RB track not found in Traktor


class Resolution(Enum):
    """Per-track decision for how to combine cue sets."""

    RB_WINS = "rb_wins"  # replace Traktor cues with Rekordbox cues
    TRAKTOR_WINS = "traktor_wins"  # keep Traktor cues, ignore Rekordbox
    MERGE = "merge"  # union; RB fills empty hotcue slots, keeps Traktor's


@dataclass
class TrackChange:
    """One row of the sync plan: how a single matched track will change."""

    rb_track: RbTrack
    traktor_entry: Optional[TraktorEntry]
    change_type: ChangeType
    resolution: Resolution = Resolution.RB_WINS  # cue resolution
    grid_resolution: Resolution = Resolution.RB_WINS  # beatgrid resolution (RB/Traktor)
    match_confidence: str = "exact"  # exact | filename | fuzzy
    grid_warning: str = ""  # non-empty when multi-region grid won't fully transfer
    grid_changed: bool = False  # RB beatgrid anchor/BPM differs from Traktor's

    cues_added: list[Cue] = field(default_factory=list)
    cues_changed: list[tuple[Cue, Cue]] = field(default_factory=list)  # (traktor, rb)
    cues_removed: list[Cue] = field(default_factory=list)

    @property
    def has_grid_change(self) -> bool:
        return self.rb_track.beatgrid is not None and self.rb_track.beatgrid.markers != ()


@dataclass
class PlaylistPlan:
    """Which Rekordbox playlists the user chose to import."""

    roots: list[RbPlaylist] = field(default_factory=list)
    selected_names: set[str] = field(default_factory=set)


@dataclass
class SyncPlan:
    track_changes: list[TrackChange] = field(default_factory=list)
    playlist_plan: PlaylistPlan = field(default_factory=PlaylistPlan)

    def summary(self) -> dict[str, int]:
        from collections import Counter

        c = Counter(tc.change_type for tc in self.track_changes)
        return {
            "total": len(self.track_changes),
            "no_change": c[ChangeType.NO_CHANGE],
            "new_cues": c[ChangeType.NEW_CUES],
            "conflict": c[ChangeType.CONFLICT],
            "unmatched": c[ChangeType.UNMATCHED],
        }
