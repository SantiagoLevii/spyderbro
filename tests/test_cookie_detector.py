"""Tests for the automatic browser cookie detector (Sprint L)."""
import json
from types import SimpleNamespace

from utils import cookie_detector


def test_detect_cookies_unsupported_source():
    ok, _msg, cookies = cookie_detector.detect_cookies("google_maps")
    assert ok is False
    assert cookies is None


def test_detect_cookies_no_browser_cookie3(monkeypatch):
    monkeypatch.setattr(cookie_detector, "BROWSER_COOKIE3_AVAILABLE", False)
    ok, msg, cookies = cookie_detector.detect_cookies("instagram")
    assert ok is False
    assert "not installed" in msg.lower()
    assert cookies is None


def test_cookiejar_to_list_filters_empty():
    jar = [
        SimpleNamespace(name="sessionid", value="abc", domain=".x.com", path="/", secure=True),
        SimpleNamespace(name="empty", value="", domain=".x.com", path="/", secure=False),
    ]
    result = cookie_detector._cookiejar_to_list(jar)
    assert len(result) == 1
    assert result[0]["name"] == "sessionid"


def test_save_cookies_creates_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cookie_detector, "COOKIE_DIR", tmp_path / "cookies")
    path = cookie_detector.save_cookies("instagram", [{"name": "a", "value": "b"}])
    assert path.exists()


def test_save_cookies_valid_json(tmp_path, monkeypatch):
    monkeypatch.setattr(cookie_detector, "COOKIE_DIR", tmp_path / "cookies")
    path = cookie_detector.save_cookies("facebook", [{"name": "a", "value": "b"}])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == [{"name": "a", "value": "b"}]
