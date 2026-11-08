"""Export your Mixxx library (cues + playlists) to a rekordbox.xml for CDJ USBs.

Examples:
    # Preview what would be exported; writes nothing.
    djmk-mixxx-to-rekordbox --dry-run

    # Music on a NAS mounted at /mnt/nas on Linux, Rekordbox runs on a Mac that
    # mounts the same share at /Volumes/personal_folder:
    djmk-mixxx-to-rekordbox --map-path "/mnt/nas=/Volumes/personal_folder"

Your Mixxx database is only read, never modified. The XML is written as a new
file (default: mixxx-to-rekordbox.xml on your Desktop); an existing file is
never overwritten.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .mapping.path_remap import PathMapper, PathRuleError
from .mixxx_io import find_mixxx_db
from .mixxx_io.to_rekordbox import build_export, write_export
from .rekordbox_xml.writer import RekordboxXmlOverwriteError, resolve_output_path


def default_output_dir() -> Path:
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="djmk-mixxx-to-rekordbox", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mixxx", help="Path to mixxxdb.sqlite (else auto-detect / MIXXX_DB)")
    ap.add_argument("--out", help="Output file or folder (default: Desktop)")
    ap.add_argument("--map-path", action="append", metavar="FROM=TO", default=[],
                    help="Rewrite track paths for the Rekordbox machine; repeatable")
    ap.add_argument("--flat", action="store_true",
                    help="Keep playlist names as-is (don't rebuild 'A / B' into folders)")
    ap.add_argument("--hot-as-memory", action="store_true",
                    help="Also add a memory cue at every hot cue (shows on older CDJs' waveforms)")
    ap.add_argument("--no-playlists", action="store_true", help="Export the collection only")
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    args = ap.parse_args(argv)

    try:
        mapper = PathMapper.parse(args.map_path)
    except PathRuleError as e:
        print(e)
        return 2

    mixxx = Path(args.mixxx) if args.mixxx else find_mixxx_db()
    if mixxx is None or not Path(mixxx).exists():
        print("No Mixxx database found. Pass --mixxx or set MIXXX_DB.")
        return 2
    print(f"Mixxx library: {mixxx}")

    plan = build_export(mixxx, mapper, folders=not args.flat,
                        hot_as_memory=args.hot_as_memory,
                        include_playlists=not args.no_playlists)
    s = plan.stats
    print("\n=== Export plan ===")
    print(f"  tracks              : {s.tracks}")
    print(f"  tracks with cues    : {s.tracks_with_cues}")
    print(f"  hot cues (A-H)      : {s.hot_cues}")
    print(f"  memory cues         : {s.memory_cues}"
          + (f"  (incl. {s.hot_cues_demoted} from Mixxx pads 9+)" if s.hot_cues_demoted else ""))
    print(f"  loops               : {s.loops}")
    print(f"  playlists           : {s.playlists} in {s.folders} folders"
          f" ({s.playlist_entries} entries)")
    if s.crates:
        print(f"  crates              : {s.crates}")
    if s.cues_without_samplerate:
        print(f"  ! {s.cues_without_samplerate} cues skipped: their tracks were never "
              "analysed by Mixxx (no sample rate). Load them in Mixxx once and re-run.")
    if mapper.rules:
        print(f"  paths rewritten     : {s.tracks - s.paths_unmapped} / {s.tracks}")
        if s.paths_unmapped:
            print(f"  ! {s.paths_unmapped} paths matched no --map-path rule, e.g.:")
            for p in plan.unmapped_examples:
                print(f"      {p}")
    elif plan.tracks:
        print(f"  track paths kept as-is, e.g. {plan.tracks[0].path}")
        print("    (use --map-path FROM=TO if Rekordbox sees the music at a different path)")

    if args.dry_run:
        print("\nDry run - no file written.")
        return 0

    try:
        out = resolve_output_path(args.out, default_output_dir())
        written = write_export(plan, out)
    except RekordboxXmlOverwriteError as e:
        print(e)
        return 2

    print(f"\nWrote: {written}")
    print("\nNEXT STEPS in Rekordbox (Mac/Windows):")
    print("  1. Preferences > Advanced > Database > rekordbox xml > Imported Library:")
    print("     browse to the file above.")
    print("  2. Preferences > View > Layout: tick 'rekordbox xml' so it shows in the sidebar.")
    print("  3. In the sidebar open 'rekordbox xml' > Playlists, right-click a playlist")
    print("     (or folder) > Import Playlist.")
    print("  4. Spot-check a few tracks' cues, then Export to your USB as usual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
