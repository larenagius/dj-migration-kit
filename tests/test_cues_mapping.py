from lxml import etree

from djmigrationkit.models import Cue, CueKind
from djmigrationkit.mapping import cues as cue_map


def test_hot_cue_element_attributes():
    c = Cue(position_ms=1234.5, kind=CueKind.HOT, hotcue_index=2,
            name="Drop", color_rgb=(255, 0, 128))
    el = cue_map.cue_to_element(c)
    assert el.tag == "CUE_V2"
    assert el.get("TYPE") == "0"
    assert el.get("HOTCUE") == "2"
    assert el.get("NAME") == "Drop"
    assert el.get("START") == "1234.500000"
    assert el.get("LEN") == "0.000000"
    assert el.get("REPEATS") == "-1"
    assert el.get("COLOR") == "#FF0080"


def test_memory_cue_has_hotcue_minus_one():
    c = Cue(position_ms=9000, kind=CueKind.MEMORY)
    el = cue_map.cue_to_element(c)
    assert el.get("HOTCUE") == "-1"
    assert el.get("TYPE") == "0"


def test_default_color_assigned_to_uncolored_hotcue():
    c = Cue(position_ms=0, kind=CueKind.HOT, hotcue_index=0)
    el = cue_map.cue_to_element(c)
    assert el.get("COLOR", "").startswith("#")


def test_cues_ordering_hot_then_memory():
    cues = [
        Cue(position_ms=5000, kind=CueKind.MEMORY),
        Cue(position_ms=100, kind=CueKind.HOT, hotcue_index=1),
        Cue(position_ms=50, kind=CueKind.HOT, hotcue_index=0),
    ]
    els = cue_map.cues_to_elements(cues)
    order = [(e.get("HOTCUE")) for e in els]
    assert order == ["0", "1", "-1"]
    # displ_order is sequential
    assert [e.get("DISPL_ORDER") for e in els] == ["0", "1", "2"]


def test_rgb_to_hex():
    assert cue_map.rgb_to_hex((40, 226, 20)) == "#28E214"
