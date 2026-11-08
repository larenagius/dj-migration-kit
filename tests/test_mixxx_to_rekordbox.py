"""Mixxx -> rekordbox.xml export: cue mapping, playlists, paths, safety."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from djmigrationkit.cli_mixxx_rb import main as cli_main
from djmigrationkit.mapping.path_remap import PathMapper, PathRuleError
from djmigrationkit.mixxx_io.to_rekordbox import build_export, write_export
from djmigrationkit.models import CueKind
from djmigrationkit.rb_reader.xml import RekordboxXmlReader
from djmigrationkit.rekordbox_xml.writer import (
    RekordboxXmlOverwriteError,
    path_to_location,
    resolve_output_path,
)

SR = 44100
PER_S = SR * 2  # Mixxx stores stereo samples


def _make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE track_locations (id INTEGER PRIMARY KEY, location TEXT,
            filesize INTEGER, fs_deleted INTEGER DEFAULT 0);
        CREATE TABLE library (id INTEGER PRIMARY KEY, artist TEXT, title TEXT,
            album TEXT, genre TEXT, year TEXT, tracknumber TEXT, key TEXT,
            bpm FLOAT, duration FLOAT, bitrate INTEGER, samplerate INTEGER,
            filetype TEXT, rating INTEGER DEFAULT 0, timesplayed INTEGER DEFAULT 0,
            datetime_added TEXT, location INTEGER, mixxx_deleted INTEGER DEFAULT 0);
        CREATE TABLE cues (id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER,
            type INTEGER, position INTEGER, length INTEGER DEFAULT 0,
            hotcue INTEGER DEFAULT -1, label TEXT DEFAULT '', color INTEGER);
        CREATE TABLE Playlists (id INTEGER PRIMARY KEY, name TEXT, position INTEGER,
            hidden INTEGER DEFAULT 0);
        CREATE TABLE PlaylistTracks (id INTEGER PRIMARY KEY, playlist_id INTEGER,
            track_id INTEGER, position INTEGER);
        CREATE TABLE crates (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE crate_tracks (crate_id INTEGER, track_id INTEGER);
        """
    )
    tracks = [
        (1, "/mnt/nas/Music/DJ Box/A & B - Song (Dub).mp3", "A & B", "Song", 125.0, SR, "mp3", 4),
        (2, "/mnt/nas/Music/DJ Box/Björk - Two.flac", "Björk", "Two", 128.0, SR, "flac", 0),
        (3, "/mnt/nas/Music/DJ Box/Deleted.mp3", "X", "Deleted", 120.0, SR, "mp3", 0),
        (4, "/mnt/nas/Music/DJ Box/Unanalysed.mp3", "Y", "Unanalysed", None, None, "mp3", 0),
        (5, "/home/me/elsewhere.mp3", "Z", "Elsewhere", 100.0, SR, "mp3", 0),
    ]
    for tid, loc, artist, title, bpm, sr, ft, rating in tracks:
        con.execute("INSERT INTO track_locations VALUES (?,?,?,0)", (tid, loc, 5_000_000))
        con.execute(
            "INSERT INTO library (id, artist, title, bpm, duration, samplerate, filetype,"
            " rating, key, datetime_added, location, mixxx_deleted)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, artist, title, bpm, 300.4, sr, ft, rating, "8A",
             "2026-07-26T10:00:00", tid, 1 if tid == 3 else 0))
    cues = [
        # track, type, position(samples), length, hotcue, label, color
        (1, 1, 10 * PER_S, 0, 0, "Intro", 0xFFFF0000),
        (1, 1, int(32.5 * PER_S), 0, 3, "Drop", 0xFF00FF00),
        (1, 1, 60 * PER_S, 0, 12, "Pad 13", 0xFF0000FF),  # beyond A-H -> memory
        (1, 4, 90 * PER_S, 4 * PER_S, 1, "Loop", 0xFFFFFF00),  # hot loop on B
        (1, 2, 10 * PER_S, 0, -1, "", 0xFFFF0000),  # main cue -> memory
        (1, 6, 5 * PER_S, 0, -1, "", 0),  # intro marker -> skipped
        (2, 1, 1 * PER_S, 0, 0, "", None),  # no colour
        (4, 1, 1 * PER_S, 0, 0, "", 0xFFFF0000),  # no samplerate -> skipped
    ]
    con.executemany("INSERT INTO cues (track_id, type, position, length, hotcue, label, color)"
                    " VALUES (?,?,?,?,?,?,?)", cues)
    con.executemany("INSERT INTO Playlists VALUES (?,?,?,?)", [
        (1, "Gigs - Played / 18.01", 1, 0),
        (2, "Gigs - Played / 18.02", 2, 0),
        (3, "Warmup", 3, 0),
        (4, "Auto DJ", 4, 1),  # hidden -> not exported
        (5, "History 1", 5, 2),
    ])
    con.executemany("INSERT INTO PlaylistTracks (playlist_id, track_id, position) VALUES (?,?,?)",
                    [(1, 2, 1), (1, 1, 2), (1, 3, 3), (2, 1, 1), (3, 2, 1), (4, 1, 1)])
    con.execute("INSERT INTO crates VALUES (1, 'Techno')")
    con.execute("INSERT INTO crate_tracks VALUES (1, 2)")
    con.commit()
    con.close()


