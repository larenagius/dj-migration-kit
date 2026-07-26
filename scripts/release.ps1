<#
.SYNOPSIS
    Build the DJ Migration Kit GUI and publish a versioned release.

.DESCRIPTION
    One-command build + publish. Reads the version from pyproject.toml, runs the
    test suite (gate), builds the PyInstaller bundle on LOCAL disk (building onto a
    network share trips SMB file locks), zips it, then copies the app folder and
    zip to <ReleasesRoot>\djmigrationkit-<version>\.

    Run from anywhere; the script locates the repo from its own path.

.PARAMETER ReleasesRoot
    Where releases are published. Default: $env:DJMK_RELEASES, else <repo>\releases.

.PARAMETER Python
    python.exe used to build/test. Default: $env:DJMK_PYTHON, else <repo>\.venv,
    else 'python' on PATH.

.PARAMETER SkipTests
    Skip the pytest gate (not recommended).

.PARAMETER Force
    Overwrite an existing release of the same version.

.EXAMPLE
    # Set your machine's locations once (e.g. in your PowerShell profile):
    $env:DJMK_PYTHON   = "C:\path\to\venv\Scripts\python.exe"
    $env:DJMK_RELEASES = "Y:\some\releases"
    .\scripts\release.ps1
.EXAMPLE
    .\scripts\release.ps1 -ReleasesRoot D:\out -Force
#>
[CmdletBinding()]
param(
    # Defaults come from env vars; if unset they're resolved below to neutral,
    # machine-independent locations so this script ships clean in a public repo.
    [string]$ReleasesRoot = $env:DJMK_RELEASES,
    [string]$Python = $env:DJMK_PYTHON,
    [switch]$SkipTests,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# Run a native exe and return its exit code. Native tools (pyinstaller, pytest,
# robocopy) write progress to stderr; under $ErrorActionPreference='Stop' that
# would raise a bogus NativeCommandError. We drop to 'Continue' for the call and
# rely on the returned exit code instead.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [switch]$Quiet)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) { & $Exe @Arguments 2>&1 | Out-Null }
        else        { & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host $_ } }
    } finally { $ErrorActionPreference = $prev }
    return $LASTEXITCODE
}

# --- locate repo + tools ---------------------------------------------------- #
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Spec     = Join-Path $RepoRoot "packaging\djmigrationkit.spec"
$PyProj   = Join-Path $RepoRoot "pyproject.toml"
if (-not (Test-Path $Spec)) { Fail "Spec not found: $Spec" }

# Resolve Python: -Python / $DJMK_PYTHON, else a repo-local .venv, else PATH.
if (-not $Python) {
    $venvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path $venvPy) { $venvPy } else { "python" }
}
$resolved = Get-Command $Python -ErrorAction SilentlyContinue
if ($resolved) { $Python = $resolved.Source }
if (-not (Test-Path $Python)) { Fail "Python not found: '$Python' (set DJMK_PYTHON or create a .venv)" }

# Resolve releases root: -ReleasesRoot / $DJMK_RELEASES, else repo-local releases\.
if (-not $ReleasesRoot) { $ReleasesRoot = Join-Path $RepoRoot "releases" }

# pyinstaller next to python, else on PATH.
$PyDir       = Split-Path $Python -Parent
$PyInstaller = Join-Path $PyDir "pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    $pi = Get-Command pyinstaller -ErrorAction SilentlyContinue
    if ($pi) { $PyInstaller = $pi.Source } else { Fail "pyinstaller not found (pip install pyinstaller into your venv)" }
}

# --- read version ----------------------------------------------------------- #
$verMatch = Select-String -Path $PyProj -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $verMatch) { Fail "Could not read version from $PyProj" }
$Version = $verMatch.Matches[0].Groups[1].Value
Write-Host "DJ Migration Kit release v$Version" -ForegroundColor Green

$DestFolder = Join-Path $ReleasesRoot "dj-migration-kit-$Version"
$DestZip    = Join-Path $ReleasesRoot "dj-migration-kit-$Version.zip"
if ((Test-Path $DestFolder) -and -not $Force) {
    Fail "Release already exists: $DestFolder  (bump version in pyproject.toml, or pass -Force)"
}

