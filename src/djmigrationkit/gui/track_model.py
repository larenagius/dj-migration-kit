"""Qt table model exposing a SyncPlan's track changes.

Separate from the domain models in djmigrationkit.models -- this is the view-model the
QTableView renders. It also owns per-row filtering and resolution edits.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel
from PySide6.QtGui import QColor

from ..models import ChangeType, Resolution, SyncPlan, TrackChange

COLUMNS = ["Status", "Artist", "Title", "Match", "Cue Δ", "Grid", "Resolution"]

STATUS_TEXT = {
    ChangeType.NO_CHANGE: "—",
    ChangeType.NEW_CUES: "+ new",
    ChangeType.CONFLICT: "⚠ conflict",
    ChangeType.UNMATCHED: "⊘ unmatched",
}

STATUS_COLOR = {
    ChangeType.NO_CHANGE: QColor(120, 120, 120),
    ChangeType.NEW_CUES: QColor(40, 160, 60),
    ChangeType.CONFLICT: QColor(200, 130, 0),
    ChangeType.UNMATCHED: QColor(170, 60, 60),
}

RES_TEXT = {
    Resolution.RB_WINS: "Rekordbox",
    Resolution.TRAKTOR_WINS: "Traktor",
    Resolution.MERGE: "Merge",
}


class TrackTableModel(QAbstractTableModel):
    def __init__(self, plan: SyncPlan | None = None):
        super().__init__()
        self._rows: list[TrackChange] = list(plan.track_changes) if plan else []

    def set_plan(self, plan: SyncPlan):
        self.beginResetModel()
        self._rows = list(plan.track_changes)
        self.endResetModel()

    def track_change(self, row: int) -> TrackChange:
        return self._rows[row]

    # Qt API ---------------------------------------------------------------- #
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        tc = self._rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return STATUS_TEXT[tc.change_type]
            if col == 1:
                return tc.rb_track.artist
            if col == 2:
                return tc.rb_track.title
            if col == 3:
                return tc.match_confidence
            if col == 4:
                if tc.change_type is ChangeType.UNMATCHED:
                    return ""
                return f"+{len(tc.cues_added)} ~{len(tc.cues_changed)} -{len(tc.cues_removed)}"
            if col == 5:
                if tc.rb_track.beatgrid is None:
                    return ""
                tag = RES_TEXT[tc.grid_resolution]
                if tc.grid_warning:
                    tag += " ⚠"
                elif tc.grid_changed:
                    tag += " ≠"
                return tag
            if col == 6:
                if tc.change_type in (ChangeType.UNMATCHED, ChangeType.NO_CHANGE):
                    return ""
                return RES_TEXT[tc.resolution]

        # Sort keys (the proxy sorts on Qt.UserRole): text columns sort
        # case-insensitively a-z; numeric columns sort numerically.
        if role == Qt.UserRole:
            if col == 0:
                order = {ChangeType.CONFLICT: 0, ChangeType.NEW_CUES: 1,
                         ChangeType.NO_CHANGE: 2, ChangeType.UNMATCHED: 3}
                return order.get(tc.change_type, 9)
            if col == 1:
                return (tc.rb_track.artist or "").casefold()
            if col == 2:
                return (tc.rb_track.title or "").casefold()
            if col == 3:
                return tc.match_confidence
            if col == 4:
                if tc.change_type is ChangeType.UNMATCHED:
                    return -1
                return len(tc.cues_added) + len(tc.cues_changed) + len(tc.cues_removed)
            if col == 5:
                if tc.rb_track.beatgrid is None:
                    return ""
                return RES_TEXT[tc.grid_resolution]
            if col == 6:
                if tc.change_type in (ChangeType.UNMATCHED, ChangeType.NO_CHANGE):
                    return ""
                return RES_TEXT[tc.resolution]

        if role == Qt.ForegroundRole and col == 0:
            return STATUS_COLOR.get(tc.change_type)

        if role == Qt.ToolTipRole and col == 5 and tc.grid_warning:
            return tc.grid_warning

        return None

    def set_resolution(self, row: int, resolution: Resolution):
        self._rows[row].resolution = resolution
        idx = self.index(row, 6)
        self.dataChanged.emit(idx, idx)

    def set_grid_resolution(self, row: int, resolution: Resolution):
        self._rows[row].grid_resolution = resolution
        idx = self.index(row, 5)
        self.dataChanged.emit(idx, idx)

    def set_grid_resolution_bulk(self, resolution: Resolution):
        changed = False
        for tc in self._rows:
            if tc.change_type is ChangeType.UNMATCHED:
                continue
            if tc.rb_track.beatgrid is not None:
                tc.grid_resolution = resolution
                changed = True
        if changed and self._rows:
            self.dataChanged.emit(self.index(0, 5), self.index(len(self._rows) - 1, 5))

    def set_resolution_bulk(self, resolution: Resolution, only_conflicts: bool = True):
        changed = False
        for tc in self._rows:
            if tc.change_type is ChangeType.UNMATCHED:
                continue
            if only_conflicts and tc.change_type is not ChangeType.CONFLICT:
                continue
            tc.resolution = resolution
            changed = True
        if changed and self._rows:
            self.dataChanged.emit(self.index(0, 6), self.index(len(self._rows) - 1, 6))


class TrackFilterProxy(QSortFilterProxyModel):
    """Filter rows by change type and a free-text query over artist/title."""

    def __init__(self):
        super().__init__()
        self._type_filter: ChangeType | None = None
        self._text = ""

    def set_type_filter(self, change_type: ChangeType | None):
        self._type_filter = change_type
        self.invalidate()

    def set_text(self, text: str):
        self._text = text.casefold().strip()
        self.invalidate()

    def filterAcceptsRow(self, source_row, source_parent):
        model: TrackTableModel = self.sourceModel()
        tc = model.track_change(source_row)
        if self._type_filter is not None and tc.change_type is not self._type_filter:
            return False
        if self._text:
            hay = f"{tc.rb_track.artist} {tc.rb_track.title}".casefold()
            if self._text not in hay:
                return False
        return True