@pytest.fixture
def mixxx_db(tmp_path):
    db = tmp_path / "mixxxdb.sqlite"
    _make_db(db)
    return db


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _export(mixxx_db, tmp_path, **kw):
    mapper = PathMapper.parse(["/mnt/nas=/Volumes/personal_folder"])
    plan = build_export(mixxx_db, mapper, **kw)
    out = write_export(plan, tmp_path / "out.xml")
    return plan, RekordboxXmlReader(out)


def test_cues_round_trip(mixxx_db, tmp_path):
    plan, rb = _export(mixxx_db, tmp_path)
    by_title = {t.title: t for t in rb.tracks()}
    assert "Deleted" not in by_title  # mixxx_deleted tracks are excluded

    song = by_title["Song"]
    hot = {c.hotcue_index: c for c in song.cues if c.kind is CueKind.HOT}
    assert hot[0].position_ms == pytest.approx(10_000, abs=1)
    assert hot[0].name == "Intro" and hot[0].color_rgb == (255, 0, 0)
    assert hot[3].position_ms == pytest.approx(32_500, abs=1)
    assert hot[3].color_rgb == (0, 255, 0)

    loops = [c for c in song.cues if c.kind is CueKind.LOOP]
    assert len(loops) == 1
    assert loops[0].position_ms == pytest.approx(90_000, abs=1)
    assert loops[0].length_ms == pytest.approx(4_000, abs=1)

    memory = sorted(c.position_ms for c in song.cues if c.kind is CueKind.MEMORY)
    assert memory == [pytest.approx(10_000, abs=1), pytest.approx(60_000, abs=1)]

    assert plan.stats.hot_cues == 3  # Song A + D, Two A
    assert plan.stats.hot_cues_demoted == 1
    assert plan.stats.loops == 1
    assert plan.stats.cues_without_samplerate == 1

    two = by_title["Two"]
    assert [c.color_rgb for c in two.cues] == [None]


def test_hot_as_memory_adds_memory_cues(mixxx_db, tmp_path):
    plan, rb = _export(mixxx_db, tmp_path, hot_as_memory=True)
    song = next(t for t in rb.tracks() if t.title == "Song")
    memory = sorted(round(c.position_ms) for c in song.cues if c.kind is CueKind.MEMORY)
    # main cue at 10s de-duplicates with hot cue A's memory copy
    assert memory == [10_000, 32_500, 60_000]


