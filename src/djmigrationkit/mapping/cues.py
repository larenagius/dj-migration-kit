"""Translate canonical cues into Traktor CUE_V2 lxml elements.

Traktor Pro 4 stores an arbitrary ``COLOR="#RRGGBB"`` per cue, so a Rekordbox
cue's RGB color passes straight through -- no quantization to a fixed palette is
needed (that was only required for older Traktor formats).

Mapping rules:
    hot cue    -> CUE_V2 TYPE=0, HOTCUE=index(0..7), COLOR=#hex
    memory cue -> CUE_V2 TYPE=0, HOTCUE=-1, COLOR=#hex (shows as a waveform marker)
    loop       -> CUE_V2 TYPE=5, LEN=length  (parsed but not written in v1)
"""

from __future__ import annotations

from lxml import etree

from ..models import Cue, CueKind

TK_TYPE_CUE = 0
TK_TYPE_LOOP = 5

# Fallback colors (Traktor-ish defaults) for hot cues that have no RB color.
DEFAULT_HOTCUE_COLORS = [
    (0, 130, 255),    # blue
    (0, 200, 0),      # green
    (255, 180, 0),    # amber
    (255, 0, 0),      # red
    (180, 0, 255),    # purple
    (0, 220, 220),    # cyan
    (255, 0, 180),    # magenta
    (255, 255, 255),  # white
]


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def _color_for(cue: Cue) -> str | None:
    if cue.color_rgb:
        return rgb_to_hex(cue.color_rgb)
    if cue.kind is CueKind.HOT and cue.hotcue_index is not None:
        return rgb_to_hex(DEFAULT_HOTCUE_COLORS[cue.hotcue_index % len(DEFAULT_HOTCUE_COLORS)])
    return None


def cue_to_element(cue: Cue, displ_order: int = 0) -> etree._Element:
    """Build one <CUE_V2> element for a hot or memory cue."""
    el = etree.Element("CUE_V2")
    el.set("NAME", cue.name or ("Cue" if cue.kind is CueKind.MEMORY else "n.n."))
    el.set("DISPL_ORDER", str(displ_order))
    if cue.kind is CueKind.LOOP:
        el.set("TYPE", str(TK_TYPE_LOOP))
    else:
        el.set("TYPE", str(TK_TYPE_CUE))
    el.set("START", f"{cue.position_ms:.6f}")
    el.set("LEN", f"{(cue.length_ms or 0.0):.6f}")
    el.set("REPEATS", "-1")
    el.set("HOTCUE", str(cue.hotcue_index if cue.hotcue_index is not None else -1))
    color = _color_for(cue)
    if color:
        el.set("COLOR", color)
    return el


def cues_to_elements(cues: list[Cue]) -> list[etree._Element]:
    """Build CUE_V2 elements for a list of cues, in a stable order.

    Hot cues first (by slot), then memory cues (by position) -- mirrors how
    Traktor itself tends to order them, keeping diffs and the GUI tidy.
    """
    hot = sorted(
        [c for c in cues if c.kind is CueKind.HOT],
        key=lambda c: (c.hotcue_index if c.hotcue_index is not None else 99),
    )
    mem = sorted(
        [c for c in cues if c.kind is CueKind.MEMORY], key=lambda c: c.position_ms
    )
    ordered = hot + mem
    return [cue_to_element(c, displ_order=i) for i, c in enumerate(ordered)]
