"""Traktor -> Mixxx migration engine: match tracks, map cues/playlists, apply.

Reuses the existing Traktor reader and the track matcher. The matcher is generic
over "source" and "target"; here the source is a Traktor entry and the target is
a Mixxx track (``Match.traktor_entry`` holds the matched target).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..matcher import TrackMatcher
from ..models import MixxxTrack, TraktorEntry, TraktorPlaylist
from ..mapping import mixxx_cues
from ..traktor_io.reader import TraktorCollection
from .db import CueAssignment, MixxxDatabase, MixxxMergeWriter, PlaylistSpec, MixxxWriteResult


@dataclass
class MixxxPlan:
    cue_assignments: list[CueAssignment] = field(default_factory=list)
    playlist_specs: list[PlaylistSpec] = field(default_factory=list)
    match_counts: Counter = field(default_factory=Counter)  # exact/filename/fuzzy/none
    total_traktor: int = 0
    total_mixxx: int = 0

    def summary(self) -> dict:
        return {
            "traktor_tracks": self.total_traktor,
            "mixxx_tracks": self.total_mixxx,
            "matched_exact": self.match_counts.get("exact", 0),
            "matched_filename": self.match_counts.get("filename", 0),
            "matched_fuzzy": self.match_counts.get("fuzzy", 0),
            "unmatched": self.match_counts.get("none", 0),
            "tracks_with_cues": len(self.cue_assignments),
            "cues": sum(len(a.rows) for a in self.cue_assignments),
            "playlists": len(self.playlist_specs),
        }


def _flatten_playlists(nodes: list[TraktorPlaylist], prefix: str = "") -> list[tuple[str, list[str]]]:
    """Flatten Traktor's playlist tree into (display_name, track_keys) leaves.

    Mixxx playlists are flat, so folder paths become part of the name
    ('Folder / Sub / Playlist').
    """
    out: list[tuple[str, list[str]]] = []
    for n in nodes:
        name = f"{prefix}{n.name}"
        if n.is_folder:
            out.extend(_flatten_playlists(n.children, prefix=f"{name} / "))
        elif n.track_keys:
            out.append((name, n.track_keys))
    return out


def build_plan(
    traktor_nml: str | Path,
    mixxx_db: str | Path,
    include_memory: bool = True,
    include_playlists: bool = True,
    selected_playlists: Optional[set[str]] = None,
) -> MixxxPlan:
    tk = TraktorCollection.load(traktor_nml)
    entries = tk.entries()
    mixxx_tracks = MixxxDatabase(mixxx_db).tracks()

    matcher = TrackMatcher(mixxx_tracks)
    plan = MixxxPlan(total_traktor=len(entries), total_mixxx=len(mixxx_tracks))

    key_to_mixxx: dict[str, MixxxTrack] = {}
    for entry in entries:
        m = matcher.match(entry)
        plan.match_counts[m.confidence] += 1
        matched: Optional[MixxxTrack] = m.traktor_entry  # generic "matched target"
        if matched is None:
            continue
        if entry.traktor_key:
            key_to_mixxx[entry.traktor_key] = matched
        rows = mixxx_cues.build_cue_rows(entry.cues, matched.samplerate, include_memory=include_memory)
        if rows:
            plan.cue_assignments.append(CueAssignment(track_id=matched.mixxx_id, rows=rows))

    if include_playlists:
        for name, keys in _flatten_playlists(tk.playlists()):
            if selected_playlists is not None and name not in selected_playlists:
                continue
            track_ids = [key_to_mixxx[k].mixxx_id for k in keys if k in key_to_mixxx]
            if track_ids:
                plan.playlist_specs.append(PlaylistSpec(name=name, track_ids=track_ids))

    return plan


def migrate(
    traktor_nml: str | Path,
    mixxx_db: str | Path,
    include_memory: bool = True,
    include_playlists: bool = True,
    selected_playlists: Optional[set[str]] = None,
) -> tuple[MixxxPlan, MixxxWriteResult]:
    plan = build_plan(
        traktor_nml, mixxx_db,
        include_memory=include_memory,
        include_playlists=include_playlists,
        selected_playlists=selected_playlists,
    )
    result = MixxxMergeWriter(mixxx_db).apply_and_write(plan.cue_assignments, plan.playlist_specs)
    return plan, result