def test_playlists_rebuild_folders_and_skip_hidden(mixxx_db, tmp_path):
    plan, rb = _export(mixxx_db, tmp_path)
    top = rb.playlists()
    names = [p.name for p in top]
    assert names == ["Gigs - Played", "Warmup", "Mixxx Crates"]
    gigs = top[0]
    assert gigs.is_folder and [c.name for c in gigs.children] == ["18.01", "18.02"]
    # order kept, deleted track dropped
    tid = {t.title: t.rb_id for t in rb.tracks()}
    assert gigs.children[0].track_ids == [tid["Two"], tid["Song"]]
    assert top[2].children[0].name == "Techno"
    assert plan.stats.playlists == 3 and plan.stats.crates == 1


def test_flat_keeps_names(mixxx_db, tmp_path):
    _, rb = _export(mixxx_db, tmp_path, folders=False)
    assert [p.name for p in rb.playlists()][:2] == ["Gigs - Played / 18.01", "Gigs - Played / 18.02"]


def test_paths_are_remapped_and_url_encoded(mixxx_db, tmp_path):
    plan, rb = _export(mixxx_db, tmp_path)
    paths = {t.title: t.file_path for t in rb.tracks()}
    assert paths["Song"] == "/Volumes/personal_folder/Music/DJ Box/A & B - Song (Dub).mp3"
    assert paths["Two"].endswith("Björk - Two.flac")
    assert plan.stats.paths_unmapped == 1  # /home/me/elsewhere.mp3
    raw = (tmp_path / "out.xml").read_text(encoding="utf-8")
    assert "file://localhost/Volumes/personal_folder/Music/DJ%20Box/A%20%26%20B" in raw


def test_location_formats():
    assert path_to_location("/Volumes/x/a b.mp3") == "file://localhost/Volumes/x/a%20b.mp3"
    assert path_to_location("Y:\\Music\\a b.mp3") == "file://localhost/Y:/Music/a%20b.mp3"


def test_path_mapper_rules():
    m = PathMapper.parse(["Y:/Music=/mnt/nas/Music", "Y:/Music/DJ Box=/srv/dj"])
    assert m.map("y:\\Music\\DJ Box\\t.mp3") == ("/srv/dj/t.mp3", True)  # longest wins
    assert m.map("Y:/Music/other/t.mp3") == ("/mnt/nas/Music/other/t.mp3", True)
    assert m.map("Y:/Musician/t.mp3") == ("Y:/Musician/t.mp3", False)  # folder boundary
    with pytest.raises(PathRuleError):
        PathMapper.parse(["no-equals-sign"])


def test_output_safety(tmp_path):
    with pytest.raises(RekordboxXmlOverwriteError):
        resolve_output_path(tmp_path / "rekordbox.xml", tmp_path)
    first = resolve_output_path(None, tmp_path)
    assert first.name == "mixxx-to-rekordbox.xml"
    first.write_text("existing")
    second = resolve_output_path(None, tmp_path)
    assert second != first and second.name.startswith("mixxx-to-rekordbox-")


def test_cli_never_touches_mixxx_db(mixxx_db, tmp_path):
    before = _sha(mixxx_db)
    rc = cli_main(["--mixxx", str(mixxx_db), "--out", str(tmp_path),
                   "--map-path", "/mnt/nas=/Volumes/personal_folder"])
    assert rc == 0
    assert _sha(mixxx_db) == before
    assert (tmp_path / "mixxx-to-rekordbox.xml").exists()
    # second run must not overwrite the first
    assert cli_main(["--mixxx", str(mixxx_db), "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("mixxx-to-rekordbox*.xml"))) == 2


def test_cli_dry_run_writes_nothing(mixxx_db, tmp_path):
    assert cli_main(["--mixxx", str(mixxx_db), "--out", str(tmp_path), "--dry-run"]) == 0
    assert not list(tmp_path.glob("*.xml"))
