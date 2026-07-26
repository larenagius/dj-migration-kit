import hashlib
from pathlib import Path

import pytest

from djmigrationkit.traktor_io import safe_output as so


def test_refuses_live_filename(tmp_path):
    live = tmp_path / "collection.nml"
    with pytest.raises(so.LiveFileWriteError):
        so.assert_not_live(live)
    with pytest.raises(so.LiveFileWriteError):
        so.atomic_write_text(live, "data")


def test_resolve_output_path_is_merge_sibling(tmp_path):
    live = tmp_path / "collection.nml"
    live.write_text("orig")
    out = so.resolve_output_path(live)
    assert out.name == "collection-merge.nml"
    assert out.parent == live.parent


def test_resolve_output_path_timestamps_when_merge_exists(tmp_path):
    live = tmp_path / "collection.nml"
    (tmp_path / "collection-merge.nml").write_text("previous")
    out = so.resolve_output_path(live)
    assert out.name.startswith("collection-merge-")
    assert out.name.endswith(".nml")


def test_atomic_write_does_not_touch_live(tmp_path):
    live = tmp_path / "collection.nml"
    live.write_text("LIVE-CONTENT")
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    before_mtime = live.stat().st_mtime_ns

    out = so.resolve_output_path(live)
    so.atomic_write_text(out, "merged content")

    # live file untouched
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before
    assert live.stat().st_mtime_ns == before_mtime
    # output written, no leftover temp
    assert out.read_text() == "merged content"
    assert not (tmp_path / (out.name + ".tmp")).exists()
