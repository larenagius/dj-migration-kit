"""Translate canonical cues into Mixxx `cues` table rows.

Format confirmed against Mixxx's own Traktor-import code (mixxxdj/mixxx PR #1411):

  * position is stored in **stereo samples**:  seconds * samplerate * 2
    (Mixxx's engine is always 2-channel, so the factor is 2 regardless of the
    file's real channel count).
  * cue type: 1 = HotCue, 2 = MainCue.
  * `hotcue` is the 0-based pad slot (-1 for non-hotcue).
  * `color` is a 32-bit ARGB integer (0xAARRGGBB); Mixxx's default is 0xFFFF0000.
"""

from __future__ import annotations

from ..models import Cue, CueKind

MIXXX_TYPE_HOTCUE = 1
MIXXX_TYPE_MAINCUE = 2

DEFAULT_COLOR = 0xFFFF0000  # opaque red (Mixxx default)
MAX_HOTCUE_SLOT = 35  # Mixxx supports 36 hotcues (0..35)


def position_samples(position_ms: float, samplerate: int) -> int:
    """Convert a millisecond offset to Mixxx's stereo-sample position."""
    return int(round(position_ms / 1000.0 * samplerate * 2))


def rgb_to_mixxx_color(rgb: tuple[int, int, int] | None) -> int:
    if not rgb:
        return DEFAULT_COLOR
    r, g, b = rgb
    return 0xFF000000 | (r << 16) | (g << 8) | b


def build_cue_rows(cues: list[Cue], samplerate: int, include_memory: bool = True) -> list[dict]:
    """Build Mixxx `cues` rows (dicts) from canonical cues for one track.

    Hot cues keep their Traktor slot. Memory cues (which Mixxx has no direct
    equivalent for) are optionally preserved by assigning them to the next free
    hotcue slots so they aren't lost.
    """
    rows: list[dict] = []
    used = {c.hotcue_index for c in cues if c.kind is CueKind.HOT and c.hotcue_index is not None}

    for c in cues:
        if c.kind is CueKind.HOT and c.hotcue_index is not None:
            rows.append(_row(c, c.hotcue_index, samplerate))

    if include_memory:
        next_slot = 0
        for c in cues:
            if c.kind is not CueKind.MEMORY:
                continue
            while next_slot in used and next_slot <= MAX_HOTCUE_SLOT:
                next_slot += 1
            if next_slot > MAX_HOTCUE_SLOT:
                break
            used.add(next_slot)
            rows.append(_row(c, next_slot, samplerate))

    return rows


def _row(cue: Cue, slot: int, samplerate: int) -> dict:
    return {
        "type": MIXXX_TYPE_HOTCUE,
        "position": position_samples(cue.position_ms, samplerate),
        "length": 0,
        "hotcue": slot,
        "label": cue.name or "",
        "color": rgb_to_mixxx_color(cue.color_rgb),
    }
