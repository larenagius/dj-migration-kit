# Working on djmigrationkit

`main` is the stable branch — it should always be releasable. **No direct commits
to `main`.** All changes land via a short-lived branch that has passed tests.

## Workflow

```powershell
# 1. Start from an up-to-date main
git checkout main
git pull

# 2. Branch (one branch per iteration / feature / fix)
git checkout -b feat/cue-color-picker        # or fix/..., chore/..., docs/...

# 3. Make changes, then TEST (the gate — must pass)
$env:QT_QPA_PLATFORM = "offscreen"; $env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m pytest      # or your venv's python ($env:DJMK_PYTHON)

# 4. Commit + push the branch
git add -A
git commit -m "..."
git push -u origin feat/cue-color-picker

# 5. Merge to main ONLY when tested and green
#    Option A (preferred, keeps a record): open a Pull Request on GitHub and merge it.
#    Option B (quick, solo): merge locally with a merge commit, then push:
git checkout main
git merge --no-ff feat/cue-color-picker
git push

# 6. Delete the merged branch
git branch -d feat/cue-color-picker
git push origin --delete feat/cue-color-picker
```

## Branch names

| Prefix   | For                                  |
|----------|--------------------------------------|
| `feat/`  | new functionality                    |
| `fix/`   | bug fixes                            |
| `chore/` | tooling, deps, housekeeping          |
| `docs/`  | documentation only                   |

## The test gate

A branch may merge to `main` only when:
- `pytest` passes (unit + writer round-trip + headless GUI smoke).
- For changes that touch real-data behavior, `scripts/verify_e2e.py` still passes
  (it's read-only on the live `collection.nml`).

## Don't commit

- Real DJ libraries or merge output (`collection.nml`, `master.db`, `rekordbox.xml`,
  `collection-merge*.nml`) — already covered by `.gitignore`.
- Build artifacts / venvs / `dist/` — gitignored; releases go to your `<releases-dir>`.

## Cutting a release

Bump `version` in `pyproject.toml`, then run `scripts/release.ps1` (see
[docs/RELEASES.md](docs/RELEASES.md)). Do this from `main` after the work is merged.
