"""Apply a SyncPlan to a Traktor collection and write a merge file.

Safety: this re-parses the *source* collection.nml fresh (read-only), mutates that
in-memory tree, and serializes the result to ``collection-merge.nml`` via
:mod:`safe_output`. The live file is never opened for writing. Re-parsing fresh
(rather than reusing the GUI's reader tree) keeps every run idempotent.

What it changes per matched entry, driven by the per-track Resolution:
  * cues   -- removes existing hot/memory CUE_V2 (TYPE != 4) and writes the
              resolved cue set (grid markers TYPE=4 are handled separately).
  * grid   -- when the resolution isn't TRAKTOR_WINS and the RB track has a
              beatgrid, replaces the AutoGrid CUE_V2 (TYPE=4) and the TEMPO BPM.
Playlists chosen in the plan are recreated under a dedicated folder so they never
collide with the user's existing Traktor playlist tree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from ..matcher import paths
from ..models import (
    ChangeType,
    CueKind,
    Resolution,
    RbPlaylist,
    SyncPlan,
)
from ..sync import engine
from ..mapping import cues as cue_map
from ..mapping import grid as grid_map
from . import safe_output

TK_TYPE_GRID = 4
IMPORT_FOLDER_NAME = "Rekordbox Import"


@dataclass
class WriteResult:
    output_path: Path
    entries_updated: int
    cues_written: int
    grids_written: int
    playlists_written: int


def traktor_entry_key(entry_el: etree._Element) -> str | None:
    """The Traktor track key used in playlist PRIMARYKEY: VOLUME + DIR + FILE."""
    loc = entry_el.find("LOCATION")
    if loc is None:
        return None
    return f'{loc.get("VOLUME", "")}{loc.get("DIR", "")}{loc.get("FILE", "")}'


def _entry_path(entry_el: etree._Element) -> str:
    loc = entry_el.find("LOCATION")
    if loc is None:
        return ""
    return paths.traktor_location_to_path(
        loc.get("VOLUME", ""), loc.get("DIR", ""), loc.get("FILE", "")
    )


class MergeWriter:
    def __init__(self, source_nml: str | Path):
        self.source_nml = Path(source_nml)
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        self._tree = etree.parse(str(self.source_nml), parser)
        self._root = self._tree.getroot()
        self._collection = self._root.find("COLLECTION")
        # index entries by normalized path AND traktor key for fast lookup
        self._by_path: dict[str, etree._Element] = {}
        self._by_key: dict[str, etree._Element] = {}
        for e in self._collection.findall("ENTRY"):
            np = paths.normalize(_entry_path(e))
            if np:
                self._by_path.setdefault(np, e)
            k = traktor_entry_key(e)
            if k:
                self._by_key.setdefault(k, e)

    # ---- per-entry mutation ---------------------------------------------- #
    def _apply_cues(self, entry_el: etree._Element, final_cues) -> int:
        # remove existing non-grid CUE_V2 (hot/memory/loop); keep grid (TYPE=4)
        for cue_el in entry_el.findall("CUE_V2"):
            if int(cue_el.get("TYPE", "0")) != TK_TYPE_GRID:
                entry_el.remove(cue_el)
        # write resolved hot + memory cues (loops are out of scope for v1)
        writable = [c for c in final_cues if c.kind in (CueKind.HOT, CueKind.MEMORY)]
        for el in cue_map.cues_to_elements(writable):
            entry_el.append(el)
        return len(writable)

    def _apply_grid(self, entry_el: etree._Element, beatgrid) -> bool:
        grid_el = grid_map.build_grid_cue_element(beatgrid)
        if grid_el is None:
            return False
        # remove existing grid markers
        for cue_el in entry_el.findall("CUE_V2"):
            if int(cue_el.get("TYPE", "0")) == TK_TYPE_GRID:
                entry_el.remove(cue_el)
        # grid marker goes first among CUE_V2 for tidiness
        first_cue = entry_el.find("CUE_V2")
        if first_cue is not None:
            first_cue.addprevious(grid_el)
        else:
            entry_el.append(grid_el)
        # update TEMPO
        bpm = grid_map.tempo_bpm(beatgrid)
        if bpm is not None:
            tempo = entry_el.find("TEMPO")
            if tempo is None:
                tempo = etree.SubElement(entry_el, "TEMPO")
            tempo.set("BPM", f"{bpm:.6f}")
            tempo.set("BPM_QUALITY", "100.000000")
        return True

    def _find_entry(self, tc) -> etree._Element | None:
        if tc.traktor_entry is None:
            return None
        np = paths.normalize(tc.traktor_entry.file_path)
        return self._by_path.get(np)

    # ---- playlists -------------------------------------------------------- #
    def _track_key_for_rb(self, tc) -> str | None:
        el = self._find_entry(tc)
        return traktor_entry_key(el) if el is not None else None

    def _build_playlists(self, plan: SyncPlan) -> int:
        pl_root = self._root.find("PLAYLISTS")
        if pl_root is None:
            return 0
        root_node = pl_root.find("NODE")
        if root_node is None:
            return 0
        root_subnodes = root_node.find("SUBNODES")
        if root_subnodes is None:
            root_subnodes = etree.SubElement(root_node, "SUBNODES")
            root_subnodes.set("COUNT", "0")

        # map RB content id -> traktor key (only for matched, selected tracks)
        rb_id_to_key: dict[str, str] = {}
        for tc in plan.track_changes:
            key = self._track_key_for_rb(tc)
            if key:
                rb_id_to_key[tc.rb_track.rb_id] = key

        selected = plan.playlist_plan.selected_names
        roots = plan.playlist_plan.roots
        if not roots:
            return 0

        import_folder = self._make_folder_node(IMPORT_FOLDER_NAME)
        count = [0]

        def add_node(parent_subnodes, rb_node: RbPlaylist):
            if rb_node.is_folder:
                folder = self._make_folder_node(rb_node.name)
                fsub = folder.find("SUBNODES")
                any_child = False
                for child in rb_node.children:
                    if add_node(fsub, child):
                        any_child = True
                if any_child:
                    parent_subnodes.append(folder)
                    self._bump(fsub)
                    return True
                return False
            # leaf playlist
            if selected and rb_node.name not in selected:
                return False
            keys = [rb_id_to_key[t] for t in rb_node.track_ids if t in rb_id_to_key]
            if not keys:
                return False
            parent_subnodes.append(self._make_playlist_node(rb_node.name, keys))
            count[0] += 1
            return True

        import_sub = import_folder.find("SUBNODES")
        added_any = False
        for r in roots:
            if add_node(import_sub, r):
                added_any = True
        if added_any:
            root_subnodes.append(import_folder)
            self._bump(import_sub)
            self._bump(root_subnodes)
        return count[0]

    def _make_folder_node(self, name: str) -> etree._Element:
        node = etree.Element("NODE")
        node.set("TYPE", "FOLDER")
        node.set("NAME", name)
        sub = etree.SubElement(node, "SUBNODES")
        sub.set("COUNT", "0")
        return node

    def _make_playlist_node(self, name: str, keys: list[str]) -> etree._Element:
        node = etree.Element("NODE")
        node.set("TYPE", "PLAYLIST")
        node.set("NAME", name)
        pl = etree.SubElement(node, "PLAYLIST")
        pl.set("ENTRIES", str(len(keys)))
        pl.set("TYPE", "LIST")
        pl.set("UUID", uuid.uuid4().hex)
        for k in keys:
            entry = etree.SubElement(pl, "ENTRY")
            pk = etree.SubElement(entry, "PRIMARYKEY")
            pk.set("TYPE", "TRACK")
            pk.set("KEY", k)
        return node

    @staticmethod
    def _bump(subnodes_el: etree._Element):
        n = len([c for c in subnodes_el if c.tag == "NODE"])
        subnodes_el.set("COUNT", str(n))

    # ---- top-level apply -------------------------------------------------- #
    def apply(self, plan: SyncPlan, transfer_grids: bool = True) -> "MergeWriter":
        """Mutate the in-memory tree per the plan.

        Args:
            transfer_grids: when True, RB beatgrids replace Traktor's (except where
                the per-track resolution is TRAKTOR_WINS). When False, grids are
                left untouched and only cues are migrated.
        """
        self._stats = WriteResult(Path(), 0, 0, 0, 0)
        for tc in plan.track_changes:
            if tc.change_type is ChangeType.UNMATCHED:
                continue
            entry_el = self._find_entry(tc)
            if entry_el is None:
                continue

            touched = False
            # cues: apply unless the user kept Traktor's side
            if tc.change_type in (ChangeType.NEW_CUES, ChangeType.CONFLICT):
                if tc.resolution is not Resolution.TRAKTOR_WINS:
                    final = engine.final_cues_for(tc)
                    self._stats.cues_written += self._apply_cues(entry_el, final)
                    touched = True

            # grid: global master toggle + independent per-track grid resolution
            if (
                transfer_grids
                and tc.rb_track.beatgrid is not None
                and tc.grid_resolution is not Resolution.TRAKTOR_WINS
            ):
                if self._apply_grid(entry_el, tc.rb_track.beatgrid):
                    self._stats.grids_written += 1
                    touched = True

            if touched:
                self._stats.entries_updated += 1

        self._stats.playlists_written = self._build_playlists(plan)
        return self

    def write(self) -> WriteResult:
        out = safe_output.resolve_output_path(self.source_nml)
        data = etree.tostring(self._tree, xml_declaration=True, encoding="UTF-8", standalone=False)
        safe_output.atomic_write_bytes(out, data)
        self._stats.output_path = out
        return self._stats


def apply_and_write(source_nml: str | Path, plan: SyncPlan, transfer_grids: bool = True) -> WriteResult:
    """Convenience: apply a plan to the source collection and write the merge file."""
    return MergeWriter(source_nml).apply(plan, transfer_grids=transfer_grids).write()
