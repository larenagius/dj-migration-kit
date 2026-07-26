# Releases

A built release is a folder + a zip:

```
<releases-dir>\
  djmigrationkit-<version>\          # the runnable app folder (djmigrationkit.exe + _internal\)
  djmigrationkit-<version>.zip       # zipped copy for distribution / backup
```

`<releases-dir>` is wherever you publish (set `DJMK_RELEASES`, else `<repo>\releases`).
`<version>` matches `project.version` in `pyproject.toml` (currently `0.1.0`).

## Running a release

Open `<releases-dir>\djmigrationkit-<version>\` and double-click `djmigrationkit.exe`.
Keep the whole folder together — the exe loads its `_internal\` siblings.

> **Cold-start note:** the app is ~170 MB of DLLs. If the release folder is on a
> network drive that was asleep, first launch can take a while to wake and load
> (and feel laggy). That's the drive spinning up, not the app hanging.

## Building a new release

### Automated (recommended) — GitHub Actions builds Windows + macOS

Push a version tag and the cloud does the rest:

```bash
# bump `version` in pyproject.toml first, then:
git tag v0.1.1
git push origin v0.1.1
```

The **Release** workflow (`.github/workflows/release.yml`) builds the app on
Windows, macOS (Apple Silicon, native) and macOS (Intel x86_64, cross-built under
Rosetta on the Apple Silicon runner — GitHub's Intel runners queue indefinitely),
zips each, and attaches them to the GitHub Release for that tag. No local build
needed, and no Mac required. The Intel build runs natively on Intel Macs and also
on Apple Silicon via Rosetta, so it's the safe pick for older machines.

> macOS note: the `.app` is **unsigned**, so first launch shows a Gatekeeper
> "unidentified developer" warning — right-click the app → **Open** → **Open**.
> (Proper notarization needs a paid Apple Developer account; can be added later.)

The **CI** workflow (`.github/workflows/ci.yml`) runs the test suite on all three
OSes on every push/PR.

### Local Windows build (optional)

You can still build a Windows release locally without CI:

1. Bump `version` in `pyproject.toml`.
2. (Optional) Point the script at your machine's locations via env vars:

```powershell
$env:DJMK_PYTHON   = "C:\path\to\venv\Scripts\python.exe"   # else uses <repo>\.venv or PATH
$env:DJMK_RELEASES = "Y:\some\releases"                      # else uses <repo>\releases
```

3. Run the one-command release script:

```powershell
.\scripts\release.ps1
```

It reads the version, runs the test suite (gate), builds the PyInstaller bundle on
**local disk** (building onto a network share trips SMB file locks), zips it, and
publishes the app folder + zip to `<releases-dir>\djmigrationkit-<version>\`. Flags:

- `-Force` — overwrite an existing release of the same version.
- `-SkipTests` — skip the pytest gate (not recommended).
- `-Python <path>` / `-ReleasesRoot <path>` — override the build python / output root.

All temp build dirs live under `%TEMP%` and are cleaned up automatically.

> **Why not build on a network share:** PyInstaller can't clean its temp output
> over SMB (file locks). The script always builds locally and copies the result.

## Notes

- The live `collection.nml` is never modified by the app; output is always
  `collection-merge.nml`. See the safety notes in the README.
- Release artifacts are git-ignored — they are not committed to the repo, only
  published to your `<releases-dir>`.
