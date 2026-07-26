"""PySide6 GUI for the Rekordbox -> Traktor merge.

Flow: pick the live collection.nml + an RB source, Scan (background thread, since
reading beatgrids for a big library takes a while), review the diff per track,
resolve conflicts, optionally pick playlists, then Apply -> collection-merge.nml.
The live collection.nml is never written.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QRadioButton, QSplitter, QTabWidget, QTableView,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from ..matcher import TrackMatcher
from ..models import ChangeType, CueKind, PlaylistPlan, Resolution
from ..sync import engine
from ..traktor_io.reader import TraktorCollection
from .track_model import TrackFilterProxy, TrackTableModel


# --------------------------------------------------------------------------- #
# Background scan worker
# --------------------------------------------------------------------------- #
class ScanWorker(QObject):
    progress = Signal(str)
    finished = Signal(object, object)  # (SyncPlan, list[RbPlaylist])
    failed = Signal(str)

    def __init__(self, traktor_path, rb_xml, transfer_grids, default_res):
        super().__init__()
        self.traktor_path = traktor_path
        self.rb_xml = rb_xml
        self.transfer_grids = transfer_grids
        self.default_res = default_res

    def run(self):
        try:
            self.progress.emit("Parsing Traktor collection ...")
            tk = TraktorCollection.load(self.traktor_path)
            entries = tk.entries()
            self.progress.emit(f"{len(entries)} Traktor entries. Reading Rekordbox ...")

            if self.rb_xml:
                from ..rb_reader.xml import RekordboxXmlReader
                reader = RekordboxXmlReader(self.rb_xml)
            else:
                from ..rb_reader.db import RekordboxDbReader
                reader = RekordboxDbReader()

            rb_tracks = []
            for i, t in enumerate(reader.iter_tracks(with_grid=self.transfer_grids), 1):
                rb_tracks.append(t)
                if i % 100 == 0:
                    self.progress.emit(f"Read {i} Rekordbox tracks ...")
            self.progress.emit(f"{len(rb_tracks)} Rekordbox tracks. Matching ...")

            matches = TrackMatcher(entries).match_all(rb_tracks)
            plan = engine.build_plan(matches, default_resolution=self.default_res)

            self.progress.emit("Reading playlists ...")
            try:
                playlists = reader.playlists()
            except Exception:
                playlists = []
            plan.playlist_plan = PlaylistPlan(roots=playlists, selected_names=set())
            self.finished.emit(plan, playlists)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class ApplyWorker(QObject):
    finished = Signal(object)  # WriteResult
    failed = Signal(str)

    def __init__(self, source, plan, transfer_grids):
        super().__init__()
        self.source = source
        self.plan = plan
        self.transfer_grids = transfer_grids

    def run(self):
        try:
            from ..traktor_io.writer import apply_and_write
            result = apply_and_write(self.source, self.plan, transfer_grids=self.transfer_grids)
            self.finished.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


# --------------------------------------------------------------------------- #
# Cue detail table helper
# --------------------------------------------------------------------------- #
def _fill_cue_table(table: QTableWidget, cues):
    table.setRowCount(0)
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels(["Slot", "Pos (s)", "Name", "Color"])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    rows = sorted(cues, key=lambda c: (c.kind is not CueKind.HOT,
                                       c.hotcue_index if c.hotcue_index is not None else 99,
                                       c.position_ms))
    table.setRowCount(len(rows))
    for r, c in enumerate(rows):
        slot = chr(ord("A") + c.hotcue_index) if c.hotcue_index is not None else "mem"
        table.setItem(r, 0, QTableWidgetItem(slot))
        table.setItem(r, 1, QTableWidgetItem(f"{c.position_ms / 1000:.2f}"))
        table.setItem(r, 2, QTableWidgetItem(c.name or ""))
        color_item = QTableWidgetItem("")
        if c.color_rgb:
            color_item.setBackground(QColor(*c.color_rgb))
        table.setItem(r, 3, color_item)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
def autodetect_traktor() -> str:
    from ..locate import find_traktor_collection

    found = find_traktor_collection()
    return str(found) if found else ""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("djmigrationkit — Rekordbox → Traktor metadata merge")
        self.resize(1150, 720)
        self._plan = None
        self._thread = None
        self._worker = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_source_box())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_tracks_tab(), "Tracks")
        self.tabs.addTab(self._build_playlists_tab(), "Playlists")
        root.addWidget(self.tabs, 1)

        root.addWidget(self._build_action_bar())
        self.statusBar().showMessage("Pick your collection.nml and Rekordbox source, then Scan.")

    # ---- top: source selection ------------------------------------------- #
    def _build_source_box(self) -> QWidget:
        box = QGroupBox("Source")
        form = QFormLayout(box)

        self.traktor_edit = QLineEdit(autodetect_traktor())
        tk_row = QHBoxLayout()
        tk_row.addWidget(self.traktor_edit)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse_traktor)
        tk_row.addWidget(b)
        tk_w = QWidget(); tk_w.setLayout(tk_row)
        form.addRow("Traktor collection.nml (read-only):", tk_w)

        self.rb_db_radio = QRadioButton("Rekordbox master.db (auto-detect)")
        self.rb_db_radio.setChecked(True)
        self.rb_xml_radio = QRadioButton("rekordbox.xml export")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_db_radio); grp.addButton(self.rb_xml_radio)
        self.rb_xml_edit = QLineEdit(); self.rb_xml_edit.setEnabled(False)
        xb = QPushButton("Browse…"); xb.clicked.connect(self._browse_xml)
        self.rb_xml_radio.toggled.connect(lambda on: (self.rb_xml_edit.setEnabled(on), xb.setEnabled(on)))
        xb.setEnabled(False)
        rb_row = QHBoxLayout()
        rb_row.addWidget(self.rb_db_radio); rb_row.addWidget(self.rb_xml_radio)
        rb_row.addWidget(self.rb_xml_edit); rb_row.addWidget(xb)
        rb_w = QWidget(); rb_w.setLayout(rb_row)
        form.addRow("Rekordbox source:", rb_w)

        opt_row = QHBoxLayout()
        self.grids_check = QCheckBox("Transfer beatgrids / BPM")
        self.grids_check.setChecked(True)
        self.default_res_combo = QComboBox()
        self.default_res_combo.addItems(["Rekordbox wins", "Traktor wins", "Merge"])
        opt_row.addWidget(self.grids_check)
        opt_row.addWidget(QLabel("Default on conflict:"))
        opt_row.addWidget(self.default_res_combo)
        opt_row.addStretch(1)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self._start_scan)
        opt_row.addWidget(self.scan_btn)
        opt_w = QWidget(); opt_w.setLayout(opt_row)
        form.addRow("Options:", opt_w)
        return box

    # ---- tracks tab ------------------------------------------------------- #
    def _build_tracks_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        filt = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Conflicts", "New cues", "Unmatched", "No change"])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText("Search artist / title…")
        self.search_edit.textChanged.connect(lambda t: self.proxy.set_text(t))
        filt.addWidget(QLabel("Show:")); filt.addWidget(self.filter_combo)
        filt.addWidget(self.search_edit, 1)
        for label, res in [("Cues: RB", Resolution.RB_WINS),
                           ("Cues: Traktor", Resolution.TRAKTOR_WINS),
                           ("Cues: Merge", Resolution.MERGE)]:
            btn = QPushButton(label)
            btn.setToolTip("Bulk-set cue resolution on all conflicts")
            btn.clicked.connect(lambda _=False, r=res: self._bulk(r))
            filt.addWidget(btn)
        for label, res in [("Grids: RB", Resolution.RB_WINS),
                           ("Grids: Traktor", Resolution.TRAKTOR_WINS)]:
            btn = QPushButton(label)
            btn.setToolTip("Bulk-set beatgrid resolution on all matched tracks")
            btn.clicked.connect(lambda _=False, r=res: self._bulk_grid(r))
            filt.addWidget(btn)
        lay.addLayout(filt)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableView()
        self.model = TrackTableModel()
        self.proxy = TrackFilterProxy(); self.proxy.setSourceModel(self.model)
        # Sort by a per-column key (UserRole) so numeric columns sort numerically
        # and text columns alphabetically; clicking a header toggles asc/desc.
        self.proxy.setSortRole(Qt.UserRole)
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setSortIndicatorShown(True)
        # Start in scan order; user clicks a header to sort.
        self.table.sortByColumn(-1, Qt.AscendingOrder)
        self.table.setColumnWidth(2, 280)
        self.table.selectionModel().selectionChanged.connect(self._on_select)
        split.addWidget(self.table)
        split.addWidget(self._build_detail_panel())
        split.setSizes([680, 420])
        lay.addWidget(split, 1)
        return w

    def _build_detail_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.detail_title = QLabel("Select a track")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.detail_title)

        res_box = QGroupBox("Resolution for this track")
        res_lay = QHBoxLayout(res_box)
        self.res_group = QButtonGroup(self)
        self.res_rb = QRadioButton("Rekordbox")
        self.res_tk = QRadioButton("Traktor")
        self.res_merge = QRadioButton("Merge")
        for b in (self.res_rb, self.res_tk, self.res_merge):
            self.res_group.addButton(b); res_lay.addWidget(b)
        self.res_rb.toggled.connect(lambda on: on and self._set_current_res(Resolution.RB_WINS))
        self.res_tk.toggled.connect(lambda on: on and self._set_current_res(Resolution.TRAKTOR_WINS))
        self.res_merge.toggled.connect(lambda on: on and self._set_current_res(Resolution.MERGE))
        lay.addWidget(res_box)

        cols = QHBoxLayout()
        tkcol = QVBoxLayout(); tkcol.addWidget(QLabel("Traktor cues (current)"))
        self.tk_cues = QTableWidget(); tkcol.addWidget(self.tk_cues)
        rbcol = QVBoxLayout(); rbcol.addWidget(QLabel("Rekordbox cues (incoming)"))
        self.rb_cues = QTableWidget(); rbcol.addWidget(self.rb_cues)
        cols.addLayout(tkcol); cols.addLayout(rbcol)
        lay.addLayout(cols, 1)

        grid_box = QGroupBox("Beatgrid for this track")
        grid_lay = QHBoxLayout(grid_box)
        self.grid_group = QButtonGroup(self)
        self.grid_rb = QRadioButton("Rekordbox")
        self.grid_tk = QRadioButton("Traktor")
        for b in (self.grid_rb, self.grid_tk):
            self.grid_group.addButton(b); grid_lay.addWidget(b)
        self.grid_rb.toggled.connect(lambda on: on and self._set_current_grid_res(Resolution.RB_WINS))
        self.grid_tk.toggled.connect(lambda on: on and self._set_current_grid_res(Resolution.TRAKTOR_WINS))
        lay.addWidget(grid_box)

        self.grid_label = QLabel("")
        self.grid_label.setWordWrap(True)
        lay.addWidget(self.grid_label)
        return w

    # ---- playlists tab ---------------------------------------------------- #
    def _build_playlists_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Select Rekordbox playlists to import (created under a "
                             "'Rekordbox Import' folder in Traktor):"))
        self.pl_tree = QTreeWidget()
        self.pl_tree.setHeaderLabels(["Playlist", "Tracks"])
        self._pl_leaf_items = []  # (QTreeWidgetItem, playlist_name)
        lay.addWidget(self.pl_tree, 1)
        row = QHBoxLayout()
        for label, fn in [("Select all", lambda: self._set_all_playlists(True)),
                          ("Select none", lambda: self._set_all_playlists(False))]:
            b = QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    # ---- bottom action bar ----------------------------------------------- #
    def _build_action_bar(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        self.summary_label = QLabel("")
        lay.addWidget(self.summary_label, 1)
        self.dryrun_btn = QPushButton("Dry-run report")
        self.dryrun_btn.clicked.connect(self._dry_run)
        self.dryrun_btn.setEnabled(False)
        self.apply_btn = QPushButton("Apply → collection-merge.nml")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setEnabled(False)
        lay.addWidget(self.dryrun_btn)
        lay.addWidget(self.apply_btn)
        return w

    # ---- browse handlers -------------------------------------------------- #
    def _browse_traktor(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select collection.nml", "", "NML (*.nml)")
        if p:
            self.traktor_edit.setText(p)

    def _browse_xml(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select rekordbox.xml", "", "XML (*.xml)")
        if p:
            self.rb_xml_edit.setText(p)

    # ---- scanning --------------------------------------------------------- #
    def _start_scan(self):
        traktor = self.traktor_edit.text().strip()
        if not traktor or not Path(traktor).exists():
            QMessageBox.warning(self, "Missing file", "Select a valid collection.nml first.")
            return
        rb_xml = self.rb_xml_edit.text().strip() if self.rb_xml_radio.isChecked() else None
        res = [Resolution.RB_WINS, Resolution.TRAKTOR_WINS, Resolution.MERGE][
            self.default_res_combo.currentIndex()]

        self.scan_btn.setEnabled(False)
        self._thread = QThread()
        self._worker = ScanWorker(traktor, rb_xml, self.grids_check.isChecked(), res)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda m: self.statusBar().showMessage(m))
        self._worker.finished.connect(self._on_scan_done)
        self._worker.failed.connect(self._on_scan_failed)
        self._thread.start()

    def _on_scan_done(self, plan, playlists):
        self._thread.quit(); self._thread.wait()
        self.scan_btn.setEnabled(True)
        self._plan = plan
        self.model.set_plan(plan)
        self._populate_playlists(playlists)
        s = plan.summary()
        self.summary_label.setText(
            f"{s['total']} RB tracks · {s['new_cues']} new · {s['conflict']} conflicts · "
            f"{s['unmatched']} unmatched · {s['no_change']} unchanged")
        self.statusBar().showMessage("Scan complete. Review and resolve, then Apply.")
        self.dryrun_btn.setEnabled(True)
        self.apply_btn.setEnabled(True)

    def _on_scan_failed(self, msg):
        self._thread.quit(); self._thread.wait()
        self.scan_btn.setEnabled(True)
        QMessageBox.critical(self, "Scan failed", msg)
        self.statusBar().showMessage("Scan failed.")

    # ---- selection / detail ---------------------------------------------- #
    def _current_row(self):
        idxs = self.table.selectionModel().selectedRows()
        if not idxs:
            return None
        return self.proxy.mapToSource(idxs[0]).row()

    def _on_select(self, *args):
        row = self._current_row()
        if row is None:
            return
        tc = self.model.track_change(row)
        self.detail_title.setText(f"{tc.rb_track.artist} — {tc.rb_track.title}\n"
                                  f"match: {tc.match_confidence} · status: {tc.change_type.value}")
        _fill_cue_table(self.tk_cues, tc.traktor_entry.cues if tc.traktor_entry else [])
        _fill_cue_table(self.rb_cues, tc.rb_track.cues)
        self.res_rb.setChecked(tc.resolution is Resolution.RB_WINS)
        self.res_tk.setChecked(tc.resolution is Resolution.TRAKTOR_WINS)
        self.res_merge.setChecked(tc.resolution is Resolution.MERGE)
        has_grid = tc.rb_track.beatgrid is not None
        self.grid_rb.setEnabled(has_grid)
        self.grid_tk.setEnabled(has_grid)
        self.grid_group.setExclusive(False)
        self.grid_rb.setChecked(has_grid and tc.grid_resolution is Resolution.RB_WINS)
        self.grid_tk.setChecked(has_grid and tc.grid_resolution is Resolution.TRAKTOR_WINS)
        self.grid_group.setExclusive(True)
        if tc.grid_warning:
            self.grid_label.setText("⚠ " + tc.grid_warning)
        elif tc.rb_track.beatgrid:
            bpm = tc.rb_track.beatgrid.dominant_bpm
            self.grid_label.setText(f"Beatgrid: {bpm:.2f} BPM "
                                    f"({'changed' if tc.grid_changed else 'same as Traktor'})")
        else:
            self.grid_label.setText("No Rekordbox beatgrid.")

    def _set_current_res(self, res):
        row = self._current_row()
        if row is not None:
            self.model.set_resolution(row, res)

    def _set_current_grid_res(self, res):
        row = self._current_row()
        if row is not None:
            self.model.set_grid_resolution(row, res)

    def _bulk(self, res):
        self.model.set_resolution_bulk(res, only_conflicts=True)

    def _bulk_grid(self, res):
        self.model.set_grid_resolution_bulk(res)

    def _apply_filter(self, text):
        mapping = {
            "All": None, "Conflicts": ChangeType.CONFLICT, "New cues": ChangeType.NEW_CUES,
            "Unmatched": ChangeType.UNMATCHED, "No change": ChangeType.NO_CHANGE,
        }
        self.proxy.set_type_filter(mapping.get(text))

    # ---- playlists -------------------------------------------------------- #
    def _populate_playlists(self, roots):
        self.pl_tree.clear()
        self._pl_leaf_items = []

        def add(parent, node):
            item = QTreeWidgetItem(parent)
            item.setText(0, node.name)
            if node.is_folder:
                item.setText(1, "")
                for c in node.children:
                    add(item, c)
            else:
                item.setText(1, str(len(node.track_ids)))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Unchecked)
                self._pl_leaf_items.append((item, node.name))

        for r in roots:
            add(self.pl_tree, r)
        self.pl_tree.expandToDepth(0)

    def _set_all_playlists(self, on):
        state = Qt.Checked if on else Qt.Unchecked
        for item, _name in self._pl_leaf_items:
            item.setCheckState(0, state)

    def _selected_playlist_names(self) -> set:
        return {name for item, name in self._pl_leaf_items
                if item.checkState(0) == Qt.Checked}

    # ---- dry run / apply -------------------------------------------------- #
    def _dry_run(self):
        if not self._plan:
            return
        s = self._plan.summary()
        from collections import Counter
        res_counts = Counter(tc.resolution for tc in self._plan.track_changes
                             if tc.change_type in (ChangeType.NEW_CUES, ChangeType.CONFLICT))
        msg = (f"Tracks: {s['total']}\n"
               f"  new cues: {s['new_cues']}\n  conflicts: {s['conflict']}\n"
               f"  unmatched: {s['unmatched']}\n  unchanged: {s['no_change']}\n\n"
               f"Resolutions: " + ", ".join(f"{k.value}={v}" for k, v in res_counts.items()) + "\n"
               f"Playlists selected: {len(self._selected_playlist_names())}\n"
               f"Transfer beatgrids: {self.grids_check.isChecked()}\n\n"
               "No file will be written.")
        QMessageBox.information(self, "Dry-run report", msg)

    def _apply(self):
        if not self._plan:
            return
        self._plan.playlist_plan.selected_names = self._selected_playlist_names()
        confirm = QMessageBox.question(
            self, "Apply merge",
            "This writes a NEW file 'collection-merge.nml' next to your collection.\n"
            "Your live collection.nml will NOT be modified.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self.apply_btn.setEnabled(False)
        self.statusBar().showMessage("Writing merge file ...")
        self._athread = QThread()
        self._aworker = ApplyWorker(self.traktor_edit.text().strip(), self._plan,
                                    self.grids_check.isChecked())
        self._aworker.moveToThread(self._athread)
        self._athread.started.connect(self._aworker.run)
        self._aworker.finished.connect(self._on_apply_done)
        self._aworker.failed.connect(self._on_apply_failed)
        self._athread.start()

    def _on_apply_done(self, result):
        self._athread.quit(); self._athread.wait()
        self.apply_btn.setEnabled(True)
        self.statusBar().showMessage(f"Wrote {result.output_path.name}")
        QMessageBox.information(
            self, "Merge written",
            f"Wrote: {result.output_path}\n\n"
            f"Entries updated: {result.entries_updated}\n"
            f"Cues written: {result.cues_written}\n"
            f"Grids written: {result.grids_written}\n"
            f"Playlists written: {result.playlists_written}\n\n"
            "NEXT STEPS (your live collection.nml was NOT modified):\n"
            "1. Close Traktor.\n"
            "2. Back up collection.nml, then rename the merge file over it\n"
            "   (or use Traktor's 'Import another Collection').\n"
            "3. Reopen Traktor and verify cues/grids.")

    def _on_apply_failed(self, msg):
        self._athread.quit(); self._athread.wait()
        self.apply_btn.setEnabled(True)
        QMessageBox.critical(self, "Apply failed", msg)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
