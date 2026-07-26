"""Command-line driver for the Rekordbox -> Traktor merge.

Examples:
    # Dry run: read both libraries, print what would change, write nothing.
    djmigrationkit --traktor "C:/.../collection.nml" --dry-run

    # Produce collection-merge.nml next to the Traktor collection.
    djmigrationkit --traktor "C:/.../collection.nml" --rb-xml export.xml

By default the Rekordbox source is the local master.db (auto-detected). Pass
--rb-xml to use an exported rekordbox.xml instead. The live collection.nml is
never modified; output is always collection-merge.nml.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .matcher import TrackMatcher
from .models import ChangeType, Resolution
from .sync import engine
from .traktor_io.reader import TraktorCollection


def _load_rb(args):
    if args.rb_xml:
        from .rb_reader.xml import RekordboxXmlReader

        return RekordboxXmlReader(args.rb_xml)
    from .rb_reader.db import RekordboxDbReader

    return RekordboxDbReader(key=args.rb_key)


def build_plan_from_args(args):
    print(f"Reading Traktor collection: {args.traktor}")
    tk = TraktorCollection.load(args.traktor)
    entries = tk.entries()
    print(f"  {len(entries)} Traktor entries")

    print("Reading Rekordbox library ...")
    rb = _load_rb(args)
    rb_tracks = rb.tracks(limit=args.limit)
    print(f"  {len(rb_tracks)} Rekordbox tracks")

    matcher = TrackMatcher(entries)
    matches = matcher.match_all(rb_tracks)

    resolution = Resolution[args.conflict.upper()]
    plan = engine.build_plan(matches, default_resolution=resolution)
    return plan


def print_report(plan):
    s = plan.summary()
    print("\n=== Sync plan ===")
    print(f"  matched + unchanged : {s['no_change']}")
    print(f"  new cues (no conflict): {s['new_cues']}")
    print(f"  conflicts           : {s['conflict']}")
    print(f"  unmatched (RB only) : {s['unmatched']}")
    print(f"  total RB tracks     : {s['total']}")

    conflicts = [tc for tc in plan.track_changes if tc.change_type is ChangeType.CONFLICT]
    if conflicts:
        print("\n  Sample conflicts (first 10):")
        for tc in conflicts[:10]:
            print(f"   - {tc.rb_track.artist} - {tc.rb_track.title} "
                  f"[+{len(tc.cues_added)} ~{len(tc.cues_changed)} -{len(tc.cues_removed)}]"
                  f" match={tc.match_confidence}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="djmigrationkit", description=__doc__)
    ap.add_argument("--traktor", required=True, help="Path to live collection.nml (read-only)")
    ap.add_argument("--rb-xml", help="Path to exported rekordbox.xml (else use master.db)")
    ap.add_argument("--rb-key", help="SQLCipher key override for master.db")
    ap.add_argument("--conflict", default="rb_wins",
                    choices=["rb_wins", "traktor_wins", "merge"],
                    help="Default resolution when both sides have cues")
    ap.add_argument("--limit", type=int, default=None, help="Limit RB tracks (debug)")
    ap.add_argument("--no-grids", action="store_true", help="Do not transfer beatgrids")
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    args = ap.parse_args(argv)

    plan = build_plan_from_args(args)
    print_report(plan)

    if args.dry_run:
        print("\nDry run -- no file written.")
        return 0

    from .traktor_io.writer import apply_and_write

    result = apply_and_write(args.traktor, plan, transfer_grids=not args.no_grids)
    print(f"\nWrote merge file: {result.output_path}")
    print(f"  entries updated : {result.entries_updated}")
    print(f"  cues written    : {result.cues_written}")
    print(f"  grids written   : {result.grids_written}")
    print(f"  playlists written: {result.playlists_written}")
    print("\nNEXT STEPS (your live collection.nml was NOT modified):")
    print("  1. Close Traktor.")
    print(f"  2. Back up your collection.nml, then rename '{result.output_path.name}'")
    print("     to 'collection.nml' (or use Traktor's 'Import another Collection').")
    print("  3. Reopen Traktor and verify cues/grids.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
