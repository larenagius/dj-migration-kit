"""Command-line driver for the Traktor -> Mixxx cue + playlist migration.

Examples:
    # Dry run: report what would migrate, write nothing.
    rb2traktor-mixxx --dry-run

    # Migrate cues + playlists into a safe copy (mixxxdb-merged.sqlite).
    rb2traktor-mixxx

The live mixxxdb.sqlite is never modified; output is a sibling copy you swap in
with Mixxx closed. Traktor collection and Mixxx DB are auto-detected if omitted.
"""

from __future__ import annotations

import argparse
import sys

from .locate import find_traktor_collection
from .mixxx_io import find_mixxx_db
from .mixxx_io.migrate import build_plan, migrate


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rb2traktor-mixxx", description=__doc__)
    ap.add_argument("--traktor", help="Path to Traktor collection.nml (else auto-detect)")
    ap.add_argument("--mixxx", help="Path to mixxxdb.sqlite (else auto-detect)")
    ap.add_argument("--no-memory", action="store_true", help="Skip Traktor memory cues")
    ap.add_argument("--no-playlists", action="store_true", help="Skip playlists")
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    args = ap.parse_args(argv)

    traktor = find_traktor_collection(args.traktor)
    if traktor is None:
        print("No Traktor collection.nml found. Pass --traktor or set RB2T_TRAKTOR.")
        return 2
    mixxx = args.mixxx or find_mixxx_db()
    if mixxx is None:
        print("No Mixxx database found. Pass --mixxx or set MIXXX_DB.")
        return 2

    print(f"Traktor: {traktor}")
    print(f"Mixxx:   {mixxx}")

    plan = build_plan(
        traktor, mixxx,
        include_memory=not args.no_memory,
        include_playlists=not args.no_playlists,
    )
    s = plan.summary()
    print("\n=== Migration plan ===")
    print(f"  Traktor tracks     : {s['traktor_tracks']}")
    print(f"  Mixxx tracks       : {s['mixxx_tracks']}")
    print(f"  matched (exact)    : {s['matched_exact']}")
    print(f"  matched (filename) : {s['matched_filename']}")
    print(f"  matched (fuzzy)    : {s['matched_fuzzy']}")
    print(f"  unmatched          : {s['unmatched']}")
    print(f"  tracks with cues   : {s['tracks_with_cues']}")
    print(f"  cues to write      : {s['cues']}")
    print(f"  playlists to write : {s['playlists']}")

    if s["mixxx_tracks"] == 0:
        print("\nYour Mixxx library is empty — add your music to Mixxx (and let it "
              "analyze) first, so cues have tracks to attach to.")

    if args.dry_run:
        print("\nDry run -- no file written.")
        return 0

    _, result = migrate(
        traktor, mixxx,
        include_memory=not args.no_memory,
        include_playlists=not args.no_playlists,
    )
    print(f"\nWrote merge database: {result.output_path}")
    print(f"  tracks with cues : {result.tracks_with_cues}")
    print(f"  cues written     : {result.cues_written}")
    print(f"  playlists written: {result.playlists_written} "
          f"({result.playlist_entries_written} entries)")
    print("\nNEXT STEPS (your live mixxxdb.sqlite was NOT modified):")
    print("  1. Close Mixxx.")
    print("  2. Back up your mixxxdb.sqlite, then rename the merge file over it")
    print(f"     (rename '{result.output_path.name}' -> 'mixxxdb.sqlite').")
    print("  3. Reopen Mixxx and check your hot cues / playlists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
