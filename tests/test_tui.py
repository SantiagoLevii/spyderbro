"""Offline tests for the ScrapBro TUI pure logic.

The interactive prompt_toolkit dialogs need a TTY and are not exercised here;
these tests cover the side-effect-free helpers (language normalization, source
catalog, cookie validation, config defaults, summary builder, Windows compat).
"""
import json

import pytest

import main
from exporters.csv_exporter import build_output_filename
from models.lead import Lead
from ui import menus, progress, theme


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


def test_theme_exact_palette():
    assert theme.GREEN_PRIMARY == "#048c04"
    assert theme.GREEN_BRIGHT == "#05b304"
    assert theme.GREEN_DIM == "#025c02"
    assert theme.GREEN_FAINT == "#013a01"
    assert theme.YELLOW_WARN == "#886600"
    assert theme.RED_ERROR == "#880000"


# --- Multi-query parsing (Sprint K) -------------------------------------------

def test_parse_multi_query_single():
    assert main.parse_multi_query("inmobiliaria lujan") == ["inmobiliaria lujan"]


def test_parse_multi_query_double():
    assert main.parse_multi_query("inmobiliaria -- gomez") == ["inmobiliaria", "gomez"]


def test_parse_multi_query_strips_whitespace():
    assert main.parse_multi_query("  query1  --  query2  ") == ["query1", "query2"]


def test_parse_multi_query_skips_too_short():
    # Deuda 2: sub-queries under 2 chars are dropped.
    assert main.parse_multi_query("ab -- x -- valid query") == ["ab", "valid query"]


def test_parse_multi_query_raises_when_all_invalid():
    with pytest.raises(ValueError):
        main.parse_multi_query("a -- b")


# --- Session memory (Sprint N, Fix 8) -----------------------------------------

def test_session_memory_remembers_and_resets():
    menus.SessionMemory.reset()
    menus.SessionMemory.remember(sources=["google_maps"], config={"limit": 99, "workers": 8})
    assert menus.SessionMemory.last_sources == ["google_maps"]
    assert menus.SessionMemory.last_limit == 99
    assert menus.SessionMemory.last_workers == 8
    menus.SessionMemory.reset()
    assert menus.SessionMemory.last_limit == 50
    assert menus.SessionMemory.last_sources == []


# --- Live status labels (Sprint N, Fix 2/3) -----------------------------------

def test_status_label_includes_blocked_and_timeout():
    assert "blocked" in progress._status_label("blocked")
    assert "timeout" in progress._status_label("timeout")
    assert "partial" in progress._status_label("partial")


def test_render_live_shows_global_progress():
    state = progress.ScrapingState()
    state.set_source_status("google_maps", "done")
    state.set_source_status("zonaprop", "working")
    state.set_query("gyms", 1, 2)
    snap = state.snapshot()
    box = progress.render_live(snap, elapsed=5, lang="en")
    assert "1/2 done" in box  # one of two sources finished
    assert "Query 1/2" in box


# --- Filter warning on confirmation (Sprint N, Fix 7) -------------------------

def test_confirm_summary_warns_when_filters_active():
    cfg = menus.default_search_config()
    cfg.update(query="restaurantes", filter_has_phone=True)
    summary = menus.build_confirm_summary(cfg, ["google_maps"], "en")
    assert "⚠" in summary

    cfg_none = menus.default_search_config()
    cfg_none.update(query="restaurantes")
    summary_none = menus.build_confirm_summary(cfg_none, ["google_maps"], "en")
    assert "none" in summary_none.lower()
    assert "⚠" not in summary_none


# --- Output filename from query (Sprint K) ------------------------------------

def test_build_output_filename_simple():
    assert build_output_filename("inmobiliarias lujan") == "inmobiliarias_lujan.xlsx"


def test_build_output_filename_multi_query():
    assert build_output_filename("lujan -- gomez") == "lujan__gomez.xlsx"


def test_build_output_filename_special_chars():
    assert build_output_filename("gyms in Miami!") == "gyms_in_miami.xlsx"


# --- Output extension matches format (Prompt O, Fix 1) ------------------------

def test_build_output_filename_json_format():
    # JSON output must NEVER get an .xlsx extension.
    assert build_output_filename("restaurantes palermo", "json") == "restaurantes_palermo.json"


def test_build_output_filename_csv_format():
    assert build_output_filename("restaurantes palermo", "csv") == "restaurantes_palermo.xlsx"


def test_build_output_filename_multi_query_with_format():
    assert build_output_filename("a -- b", "csv") == "a__b.xlsx"
    assert build_output_filename("a -- b", "json") == "a__b.json"


# --- Emergency-save atexit handler (Prompt O, Fix 6) --------------------------

def test_atexit_handler_not_accumulated():
    import atexit

    import tui

    tui._register_emergency_save(progress.ScrapingState(), "a.xlsx", {"saved": True})
    first = tui._current_emergency_handler
    tui._register_emergency_save(progress.ScrapingState(), "b.xlsx", {"saved": True})
    second = tui._current_emergency_handler
    assert first is not second  # the new handler replaced the previous one
    atexit.unregister(second)
    tui._current_emergency_handler = None


# --- Pasted-cookie validation (Sprint K) --------------------------------------

def test_validate_cookie_json_valid():
    raw = json.dumps([{"name": "sessionid", "value": "x", "domain": ".instagram.com"}])
    valid, msg = menus.validate_cookie_json(raw)
    assert valid is True
    assert "1" in msg


def test_validate_cookie_json_invalid():
    valid, msg = menus.validate_cookie_json("not json at all")
    assert valid is False
    assert "JSON" in msg


def test_validate_cookie_json_empty():
    valid, _ = menus.validate_cookie_json("   ")
    assert valid is False


# --- Results table (Sprint K) -------------------------------------------------

def test_render_results_table_truncates_at_15():
    leads = [Lead(name=f"Lead{i:02d}", phone="+5491100000000", source="google_maps")
             for i in range(20)]
    table = progress.render_results_table(leads, max_rows=15)
    assert "Lead14" in table
    assert "Lead15" not in table
    assert "+5 more" in table


def test_render_results_table_empty_fields():
    # One lead has a phone, the other does not: the phone column is present, so
    # the contact-less row renders a dim em-dash.
    leads = [
        Lead(name="Con Tel", phone="+5491100000000", source="google_maps"),
        Lead(name="Sin Tel", source="google_maps"),
    ]
    table = progress.render_results_table(leads)
    assert "—" in table


def test_render_results_table_dynamic_columns():
    # Dateas-only leads: table still renders, with CUIT/DNI columns and no phone.
    leads = [Lead(name="Reg SA", source="dateas", raw_data={"cuit": "30-1-2", "dni": "123"})]
    table = progress.render_results_table(leads)
    assert "CUIT" in table
    assert "Teléfono" not in table
