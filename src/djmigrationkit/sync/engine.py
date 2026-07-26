"""Sync engine: turn matches into a reviewable plan, then apply it.

Two phases, deliberately separated so the GUI can sit in between:

1. :func:`build_plan` -- pure analysis. Takes matches + chosen playlists and
   produces a :class:`SyncPlan` of per-track diffs. No Traktor objects mutated.
2. :func:`apply_plan` -- mutation. Walks the plan and rewrites each matched
   Traktor entry's cues/grid per its resolution, then writes the result to a
   merge file via the safe writer. Lives in writer.py to keep this module free of
   Traktor-library imports (so build_plan stays unit-testable without deps).
"""

from __future__ import annotations

from . import conflicts
from ..matcher import Match
from ..models import (
    ChangeType,
    PlaylistPlan,
    Resolution,
    SyncPlan,
    TrackChange,
)


def build_plan(
    matches: list[Match],
    playlist_plan: PlaylistPlan | None = None,
    default_resolution: Resolution = Resolution.RB_WINS,
    default_grid_resolution: Resolution = Resolution.RB_WINS,
) -> SyncPlan:
    """Build a SyncPlan from matcher output.

    Each match becomes one TrackChange classified as NO_CHANGE / NEW_CUES /
    CONFLICT / UNMATCHED, with the cue diff precomputed for the GUI.
    """
    changes: list[TrackChange] = []

    for m in matches:
        rb = m.rb_track
        entry = m.traktor_entry

        if entry is None:
            changes.append(
                TrackChange(
                    rb_track=rb,
                    traktor_entry=None,
                    change_type=ChangeType.UNMATCHED,
                    match_confidence=m.confidence,
                )
            )
            continue

        added, changed, removed = conflicts.classify(entry.cues, rb.cues)
        grid_warning = ""
        if rb.beatgrid and rb.beatgrid.is_multi_region:
            grid_warning = (
                "Track has tempo changes (multi-region grid); only the first "
                "region's BPM/anchor transfers to Traktor."
            )
        grid_changed = _grid_differs(entry, rb)

        if not added and not changed and not removed:
            change_type = ChangeType.NO_CHANGE
        elif conflicts.has_conflict(entry.cues, rb.cues):
            change_type = ChangeType.CONFLICT
        else:
            change_type = ChangeType.NEW_CUES

        tc = TrackChange(
            rb_track=rb,
            traktor_entry=entry,
            change_type=change_type,
            resolution=default_resolution,
            grid_resolution=default_grid_resolution,
            match_confidence=m.confidence,
            grid_warning=grid_warning,
            cues_added=added,
            cues_changed=changed,
            cues_removed=removed,
        )
        tc.grid_changed = grid_changed
        changes.append(tc)

    return SyncPlan(track_changes=changes, playlist_plan=playlist_plan or PlaylistPlan())


def _grid_differs(entry, rb) -> bool:
    """True if the RB beatgrid's anchor/BPM differs from Traktor's TEMPO/grid."""
    from ..mapping import grid as grid_map

    if rb.beatgrid is None:
        return False
    anchor = grid_map.grid_anchor(rb.beatgrid)
    if anchor is None:
        return False
    _pos, rb_bpm = anchor
    if entry.bpm is None:
        return True
    return abs(entry.bpm - rb_bpm) > 0.05


def set_bulk_resolution(plan: SyncPlan, resolution: Resolution, only_conflicts: bool = True) -> None:
    """Apply a resolution to many track changes at once (GUI bulk actions)."""
    for tc in plan.track_changes:
        if tc.change_type is ChangeType.UNMATCHED:
            continue
        if only_conflicts and tc.change_type is not ChangeType.CONFLICT:
            continue
        tc.resolution = resolution


def set_bulk_grid_resolution(plan: SyncPlan, resolution: Resolution) -> None:
    """Apply a grid resolution to every matched track that has an RB beatgrid."""
    for tc in plan.track_changes:
        if tc.change_type is ChangeType.UNMATCHED:
            continue
        if tc.rb_track.beatgrid is not None:
            tc.grid_resolution = resolution


def final_cues_for(tc: TrackChange):
    """Resolve a single track change into its final cue list."""
    if tc.traktor_entry is None:
        return list(tc.rb_track.cues)
    return conflicts.resolve(tc.traktor_entry.cues, tc.rb_track.cues, tc.resolution)