# --- local working dirs (never build on the NAS) ---------------------------- #
$Work  = Join-Path $env:TEMP "djmk-build"
$Dist  = Join-Path $env:TEMP "djmk-dist"
$Stage = Join-Path $env:TEMP "djmk-stage\dj-migration-kit-$Version"
foreach ($d in @($Work, $Dist, (Split-Path $Stage -Parent))) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
}

# --- 1. tests --------------------------------------------------------------- #
if (-not $SkipTests) {
    Step "Running tests"
    Push-Location $RepoRoot
    $env:QT_QPA_PLATFORM = "offscreen"; $env:PYTHONPATH = "src"
    $code = Invoke-Native $Python @("-m", "pytest", "-q")
    Remove-Item Env:\QT_QPA_PLATFORM, Env:\PYTHONPATH -ErrorAction SilentlyContinue
    Pop-Location
    if ($code -ne 0) { Fail "Tests failed (exit $code). Release aborted." }
} else {
    Write-Host "Skipping tests (-SkipTests)." -ForegroundColor Yellow
}

# --- 2. build --------------------------------------------------------------- #
Step "Building (PyInstaller, local disk)"
$code = Invoke-Native $PyInstaller @($Spec, "--distpath", $Dist, "--workpath", $Work, "--noconfirm") -Quiet
if ($code -ne 0) { Fail "PyInstaller build failed (exit $code)." }
$BuiltApp = Join-Path $Dist "DJMigrationKit"
if (-not (Test-Path (Join-Path $BuiltApp "DJMigrationKit.exe"))) { Fail "Build produced no DJMigrationKit.exe" }

# --- 3. stage + readme + zip (local) ---------------------------------------- #
Step "Staging + zipping"
New-Item -ItemType Directory -Force (Split-Path $Stage -Parent) | Out-Null
$code = Invoke-Native "robocopy" @($BuiltApp, $Stage, "/E", "/R:1", "/W:2", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") -Quiet
if ($code -ge 8) { Fail "robocopy stage failed (exit $code)" }
@"
DJ Migration Kit $Version - sync cues/playlists between Traktor, Rekordbox and Mixxx
Run: double-click DJMigrationKit.exe (keep this whole folder together).
Source & docs: https://github.com/larenagius/dj-migration-kit
Your live libraries are NEVER modified; output is a separate merge file you swap in.
"@ | Set-Content -Encoding UTF8 (Join-Path $Stage "READ ME FIRST.txt")

$LocalZip = Join-Path $env:TEMP "djmk-stage\dj-migration-kit-$Version.zip"
if (Test-Path $LocalZip) { Remove-Item $LocalZip -Force }
Compress-Archive -Path $Stage -DestinationPath $LocalZip -Force

# --- 4. publish to NAS ------------------------------------------------------ #
Step "Publishing to NAS (may lag if the drive is asleep)"
New-Item -ItemType Directory -Force $ReleasesRoot | Out-Null
$code = Invoke-Native "robocopy" @($Stage, $DestFolder, "/E", "/R:2", "/W:5", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") -Quiet
if ($code -ge 8) { Fail "robocopy to NAS failed (exit $code)" }
Copy-Item $LocalZip $DestZip -Force

# --- 5. verify + cleanup ---------------------------------------------------- #
Step "Verifying"
if (-not (Test-Path (Join-Path $DestFolder "DJMigrationKit.exe"))) { Fail "Published exe missing on NAS!" }
$folderMB = [math]::Round((Get-ChildItem $DestFolder -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$zipMB    = [math]::Round((Get-Item $DestZip).Length / 1MB, 1)

foreach ($d in @($Work, $Dist, (Split-Path (Split-Path $Stage -Parent) -Parent))) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "`nPublished DJ Migration Kit v$Version" -ForegroundColor Green
Write-Host "  Folder: $DestFolder  ($folderMB MB)"
Write-Host "  Zip:    $DestZip  ($zipMB MB)"
Write-Host "  Run:    $DestFolder\DJMigrationKit.exe"
