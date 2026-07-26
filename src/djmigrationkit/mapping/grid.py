"""Translate a canonical BeatGrid into Traktor's grid representation.

Traktor models a beatgrid as:
  * a ``<TEMPO BPM="..." BPM_QUALITY="100.0">`` element, plus
  * a grid-anchor cue: ``<CUE_V2 NAME="AutoGrid" TYPE="4" HOTCUE="-1" START="...">``
    with a child ``<GRID BPM="...">``.

For a constant-tempo track this single anchor + BPM fully defines the grid. For a
track with tempo changes (multi-region), Traktor's single-anchor model can only
represent the first region; :class:`BeatGrid.is_multi_region` lets the engine warn
about the lossy transfer.
"""

from __future__ import annotations

from typing import Optional

from lxml import etree

from ..models import BeatGrid

TK_TYPE_GRID = 4


def grid_anchor(beatgrid: BeatGrid) -> Optional[tuple[float, float]]:
    """Return (anchor_position_ms, bpm) for Traktor, or None if no grid."""
    if not beatgrid or not beatgrid.markers:
        return None
    anchor = beatgrid.first_downbeat
    if anchor is None:
        return None
    return anchor.position_ms, anchor.bpm


def build_grid_cue_element(beatgrid: BeatGrid) -> Optional[etree._Element]:
    """Build the AutoGrid CUE_V2 (TYPE=4) element for a beatgrid."""
    anchor = grid_anchor(beatgrid)
    if anchor is None:
        return None
    pos_ms, bpm = anchor
    el = etree.Element("CUE_V2")
    el.set("NAME", "AutoGrid")
    el.set("DISPL_ORDER", "0")
    el.set("TYPE", str(TK_TYPE_GRID))
    el.set("START", f"{pos_ms:.6f}")
    el.set("LEN", "0.000000")
    el.set("REPEATS", "-1")
    el.set("HOTCUE", "-1")
    grid = etree.SubElement(el, "GRID")
    grid.set("BPM", f"{bpm:.6f}")
    return el


def tempo_bpm(beatgrid: BeatGrid) -> Optional[float]:
    """The BPM to write into the entry's <TEMPO> element."""
    anchor = grid_anchor(beatgrid)
    return anchor[1] if anchor else None
