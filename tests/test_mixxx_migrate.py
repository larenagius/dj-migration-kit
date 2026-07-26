"""End-to-end Traktor -> Mixxx migration tests against a synthetic Mixxx DB."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from djmigrationkit.mapping import mixxx_cues
from djmigrationkit.mixxx_io import db as mdb
from djmigrationkit.mixxx_io.migrate import build_plan, migrate

FIX = Path(__file__).parent / "fixtures" / "collection_mixxx.nml"


def _make_mixxx_db(path: Path, tracks: list[dict]) -> None:
    """Create a minimal Mixxx DB with the tables our code reads/writes."""
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE track_locations (
            id INTEGER PRIMARY KEY, location varchar(512), filename varchar(512),
            directory varchar(512), filesize INTEGER, fs_deleted INTEGER DEFAULT 0);
        CREATE TABLE library (
            id INTEGER PRIMARY KEY, artist varchar(64), title varchar(64),
            duration FLOAT, samplerate INTEGER, channels INTEGER DEFAULT 2,
            location INTEGER, mixxx_deleted INTEGER DEFAULT 0);
        CREATE TABLE cues (
            id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER NOT NULL,
            type INTEGER DEFAULT 0 NOT NULL, position INTEGER DEFAULT -1 NOT NULL,
            length INTEGER DEFAULT 0 NOT NULL, hotcue INTEGER DEFAULT -1 NOT NULL,
            label TEXT DEFAULT '' NOT NULL, color INTEGER DEFAULT 4294901760 NOT NULL);
        CREATE TABLE Playlists (
            id INTEGER PRIMARY KEY, name varchar(48), position INTEGER,
            hidden INTEGER DEFAULT 0 NOT NULL, date_created datetime,
            date_modified datetime, locked INTEGER DEFAULT 0);
        CREATE TABLE PlaylistTracks (
            id INTEGER PRIMARY KEY, playlist_id INTEGER, track_id INTEGER,
            position INTEGER, pl_datetime_added datetime);
        """
    )
    for i, t in enumerate(tracks, start=1):
        cur.execute(
            "INSERT INTO track_locations (id, location, filename, filesize) VALUES (?,?,?,?)",
            (i, t["location"], Path(t["location"]).name, t.get("filesize", 0)),
        )
        cur.execute(
            "INSERT INTO library (id, artist, title, duration, samplerate, location) VALUES (?,?,?,?,?,?)",
            (i, t.get("artist", ""), t.get("title", ""), t.get("duration", 0.0),
             t["samplerate"], i),
        )
    con.commit()
    con.close()


@pytest.fixture
def mixxx_db(tmp_path):
    db = tmp_path / "mixxxdb.sqlite"
    _make_mixxx_db(db, [
        {"location": "D:/mixxxmusic/alpha.mp3", "title": "Alpha", "artist": "A",
         "samplerate": 44100, "duration": 200.0},
        {"location": "D:/mixxxmusic/beta.mp3", "title": "Beta", "artist": "B",
         "samplerate": 48000, "duration": 180.0},
    ])
    return db


def _cues(db_path):
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT track_id, type, position, hotcue, label, color FROM cues ORDER BY track_id, hotcue"
    ).fetchall()
    con.close()
    return rows


def test_position_formula_matches_mixxx():
    # seconds * samplerate * 2 (stereo samples), per Mixxx's own importer
    assert mixxx_cues.position_samples(1000, 44100) == 88200
    assert mixxx_cues.position_samples(60000, 48000) == 5_760_000


def test_color_is_argb():
    assert mixxx_cues.rgb_to_mixxx_color((0x28, 0xE2, 0x14)) == 0xFF28E214


def test_plan_matches_and_counts(mixxx_db):
    plan = build_plan(FIX, mixxx_db)
    s = plan.summary()
    assert s["mixxx_tracks"] == 2
    assert s["matched_filename"] == 2  # alpha/beta match by basename across drives
    assert s["unmatched"] == 0
    assert s["tracks_with_cues"] == 2
    # alpha: 2 hot + 1 memory(->slot2) = 3 ; beta: 1 hot
    assert s["cues"] == 4
    assert s["playlists"] == 1


def test_migrate_writes_cues_and_playlist(mixxx_db):
    plan, result = migrate(FIX, mixxx_db)
    assert result.output_path.name == "mixxxdb-merged.sqlite"
    assert result.cues_written == 4
    assert result.playlists_written == 1
    assert result.playlist_entries_written == 2

    rows = _cues(result.output_path)
    # alpha (track_id 1, sr 44100): slot0 @1000ms, slot1 @60000ms, memory->slot2 @30000ms
    alpha = {hot: (pos, label, color) for (tid, typ, pos, hot, label, color) in rows if tid == 1}
    assert alpha[0][0] == 88200 and alpha[0][1] == "Intro"
    assert alpha[0][2] == 0xFF28E214
    assert alpha[1][0] == 60000 / 1000 * 44100 * 2
    assert 2 in alpha and alpha[2][0] == 30000 / 1000 * 44100 * 2  # memory cue preserved
    # beta (track_id 2, sr 48000): slot0 @5000ms
    beta = {hot: pos for (tid, typ, pos, hot, label, color) in rows if tid == 2}
    assert beta[0] == 5000 / 1000 * 48000 * 2

    # playlist named by its folder path, both tracks in order
    con = sqlite3.connect(str(result.output_path))
    pl = con.execute("SELECT id, name FROM Playlists").fetchall()
    assert pl and pl[0][1] == "Gigs / Set 1"
    entries = con.execute(
        "SELECT track_id, position FROM PlaylistTracks WHERE playlist_id=? ORDER BY position",
        (pl[0][0],)).fetchall()
    con.close()
    assert entries == [(1, 1), (2, 2)]


def test_live_db_never_modified(mixxx_db):
    before = hashlib.sha256(mixxx_db.read_bytes()).hexdigest()
    before_mtime = mixxx_db.stat().st_mtime_ns
    migrate(FIX, mixxx_db)
    assert hashlib.sha256(mixxx_db.read_bytes()).hexdigest() == before
    assert mixxx_db.stat().st_mtime_ns == before_mtime


def test_refuses_to_write_live_name(tmp_path):
    live = tmp_path / "mixxxdb.sqlite"
    with pytest.raises(mdb.MixxxLiveWriteError):
        mdb._assert_not_live(live)


def test_no_memory_flag_skips_memory_cues(mixxx_db):
    plan = build_plan(FIX, mixxx_db, include_memory=False)
    # alpha now only 2 hot cues, beta 1 => 3 total
    assert plan.summary()["cues"] == 3


def test_rerun_does_not_clobber_previous_merge(mixxx_db):
    r1 = migrate(FIX, mixxx_db)[1]
    r2 = migrate(FIX, mixxx_db)[1]
    assert r1.output_path != r2.output_path
    assert r2.output_path.name.startswith("mixxxdb-merged-")
