from pathlib import Path

from utils.file_utils import ensure_dir, sanitize_filename


def test_sanitize_replaces_invalid_chars():
    assert sanitize_filename('gyms in Miami: "best"?') == "gyms_in_Miami_best"


def test_sanitize_handles_hashtags_and_handles():
    assert sanitize_filename("#gymmiami") == "gymmiami"
    assert sanitize_filename("@gymmiami") == "gymmiami"


def test_sanitize_truncates_long_names():
    assert len(sanitize_filename("x" * 300)) == 100


def test_sanitize_empty_falls_back():
    assert sanitize_filename("???") == "output"


def test_ensure_dir_creates_nested(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    result = ensure_dir(target)
    assert result == target
    assert target.is_dir()
    ensure_dir(target)
