from djmigrationkit.models import BeatGrid, BeatMarker
from djmigrationkit.mapping import grid as grid_map


def _grid(*triples):
    return BeatGrid(markers=tuple(BeatMarker(p, b, n) for p, b, n in triples))


def test_anchor_is_first_downbeat():
    g = _grid((86.0, 140.0, 3), (512.0, 140.0, 4), (938.0, 140.0, 1))
    pos, bpm = grid_map.grid_anchor(g)
    assert pos == 938.0 and bpm == 140.0


def test_build_grid_element_has_grid_child():
    g = _grid((26.0, 126.0, 1))
    el = grid_map.build_grid_cue_element(g)
    assert el.get("TYPE") == "4"
    assert el.get("HOTCUE") == "-1"
    assert el.get("START") == "26.000000"
    child = el.find("GRID")
    assert child is not None and child.get("BPM") == "126.000000"


def test_empty_grid_returns_none():
    assert grid_map.build_grid_cue_element(BeatGrid()) is None
    assert grid_map.grid_anchor(BeatGrid()) is None


def test_tempo_bpm():
    g = _grid((0.0, 128.5, 1))
    assert grid_map.tempo_bpm(g) == 128.5


def test_multi_region_detected():
    g = _grid((0.0, 120.0, 1), (1000.0, 140.0, 1))
    assert g.is_multi_region is True
    # anchor still the first downbeat / dominant bpm = first
    assert grid_map.tempo_bpm(g) == 120.0
