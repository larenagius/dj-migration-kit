"""End-to-end writer tests on a small fixture collection."""

import hashlib
import shutil
from pathlib import Path

from lxml import etree

from djmigrationkit.matcher import TrackMatcher
from djmigrationkit.models import (
    BeatGrid,
    BeatMarker,
    Cue,
    CueKind,
    RbTrack,
    Resolution,
)
from djmigrationkit.sync import engine
from djmigrationkit.traktor_io.reader import TraktorCollection
from djmigrationkit.traktor_io.writer import MergeWriter, traktor_entry_key

FIXTURE = Path(__file__).parent / "fixtures" / "collection.nml"


def _make_plan(entries, rb_tracks, resolution=Resolution.RB_WINS):
    matches = TrackMatcher(entries).match_all(rb_tracks)
    return engine.build_plan(matches, default_resolution=resolution)


def test_live_file_never_modified(tmp_path):
    src = tmp_path / "collection.nml"
    shutil.copy(FIXTURE, src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    before_mtime = src.stat().st_mtime_ns

    tk = TraktorCollection.load(src)
    rb = [RbTrack(rb_id="1", file_path="G:/elsewhere/track-one.mp3", file_size=11718 * 1024,
                  cues=[Cue(position_ms=5000, kind=CueKind.HOT, hotcue_index=0,
                            color_rgb=(255, 0, 0))])]
    plan = _make_plan(tk.entries(), rb)
    result = MergeWriter(src).apply(plan).write()

    # source untouched
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert src.stat().st_mtime_ns == before_mtime
    # merge written separately
    assert result.output_path.name == "collection-merge.nml"
    assert result.output_path.exists()


def test_rb_cues_written_into_merge(tmp_path):
    src = tmp_path / "collection.nml"
    shutil.copy(FIXTURE, src)
    tk = TraktorCollection.load(src)
    rb = [RbTrack(rb_id="1", file_path="G:/x/track-one.mp3", file_size=11718 * 1024,
                  cues=[
                      Cue(position_ms=5000, kind=CueKind.HOT, hotcue_index=0, name="Intro",
                          color_rgb=(40, 226, 20)),
                      Cue(position_ms=120000, kind=CueKind.HOT, hotcue_index=1,
                          color_rgb=(255, 160, 0)),
                      Cue(position_ms=60000, kind=CueKind.MEMORY, name="mem"),
                  ])]
    plan = _make_plan(tk.entries(), rb)
    result = MergeWriter(src).apply(plan).write()

    merged = etree.parse(str(result.output_path))
    entry = merged.find('.//ENTRY[@TITLE="Test One"]')
    cues = entry.findall("CUE_V2")
    hot = [c for c in cues if c.get("TYPE") == "0" and c.get("HOTCUE") != "-1"]
    mem = [c for c in cues if c.get("TYPE") == "0" and c.get("HOTCUE") == "-1"]
    # old cue replaced; 2 hot + 1 memory now present
    assert len(hot) == 2
    assert len(mem) == 1
    starts = sorted(float(c.get("START")) for c in hot)
    assert starts == [5000.0, 120000.0]
    assert any(c.get("COLOR") == "#28E214" for c in hot)
    # the old "OldCue" is gone
    assert not any(c.get("NAME") == "OldCue" for c in cues)


def test_grid_and_tempo_updated(tmp_path):
    src = tmp_path / "collection.nml"
    shutil.copy(FIXTURE, src)
    tk = TraktorCollection.load(src)
    rb = [RbTrack(rb_id="1", file_path="G:/x/track-one.mp3", file_size=11718 * 1024,
                  bpm=140.0,
                  beatgrid=BeatGrid(markers=(BeatMarker(250.0, 140.0, 1),)),
                  cues=[Cue(position_ms=250.0, kind=CueKind.HOT, hotcue_index=0)])]
    plan = _make_plan(tk.entries(), rb)
    result = MergeWriter(src).apply(plan).write()

    merged = etree.parse(str(result.output_path))
    entry = merged.find('.//ENTRY[@TITLE="Test One"]')
    tempo = entry.find("TEMPO")
    assert tempo.get("BPM") == "140.000000"
    grid = [c for c in entry.findall("CUE_V2") if c.get("TYPE") == "4"]
    assert len(grid) == 1
    assert grid[0].find("GRID").get("BPM") == "140.000000"
    assert grid[0].get("START") == "250.000000"


def test_traktor_wins_keeps_original(tmp_path):
    src = tmp_path / "collection.nml"
    shutil.copy(FIXTURE, src)
    tk = TraktorCollection.load(src)
    rb = [RbTrack(rb_id="1", file_path="G:/x/track-one.mp3", file_size=11718 * 1024,
                  cues=[Cue(position_ms=5000, kind=CueKind.HOT, hotcue_index=0)])]
    plan = _make_plan(tk.entries(), rb, resolution=Resolution.TRAKTOR_WINS)
    result = MergeWriter(src).apply(plan).write()

    merged = etree.parse(str(result.output_path))
    entry = merged.find('.//ENTRY[@TITLE="Test One"]')
    # original OldCue preserved (Traktor wins, no beatgrid on rb -> no grid change)
    assert any(c.get("NAME") == "OldCue" for c in entry.findall("CUE_V2"))


def test_grid_resolution_decoupled_from_cues(tmp_path):
    # Cues from RB, but keep Traktor's beatgrid (grid_resolution = TRAKTOR_WINS).
    src = tmp_path / "collection.nml"
    shutil.copy(FIXTURE, src)
    tk = TraktorCollection.load(src)
    rb = [RbTrack(rb_id="1", file_path="G:/x/track-one.mp3", file_size=11718 * 1024,
                  bpm=140.0,
                  beatgrid=BeatGrid(markers=(BeatMarker(250.0, 140.0, 1),)),
                  cues=[Cue(position_ms=5000, kind=CueKind.HOT, hotcue_index=0)])]
    plan = _make_plan(tk.entries(), rb)
    plan.track_changes[0].grid_resolution = Resolution.TRAKTOR_WINS  # keep TK grid
    result = MergeWriter(src).apply(plan).write()

    merged = etree.parse(str(result.output_path))
    entry = merged.find('.//ENTRY[@TITLE="Test One"]')
    # cues replaced with RB's
    hot = [c for c in entry.findall("CUE_V2") if c.get("HOTCUE") == "0" and c.get("TYPE") == "0"]
    assert hot and hot[0].get("START") == "5000.000000"
    # but TEMPO/grid stayed Traktor's 120 (not RB 140)
    assert entry.find("TEMPO").get("BPM") == "120.000000"
    grid = [c for c in entry.findall("CUE_V2") if c.get("TYPE") == "4"]
    assert grid and grid[0].find("GRID").get("BPM") == "120.000000"


def test_transfer_grids_false_skips_all_grids(tmp_path):
    src = tmp_path / "collection.nml"
    shutil.copy(FIXTURE, src)
    tk = TraktorCollection.load(src)
    rb = [RbTrack(rb_id="1", file_path="G:/x/track-one.mp3", file_size=11718 * 1024,
                  beatgrid=BeatGrid(markers=(BeatMarker(250.0, 140.0, 1),)),
                  cues=[Cue(position_ms=5000, kind=CueKind.HOT, hotcue_index=0)])]
    plan = _make_plan(tk.entries(), rb)
    result = MergeWriter(src).apply(plan, transfer_grids=False).write()
    merged = etree.parse(str(result.output_path))
    entry = merged.find('.//ENTRY[@TITLE="Test One"]')
    assert entry.find("TEMPO").get("BPM") == "120.000000"  # untouched


def test_merge_output_is_valid_and_reparsable(tmp_path):
    src = tmp_path / "collection.nml"
    shutil.copy(FIXTURE, src)
    tk = TraktorCollection.load(src)
    rb = [RbTrack(rb_id="1", file_path="G:/x/track-one.mp3", file_size=11718 * 1024,
                  cues=[Cue(position_ms=5000, kind=CueKind.HOT, hotcue_index=0)])]
    plan = _make_plan(tk.entries(), rb)
    result = MergeWriter(src).apply(plan).write()
    # reparse through our own reader: should not raise and keep both entries
    reparsed = TraktorCollection.load(result.output_path)
    assert len(reparsed.entries()) == 2


def test_traktor_entry_key_format(tmp_path):
    tk = TraktorCollection.load(FIXTURE)
    el = tk.entry_elements()[0]
    assert traktor_entry_key(el) == "C:/:Music/:House/:track-one.mp3"
