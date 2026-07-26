"""End-to-end verification against the REAL libraries, writing only to a scratch
directory. Proves the live collection.nml is untouched and the merge is correct.

Run:
    python scripts/verify_e2e.py                 # auto-detect Traktor collection
    python scripts/verify_e2e.py <collection.nml># or pass it explicitly
Env overrides:
    DJMK_TRAKTOR   path to collection.nml (instead of auto-detect)
    DJMK_SCRATCH   scratch dir (default: a temp folder)
    VERIFY_LIMIT   limit RB tracks (debug);  VERIFY_GRIDS=0 to skip beatgrids
"""

import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from lxml import etree

from djmigrationkit.locate import find_traktor_collection
from djmigrationkit.matcher import TrackMatcher, paths
from djmigrationkit.models import ChangeType, CueKind
from djmigrationkit.rb_reader.db import RekordboxDbReader
from djmigrationkit.sync import engine
from djmigrationkit.traktor_io.reader import TraktorCollection
from djmigrationkit.traktor_io.writer import MergeWriter

LIVE = find_traktor_collection(sys.argv[1] if len(sys.argv) > 1 else None)
if LIVE is None:
    sys.exit("No Traktor collection.nml found. Pass it as an argument or set DJMK_TRAKTOR.")
SCRATCH = Path(os.environ.get("DJMK_SCRATCH") or (Path(tempfile.gettempdir()) / "djmigrationkit-scratch"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    scratch_src = SCRATCH / "collection.nml"
    shutil.copy(LIVE, scratch_src)

    live_sha_before = sha(LIVE)
    live_mtime_before = LIVE.stat().st_mtime_ns
    print(f"Live file sha256 before: {live_sha_before[:16]}...")

    print("Reading Traktor (scratch copy) ...")
    tk = TraktorCollection.load(scratch_src)
    entries = tk.entries()
    print(f"  {len(entries)} entries")

    # Env knobs for a fast self-test: VERIFY_LIMIT=400 VERIFY_GRIDS=0
    limit = int(os.environ.get("VERIFY_LIMIT", "0")) or None
    grids = os.environ.get("VERIFY_GRIDS", "1") != "0"
    print(f"Reading Rekordbox master.db (grids={grids}, limit={limit}) ...")
    t0 = time.time()
    rb = RekordboxDbReader()
    rb_tracks = rb.tracks(with_grid=grids, limit=limit)
    print(f"  {len(rb_tracks)} tracks in {time.time()-t0:.0f}s")

    matches = TrackMatcher(entries).match_all(rb_tracks)
    plan = engine.build_plan(matches)
    s = plan.summary()
    print(f"Plan: new={s['new_cues']} conflict={s['conflict']} "
          f"unmatched={s['unmatched']} unchanged={s['no_change']}")

    # pick a track we know has hot cues for spot-checking
    sample = next((tc for tc in plan.track_changes
                   if tc.traktor_entry is not None and tc.rb_track.hot_cues), None)

    print("Applying + writing merge to scratch ...")
    writer = MergeWriter(scratch_src)
    result = writer.apply(plan, transfer_grids=True).write()
    print(f"  wrote {result.output_path.name}: entries={result.entries_updated} "
          f"cues={result.cues_written} grids={result.grids_written} "
          f"playlists={result.playlists_written}")

    # ---- SAFETY: live file untouched ---- #
    assert sha(LIVE) == live_sha_before, "LIVE FILE CHANGED!"
    assert LIVE.stat().st_mtime_ns == live_mtime_before, "LIVE FILE MTIME CHANGED!"
    assert sha(scratch_src) == live_sha_before, "scratch source changed!"
    print("SAFETY OK: live collection.nml unchanged (sha + mtime).")

    # ---- merge reparses ---- #
    merged_tree = etree.parse(str(result.output_path), etree.XMLParser(huge_tree=True))
    # NOTE: only COLLECTION/ENTRY -- './/ENTRY' would also match playlist <ENTRY>.
    merged_entries = merged_tree.find("COLLECTION").findall("ENTRY")
    assert len(merged_entries) == len(entries), (
        f"entry count changed in merge! {len(merged_entries)} != {len(entries)}")
    print(f"REPARSE OK: {len(merged_entries)} COLLECTION entries, well-formed XML.")
    # also reparse through our reader
    TraktorCollection.load(result.output_path).entries()

    # ---- spot-check the sample track's cues ---- #
    if sample is not None:
        np = paths.normalize(sample.traktor_entry.file_path)
        found = None
        for e in merged_entries:
            loc = e.find("LOCATION")
            if loc is None:
                continue
            p = paths.traktor_location_to_path(loc.get("VOLUME",""), loc.get("DIR",""), loc.get("FILE",""))
            if paths.normalize(p) == np:
                found = e
                break
        assert found is not None, "sample track not found in merge"
        hot = [c for c in found.findall("CUE_V2")
               if c.get("TYPE") == "0" and c.get("HOTCUE") != "-1"]
        print(f"SPOT-CHECK: {sample.rb_track.artist} - {sample.rb_track.title}")
        print(f"  RB hot cues: {len(sample.rb_track.hot_cues)} | merge hot cues: {len(hot)}")
        for c in sorted(hot, key=lambda x: int(x.get('HOTCUE'))):
            print(f"    slot {c.get('HOTCUE')} start {float(c.get('START')):.0f}ms "
                  f"color {c.get('COLOR')}")
        assert len(hot) == len(sample.rb_track.hot_cues), "hot cue count mismatch!"

    # ---- re-run safety: second write should be timestamped ---- #
    result2 = MergeWriter(scratch_src).apply(plan, transfer_grids=True).write()
    assert result2.output_path != result.output_path, "second run overwrote first merge!"
    assert result2.output_path.name.startswith("collection-merge-"), result2.output_path.name
    print(f"RE-RUN OK: second merge written as {result2.output_path.name} (no overwrite).")

    print("\nALL VERIFICATION CHECKS PASSED.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("VERIFICATION FAILED:", e)
        sys.exit(1)
