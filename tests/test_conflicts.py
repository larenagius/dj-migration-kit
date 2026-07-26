from djmigrationkit.models import Cue, CueKind, Resolution
from djmigrationkit.sync import conflicts


def hot(i, pos, name="", color=None):
    return Cue(position_ms=pos, kind=CueKind.HOT, hotcue_index=i, name=name, color_rgb=color)


def mem(pos, name=""):
    return Cue(position_ms=pos, kind=CueKind.MEMORY, name=name)


def test_no_conflict_when_traktor_empty():
    rb = [hot(0, 1000), hot(1, 2000)]
    assert conflicts.has_conflict([], rb) is False


def test_classify_added_changed_removed():
    traktor = [hot(0, 1000, name="Intro"), hot(1, 5000)]
    rb = [hot(0, 1000, name="Drop"), hot(2, 9000)]  # 0 changed name, 2 added, 1 removed
    added, changed, removed = conflicts.classify(traktor, rb)
    assert {c.hotcue_index for c in added} == {2}
    assert {c.hotcue_index for c in removed} == {1}
    assert len(changed) == 1 and changed[0][1].name == "Drop"


def test_conflict_detected_when_traktor_has_differing_cue():
    traktor = [hot(0, 1000, name="A")]
    rb = [hot(0, 1000, name="B")]
    assert conflicts.has_conflict(traktor, rb) is True


def test_rb_wins_replaces():
    traktor = [hot(0, 1000)]
    rb = [hot(0, 2000), hot(1, 3000)]
    out = conflicts.resolve(traktor, rb, Resolution.RB_WINS)
    assert out == rb


def test_traktor_wins_keeps():
    traktor = [hot(0, 1000)]
    rb = [hot(0, 2000)]
    out = conflicts.resolve(traktor, rb, Resolution.TRAKTOR_WINS)
    assert out == traktor


def test_merge_fills_empty_hot_slots_only():
    traktor = [hot(0, 1000)]
    rb = [hot(0, 9999), hot(1, 3000), mem(4000)]
    out = conflicts.resolve(traktor, rb, Resolution.MERGE)
    # slot 0 kept from Traktor, slot 1 added from RB, memory cue added
    hots = sorted([c for c in out if c.kind is CueKind.HOT], key=lambda c: c.hotcue_index)
    assert [c.hotcue_index for c in hots] == [0, 1]
    assert hots[0].position_ms == 1000  # Traktor's slot 0 preserved
    assert any(c.kind is CueKind.MEMORY for c in out)


def test_merge_dedupes_memory_by_position():
    traktor = [mem(4000)]
    rb = [mem(4003)]  # within 10ms bucket -> treated as same
    out = conflicts.resolve(traktor, rb, Resolution.MERGE)
    assert len([c for c in out if c.kind is CueKind.MEMORY]) == 1
