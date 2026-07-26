# DJ Migration Kit

[![CI](https://github.com/larenagius/dj-migration-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/larenagius/dj-migration-kit/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/larenagius/dj-migration-kit)](https://github.com/larenagius/dj-migration-kit/releases/latest)

**Keep your DJ libraries in sync.** Move hot cues, memory cues, beatgrids and
playlists between **Traktor**, **Rekordbox** and **Mixxx** — pick your primary
tool, choose which others to sync to, and go.

Your live libraries are **never modified**: every run writes a separate merge file
you swap in yourself, so a bad sync can't break years of DJ prep.

## Note from Laren
As a DJ I have relied on Traktor for many years and this became my mian library and setup, however over the years, rekordox became the go to standard for clubs, hence I ended with two seperate librarys, the club and the home one. The problem with that was that the rekordbox collection was always a bit ahead as it had the cuepoint that have been capture while playing live and if I wanted to recreate these, it always ended being a manual process that I never ended doing. So thanks to Claude Code, I was able to build a lightweight app that can read the rekordbox library, and move playlists, cues, beatgrid metadata over to traktor. 

For this first iteration, this only works of Windows as it solved my problem and when I play with my Mac, I just sync everything to my Windows machine at the end of the gig. However I do intend to make a Mac version of this as I know most of the users are on the mac ecosystem.

As an app, this will remain free for use by anyone and if you have any feedback, please let me know and I will try to make the changes. 

Enjoy the music
Cheers
Laren

# Overview

## The #1 safety rule

**Your live `collection.nml` is never modified.** Every run writes a *new* file
`collection-merge.nml` next to it (the full merged collection). You swap it in
manually after reviewing. A bad merge can never break your live library.

## What it does

- Reads Rekordbox from the encrypted **`master.db`** (auto-detects the SQLCipher
  key via `pyrekordbox`) or, as a fallback, an exported **`rekordbox.xml`**.
- Reads your Traktor **`collection.nml`** (read-only).
- **Matches** tracks even when the two libraries live on different drives
  (e.g. Rekordbox on `G:` Google Drive, Traktor on `Y:`): exact path → filename +
  size → fuzzy title/artist/duration.
- **Translates** metadata faithfully:
  - hot cues (slots A–H), memory cues, with **cue colors** (RB palette → Traktor `#RRGGBB`)
  - beatgrids → Traktor `AutoGrid` + `TEMPO` (warns on multi-tempo tracks)
  - playlists/folders → a `Rekordbox Import` folder in Traktor
- **Conflict resolution** when both apps already have cues for a track: choose
  per-track *Rekordbox wins / Traktor wins / Merge*, with bulk actions.
- **Beatgrids are resolved independently of cues** — per track you can take the
  Rekordbox grid while keeping your Traktor cues (or vice-versa), with bulk
  "Grids: RB / Traktor" actions and a global on/off master toggle.

## Download (no Python needed)

Grab the latest **`djmigrationkit-<version>.zip`** from the
[**Releases page**](https://github.com/larenagius/rekordbox-to-traktor/releases/latest),
unzip it anywhere, and double-click `djmigrationkit.exe`.

Keep the whole folder together — the `.exe` loads its `_internal\` siblings.
Windows only for now; ~170 MB unzipped, and the first launch can take a few
seconds. See [docs/RELEASES.md](docs/RELEASES.md) for how releases are built.

## Install (for running from source / development)

```bash
python -m venv .venv
.venv\Scripts\pip install -e .          # add [dev] for tests: pip install -e ".[dev]"
```

Requires Python 3.10–3.14.

## Use — GUI (recommended)

```bash
.venv\Scripts\python -m djmigrationkit.gui.app
```

> Build the standalone `.exe` yourself with:
> `pyinstaller packaging/djmigrationkit.spec --distpath <out> --workpath <tmp> --noconfirm`
> (build to a **local disk** path, not a network share, to avoid file-lock errors).

1. The Traktor `collection.nml` is auto-detected (newest `Traktor */collection.nml`).
2. Leave the Rekordbox source on **master.db (auto-detect)**, or pick a `rekordbox.xml`.
3. **Scan**. Review the track table; the right panel shows Traktor vs Rekordbox cues.
4. Resolve any ⚠ conflicts (per-track cue radios / bulk "Cues:" buttons).
5. Choose beatgrid source per track (the "Beatgrid for this track" radios) or in
   bulk ("Grids: RB / Traktor"). The Grid column shows each track's choice.
6. Optionally tick playlists to import on the **Playlists** tab.
7. **Apply → collection-merge.nml**, then follow the on-screen swap-in steps.

## Use — CLI

```bash
# Dry run: report only, write nothing
djmigrationkit --traktor "C:/Users/you/Documents/Native Instruments/Traktor 4.5.0/collection.nml" --dry-run

# Write collection-merge.nml (Rekordbox wins on conflicts; grids included)
djmigrationkit --traktor ".../collection.nml" --conflict rb_wins

# From an exported XML instead of master.db, and without touching grids
djmigrationkit --traktor ".../collection.nml" --rb-xml export.xml --no-grids
```

## Swapping the merge in

1. **Close Traktor** (it rewrites `collection.nml` on exit).
2. Back up your `collection.nml`.
3. Rename `collection-merge.nml` → `collection.nml`, **or** use Traktor's
   *File → Import Collection* against the merge file.
4. Reopen Traktor; verify cues and grids.

## Also: Traktor → Mixxx (hot cues + playlists)

Migrate your Traktor hot cues (positions, colors, labels) and playlists into
[Mixxx](https://mixxx.org). Mixxx's library is a plain SQLite database, so this
writes directly into a **safe copy** of it — your live `mixxxdb.sqlite` is never
touched.

```bash
# Dry run: report what would migrate, write nothing
djmigrationkit-mixxx --dry-run

# Migrate into a copy (mixxxdb-merged.sqlite next to your live DB)
djmigrationkit-mixxx                     # auto-detects Traktor collection + Mixxx DB
djmigrationkit-mixxx --no-memory         # skip Traktor memory cues
djmigrationkit-mixxx --no-playlists      # cues only
```

First **add your music to Mixxx** (so tracks exist to attach cues to). Then, to
apply: **close Mixxx**, back up `mixxxdb.sqlite`, and rename the merge file over
it. Notes: Traktor hot cues become Mixxx hotcues (memory cues fill spare slots);
beatgrids aren't migrated (Mixxx uses a different mechanism); positions use
Mixxx's exact `seconds × samplerate × 2` sample convention.

## How it works

```
RB master.db / xml ─┐
                    ├─ matcher ─ engine(build SyncPlan) ─ GUI review ─ writer ─ collection-merge.nml
Traktor collection ─┘                                                   (live file read-only)
```

Layers (in `src/djmigrationkit/`): `rb_reader/` (db, xml) · `traktor_io/` (reader,
writer, `safe_output`) · `matcher/` · `mapping/` (cues, grid, colors, playlists) ·
`sync/` (engine, conflicts) · `gui/`.

The writer is the single point that can emit files, and it physically refuses any
path named `collection.nml` (`safe_output.assert_not_live`).

## Tests

```bash
.venv\Scripts\python -m pytest                      # unit + writer + GUI smoke
.venv\Scripts\python scripts/verify_e2e.py          # real-data end-to-end (read-only on live file)
```

## Scope

- **Rekordbox → Traktor:** hot/memory cues, beatgrids/BPM, playlists (GUI + CLI).
- **Traktor → Mixxx:** hot cues (+ memory cues) and playlists (CLI: `djmigrationkit-mixxx`).
- Out: loops, ratings/key/comments, writing back to Rekordbox, Mixxx beatgrids,
  adding new tracks (unmatched tracks are reported, not inserted), auto-sync daemon.

## Credits

- [pyrekordbox](https://github.com/dylanljones/pyrekordbox) — master.db / ANLZ access
- Rekordbox cue color palette from [Deep-Symmetry/beat-link #51](https://github.com/Deep-Symmetry/beat-link/issues/51)
- Mixxx cue format from [mixxxdj/mixxx PR #1411](https://github.com/mixxxdj/mixxx/pull/1411) (Traktor cue import)
