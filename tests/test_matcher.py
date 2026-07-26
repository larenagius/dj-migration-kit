from djmigrationkit.models import RbTrack, TraktorEntry
from djmigrationkit.matcher import TrackMatcher


def _tk(path, **kw):
    return TraktorEntry(file_path=path, **kw)


def _rb(path, **kw):
    return RbTrack(rb_id=kw.pop("rb_id", "1"), file_path=path, **kw)


def test_exact_path_match_wins():
    tk = _tk("C:/Music/a.mp3", file_size=100)
    m = TrackMatcher([tk]).match(_rb(r"C:\Music\a.mp3", file_size=100))
    assert m.confidence == "exact"
    assert m.traktor_entry is tk


def test_filename_plus_size_match_across_drives():
    tk = _tk("D:/DJ/a.mp3", file_size=12345)
    m = TrackMatcher([tk]).match(_rb("E:/Backup/a.mp3", file_size=12345))
    assert m.confidence == "filename"


def test_single_basename_without_size_is_filename_match():
    # Same file name on a different drive, no size info: basenames are effectively
    # unique, so accept it as a filename match (the common cross-drive case).
    tk = _tk("D:/DJ/a.mp3")
    m = TrackMatcher([tk]).match(_rb("E:/Backup/a.mp3"))
    assert m.confidence == "filename"
    assert m.traktor_entry is tk


def test_ambiguous_basename_without_size_not_matched():
    # Two different tracks share a name and we have no size to disambiguate.
    tk1 = _tk("D:/A/track01.mp3")
    tk2 = _tk("D:/B/track01.mp3")
    m = TrackMatcher([tk1, tk2]).match(_rb("E:/track01.mp3"))
    assert m.confidence == "none"


def test_basename_with_size_tolerance_bytes_vs_kb():
    # Traktor size normalized from KB*1024 differs from RB's exact bytes by <1KB.
    tk = _tk("D:/DJ/a.mp3", file_size=14611 * 1024)
    m = TrackMatcher([tk]).match(_rb("G:/a.mp3", file_size=14962435))
    assert m.confidence == "filename"


def test_fuzzy_metadata_match_within_duration_tolerance():
    tk = _tk("D:/x.mp3", title="Strobe", artist="deadmau5", duration_ms=600_000)
    m = TrackMatcher([tk]).match(
        _rb("E:/y.mp3", title="strobe", artist="Deadmau5", duration_ms=601_000)
    )
    assert m.confidence == "fuzzy"


def test_fuzzy_rejected_when_duration_too_far():
    tk = _tk("D:/x.mp3", title="Strobe", artist="deadmau5", duration_ms=600_000)
    m = TrackMatcher([tk]).match(
        _rb("E:/y.mp3", title="Strobe", artist="deadmau5", duration_ms=650_000)
    )
    assert m.confidence == "none"


def test_exact_preferred_over_fuzzy():
    exact = _tk("C:/Music/a.mp3", title="T", artist="A", file_size=1)
    other = _tk("Z:/elsewhere.mp3", title="T", artist="A")
    m = TrackMatcher([exact, other]).match(
        _rb(r"C:\Music\a.mp3", title="T", artist="A", file_size=1)
    )
    assert m.confidence == "exact" and m.traktor_entry is exact
