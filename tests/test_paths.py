from djmigrationkit.matcher import paths


def test_traktor_location_windows():
    p = paths.traktor_location_to_path("C:", "/:Users/:dj/:Music/:House/:", "track.mp3")
    assert p == "C:/Users/dj/Music/House/track.mp3"


def test_traktor_location_no_dir():
    p = paths.traktor_location_to_path("D:", "/:", "x.flac")
    assert paths.normalize(p) == "d:/x.flac"


def test_normalize_equates_slash_styles_and_case():
    a = paths.normalize(r"C:\Users\Dj\Music\Track.mp3")
    b = paths.normalize("C:/users/dj/music/track.mp3")
    assert a == b


def test_normalize_strips_file_url_and_decodes():
    a = paths.normalize("file:///C:/Users/dj/My%20Music/track%20one.mp3")
    b = paths.normalize(r"C:\Users\dj\My Music\track one.mp3")
    assert a == b


def test_normalize_collapses_double_slashes():
    assert paths.normalize("C://Users///dj//x.mp3") == "c:/users/dj/x.mp3"


def test_basename_key():
    assert paths.basename_key(r"C:\A\B\Song.MP3") == "song.mp3"


def test_rb_and_traktor_paths_converge():
    # The same physical file as each app would record it.
    rb = "C:/Users/dj/Music/House/banger.mp3"
    tk = paths.traktor_location_to_path("C:", "/:Users/:dj/:Music/:House/:", "banger.mp3")
    assert paths.normalize(rb) == paths.normalize(tk)
