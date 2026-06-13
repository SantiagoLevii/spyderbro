"""Offline tests for the ScrapBro TUI pure logic.

The interactive prompt_toolkit dialogs need a TTY and are not exercised here;
these tests cover the side-effect-free helpers (language normalization, source
catalog, cookie validation, config defaults, summary builder, Windows compat).
"""
import json

import main
from ui import menus, theme


def test_language_selection_returns_valid_lang():
    assert menus.normalize_language("Español") == "es"
    assert menus.normalize_language("english") == "en"
    assert menus.normalize_language("whatever") == "en"
    assert menus.normalize_language("es") in ("en", "es")


def test_source_selection_returns_list():
    assert isinstance(menus.ALL_SOURCE_KEYS, list)
    assert menus.ALL_SOURCE_KEYS
    assert all(isinstance(s, str) for s in menus.ALL_SOURCE_KEYS)
    # every catalog source is a real, registered scraper
    assert all(s in main.SCRAPERS for s in menus.ALL_SOURCE_KEYS)


def test_cookie_validator_invalid_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    valid, _ = menus.validate_cookie_file(str(bad))
    assert valid is False


def test_cookie_validator_empty_file(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    valid, _ = menus.validate_cookie_file(str(empty))
    assert valid is False


def test_cookie_validator_missing_file():
    valid, msg = menus.validate_cookie_file("does/not/exist.json")
    assert valid is False
    assert "not found" in msg.lower()


def test_cookie_validator_valid_file(tmp_path):
    good = tmp_path / "cookies.json"
    good.write_text(json.dumps([{"name": "sessionid", "value": "x", "domain": ".site"}]),
                    encoding="utf-8")
    valid, msg = menus.validate_cookie_file(str(good))
    assert valid is True
    assert "1" in msg


def test_search_config_defaults():
    cfg = menus.default_search_config()
    assert cfg["limit"] == 50
    assert cfg["output"] == "csv"
    assert cfg["dateas_type"] == "ambos"
    assert cfg["dateas_lookup"] == "name"
    assert cfg["filter_has_phone"] is False
    assert cfg["filter_min_rating"] is None


def test_confirm_summary_contains_all_fields():
    cfg = menus.default_search_config()
    cfg.update(query="restaurantes", location="Buenos Aires", filter_has_phone=True)
    summary = menus.build_confirm_summary(cfg, ["google_maps", "dateas"], "en")
    for field in ("Sources", "Query", "Location", "Limit", "Output", "Filters"):
        assert field in summary
    assert "restaurantes" in summary
    assert "Dateas" in summary  # dateas line shown when dateas selected


def test_windows_compatibility():
    assert menus.init_windows_compat() is True


def test_theme_uses_matrix_green():
    from prompt_toolkit.styles import Style
    assert theme.GREEN == "#048c04"
    assert theme.ANSI_GREEN == "\033[38;2;4;140;4m"
    assert isinstance(theme.SCRAPBRO_STYLE, Style)
