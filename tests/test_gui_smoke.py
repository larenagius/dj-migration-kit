"""Headless GUI smoke tests (offscreen Qt). Verifies the view-model + wiring."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from djmigrationkit.matcher import TrackMatcher  # noqa: E402
from djmigrationkit.models import (  # noqa: E402
    BeatGrid, BeatMarker, ChangeType, Cue, CueKind, RbPlaylist, RbTrack, Resolution,
)
from djmigrationkit.sync import engine  # noqa: E402
from djmigrationkit.traktor_io.reader import TraktorCollection  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "collection.nml"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _plan():
    tk = TraktorCollection.load(FIXTURE)
    rb = [
        RbTrack(rb_id="1", file_path="G:/x/track-one.mp3", file_size=11718 * 1024,
                artist="Artist A", title="Test One",
                beatgrid=BeatGrid(markers=(BeatMarker(0, 140.0, 1),)),
                cues=[Cue(position_ms=5000, kind=CueKind.HOT, hotcue_index=0,
                          color_rgb=(40, 226, 20))]),
        RbTrack(rb_id="2", file_path="G:/x/track-two.mp3", file_size=9375 * 1024,
                artist="Artist B", title="Test Two", cues=[]),
        RbTrack(rb_id="3", file_path="G:/x/orphan.mp3", artist="Nobody", title="Orphan"),
    ]
    matches = TrackMatcher(tk.entries()).match_all(rb)
    plan = engine.build_plan(matches)
    plan.playlist_plan.roots = [
        RbPlaylist(name="Folder", is_folder=True, children=[
            RbPlaylist(name="My Set", track_ids=["1", "2"]),
        ])
    ]
    return plan


def test_table_model_rows_and_filter(qapp):
    from djmigrationkit.gui.track_model import TrackTableModel, TrackFilterProxy
    plan = _plan()
    model = TrackTableModel(plan)
    assert model.rowCount() == 3
    proxy = TrackFilterProxy(); proxy.setSourceModel(model)
    proxy.set_type_filter(ChangeType.UNMATCHED)
    assert proxy.rowCount() == 1  # orphan
    proxy.set_type_filter(None)
    proxy.set_text("test two")
    assert proxy.rowCount() == 1


def test_header_sorting_by_title(qapp):
    from PySide6.QtCore import Qt
    from djmigrationkit.gui.track_model import TrackTableModel, TrackFilterProxy
    plan = _plan()
    model = TrackTableModel(plan)
    proxy = TrackFilterProxy(); proxy.setSourceModel(model)
    proxy.setSortRole(Qt.UserRole)

    proxy.sort(2, Qt.AscendingOrder)  # Title column
    titles = [proxy.index(r, 2).data() for r in range(proxy.rowCount())]
    assert titles == sorted(titles, key=str.casefold)

    proxy.sort(2, Qt.DescendingOrder)
    titles_desc = [proxy.index(r, 2).data() for r in range(proxy.rowCount())]
    assert titles_desc == sorted(titles, key=str.casefold, reverse=True)


def test_bulk_and_single_resolution(qapp):
    from djmigrationkit.gui.track_model import TrackTableModel
    plan = _plan()
    model = TrackTableModel(plan)
    model.set_resolution_bulk(Resolution.TRAKTOR_WINS, only_conflicts=False)
    matched = [tc for tc in plan.track_changes if tc.change_type is not ChangeType.UNMATCHED]
    assert all(tc.resolution is Resolution.TRAKTOR_WINS for tc in matched)
    model.set_resolution(0, Resolution.MERGE)
    assert plan.track_changes[0].resolution is Resolution.MERGE


def test_grid_resolution_independent_of_cues(qapp):
    from djmigrationkit.gui.track_model import TrackTableModel
    plan = _plan()
    model = TrackTableModel(plan)
    # bulk grids to Traktor; cue resolution must stay default RB_WINS
    model.set_grid_resolution_bulk(Resolution.TRAKTOR_WINS)
    track_one = next(tc for tc in plan.track_changes if tc.rb_track.rb_id == "1")
    assert track_one.grid_resolution is Resolution.TRAKTOR_WINS
    assert track_one.resolution is Resolution.RB_WINS  # unchanged
    # track 2 has no beatgrid -> grid resolution untouched / irrelevant
    model.set_grid_resolution(0, Resolution.RB_WINS)
    assert plan.track_changes[0].grid_resolution is Resolution.RB_WINS


def test_mainwindow_constructs_and_loads_plan(qapp):
    from djmigrationkit.gui.app import MainWindow
    win = MainWindow()
    win.model.set_plan(_plan())
    win._plan = _plan()
    win._populate_playlists(win._plan.playlist_plan.roots)
    # playlist tree has one folder with one checkable child
    assert win.pl_tree.topLevelItemCount() == 1
    win._set_all_playlists(True)
    assert win._selected_playlist_names() == {"My Set"}


def test_select_track_populates_detail_including_grid(qapp):
    from djmigrationkit.gui.app import MainWindow
    win = MainWindow()
    plan = _plan()
    win._plan = plan
    win.model.set_plan(plan)
    # select first row -> _on_select runs the detail panel (cues + grid radios)
    win.table.selectRow(0)
    win._on_select()
    row = win._current_row()
    tc = win.model.track_change(row)
    # grid radios reflect whether this track has an RB beatgrid
    assert win.grid_rb.isEnabled() == (tc.rb_track.beatgrid is not None)
    # cue detail tables populated without error
    assert win.rb_cues.rowCount() == len(tc.rb_track.cues)
