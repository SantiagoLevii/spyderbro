import argparse
import logging
import re
from pathlib import Path

import pytest

from config.settings import settings, validate_settings

PROJECT_ROOT = Path(__file__).parent.parent

SECRET_PATTERN = re.compile(
    r'(?i)(api_key|apikey|api_token|secret|password|bearer)\s*[=:]\s*["\'][A-Za-z0-9_\-]{20,}["\']'
)
LONG_TOKEN_PATTERN = re.compile(r'["\'][A-Za-z0-9]{40,}["\']')


def _project_py_files() -> list[Path]:
    return [
        path for path in PROJECT_ROOT.rglob("*.py")
        if "venv" not in path.parts and ".pytest_cache" not in path.parts
    ]


def test_no_hardcoded_secrets():
    offenders = []
    for path in _project_py_files():
        content = path.read_text(encoding="utf-8", errors="ignore")
        if SECRET_PATTERN.search(content) or LONG_TOKEN_PATTERN.search(content):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert not offenders, f"Possible hardcoded secrets in: {offenders}"


def test_env_example_has_all_variables():
    settings_source = (PROJECT_ROOT / "config" / "settings.py").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    env_vars = re.findall(r'os\.getenv\("([A-Z_]+)"', settings_source)
    env_vars += re.findall(r'_env_(?:float|int)\("([A-Z_]+)"', settings_source)
    missing = [var for var in set(env_vars) if var not in env_example]
    assert not missing, f".env.example is missing: {missing}"


def test_gitignore_protects_env():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
    assert ".env.*" in gitignore
    assert "!.env.example" in gitignore


def test_no_secrets_in_logs(caplog, monkeypatch):
    fake_key = "sk-test-secret-key-abcdef1234567890"
    monkeypatch.setattr(settings, "SERPER_API_KEY", fake_key)

    from scrapers.dorks import DorksScraper

    with caplog.at_level(logging.DEBUG):
        validate_settings()
        DorksScraper()

    assert fake_key not in caplog.text


def test_input_validation_query():
    from main import validate_args

    args = argparse.Namespace(query="x", limit=10, source="google_maps")
    with pytest.raises(SystemExit) as excinfo:
        validate_args(args)
    assert excinfo.value.code == 1

    args = argparse.Namespace(query="", limit=10, source="google_maps")
    with pytest.raises(SystemExit):
        validate_args(args)


def test_input_validation_limit():
    from main import validate_args

    for bad_limit in (0, -5, 1001):
        args = argparse.Namespace(query="gyms in Miami", limit=bad_limit, source="google_maps")
        with pytest.raises(SystemExit) as excinfo:
            validate_args(args)
        assert excinfo.value.code == 1


def test_input_validation_source():
    from main import validate_args

    args = argparse.Namespace(query="gyms in Miami", limit=10, source="myspace")
    with pytest.raises(SystemExit) as excinfo:
        validate_args(args)
    assert excinfo.value.code == 1


def test_valid_input_passes():
    from main import validate_args

    args = argparse.Namespace(query="gyms in Miami", limit=10, source="google_maps,dorks")
    assert validate_args(args) == ["google_maps", "dorks"]


# --- Cookie credential hardening (Prompt O — OWASP fixes) ---------------------

def test_save_cookies_restricts_permissions(tmp_path, monkeypatch):
    """Saved cookie files are owner-only (no group/other access on POSIX)."""
    import os

    from utils import cookie_detector

    monkeypatch.setattr(cookie_detector, "COOKIE_DIR", tmp_path)
    path = cookie_detector.save_cookies(
        "instagram",
        [{"name": "sessionid", "value": "abc", "domain": ".instagram.com"}],
    )
    assert path.exists()
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_get_safe_proxy_url_redacts_password(monkeypatch):
    """PROXY_URL with a password is redacted; user and host survive."""
    monkeypatch.setattr(settings, "PROXY_URL", "http://user:secret@host:8080")
    safe = settings.get_safe_proxy_url()
    assert "secret" not in safe
    assert "***" in safe
    assert "user" in safe and "host" in safe


def test_get_safe_proxy_url_empty(monkeypatch):
    """No proxy -> empty string, never an error."""
    monkeypatch.setattr(settings, "PROXY_URL", "")
    assert settings.get_safe_proxy_url() == ""


def test_validate_cookie_domain_correct():
    """Instagram cookies pass validation for the instagram source."""
    from utils import cookie_detector

    ok, _msg = cookie_detector.validate_cookie_domain(
        "instagram", [{"name": "sessionid", "domain": ".instagram.com"}]
    )
    assert ok is True


def test_validate_cookie_domain_wrong():
    """Cookies from a different domain fail validation for instagram."""
    from utils import cookie_detector

    ok, _msg = cookie_detector.validate_cookie_domain(
        "instagram", [{"name": "x", "domain": ".facebook.com"}]
    )
    assert ok is False
