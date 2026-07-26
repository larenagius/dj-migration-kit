"""Cue-set conflict detection and resolution policies.

When a Rekordbox track matches a Traktor entry, both may already carry cues. The
user decides per track (or in bulk) which side wins. This module is pure logic on
the canonical :class:`Cue` model -- no Traktor/RB specifics -- so it is trivially
testable and reused by both the CLI and GUI.
"""

from __future__ import annotations

from ..models import Cue, CueKind, Resolution


def classify(traktor_cues: list[Cue], rb_cues: list[Cue]) -> tuple[list[Cue], list[tuple[Cue, Cue]], list[Cue]]:
    """Diff two cue sets keyed by :meth:`Cue.signature`.

    Returns ``(added, changed, removed)`` describing the move from Traktor's
    current cues to Rekordbox's, where:
        added   -- in RB, not in Traktor
        changed -- present in both (same signature) but differing details
        removed -- in Traktor, not in RB
    """
    tk_by_sig = {c.signature(): c for c in traktor_cues}
    rb_by_sig = {c.signature(): c for c in rb_cues}

    added = [c for sig, c in rb_by_sig.items() if sig not in tk_by_sig]
    removed = [c for sig, c in tk_by_sig.items() if sig not in rb_by_sig]
    changed = [
        (tk_by_sig[sig], rb_by_sig[sig])
        for sig in tk_by_sig.keys() & rb_by_sig.keys()
        if _differs(tk_by_sig[sig], rb_by_sig[sig])
    ]
    return added, changed, removed


def _differs(a: Cue, b: Cue) -> bool:
    return (
        round(a.position_ms) != round(b.position_ms)
        or a.name != b.name
        or a.color_rgb != b.color_rgb
    )


def has_conflict(traktor_cues: list[Cue], rb_cues: list[Cue]) -> bool:
    """A conflict exists when Traktor already has cues that differ from RB's."""
    if not traktor_cues:
        return False
    added, changed, removed = classify(traktor_cues, rb_cues)
    return bool(changed or removed)


def resolve(traktor_cues: list[Cue], rb_cues: list[Cue], policy: Resolution) -> list[Cue]:
    """Produce the final cue list for a track given a resolution policy.

    * RB_WINS       -> Rekordbox cues replace Traktor's entirely.
    * TRAKTOR_WINS  -> keep Traktor's cues unchanged.
    * MERGE         -> keep all Traktor cues; add RB cues that don't collide.
                       Collision = same hotcue slot (for hot cues) or same
                       rounded position (for memory cues).
    """
    if policy is Resolution.RB_WINS:
        return list(rb_cues)
    if policy is Resolution.TRAKTOR_WINS:
        return list(traktor_cues)

    # MERGE
    result = list(traktor_cues)
    used_hot_slots = {c.hotcue_index for c in traktor_cues if c.kind is CueKind.HOT}
    used_mem_pos = {round(c.position_ms / 10.0) for c in traktor_cues if c.kind is CueKind.MEMORY}
    for c in rb_cues:
        if c.kind is CueKind.HOT:
            if c.hotcue_index not in used_hot_slots:
                result.append(c)
                used_hot_slots.add(c.hotcue_index)
        elif c.kind is CueKind.MEMORY:
            key = round(c.position_ms / 10.0)
            if key not in used_mem_pos:
                result.append(c)
                used_mem_pos.add(key)
    return result
