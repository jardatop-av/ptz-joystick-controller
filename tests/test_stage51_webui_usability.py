from __future__ import annotations

from fastapi.testclient import TestClient

from ptz_joystick_controller.webui import create_web_app
from test_stage29_webui_dashboard import _provider


def _client() -> TestClient:
    return TestClient(create_web_app(_provider()))


def test_dark_theme_is_default_on_all_main_pages() -> None:
    client = _client()
    for path in ("/", "/config", "/diagnostics"):
        html = client.get(path).text
        assert 'data-theme="dark"' in html
        assert 'color-scheme: dark' in html
        assert '--bg: #0b0d10' in html


def test_theme_toggle_and_local_storage_are_present_everywhere() -> None:
    client = _client()
    for path in ("/", "/config", "/diagnostics"):
        html = client.get(path).text
        assert 'id="theme-toggle"' in html
        assert "localStorage.getItem('ptz-theme')" in html
        assert "localStorage.setItem('ptz-theme', next)" in html


def test_common_navigation_marks_current_page() -> None:
    client = _client()
    cases = (("/", "Dashboard"), ("/config", "Config"), ("/diagnostics", "Diagnostics"))
    for path, label in cases:
        html = client.get(path).text
        assert f'<span class="active-page" aria-current="page"><a href="' in html
        assert f'>{label}</a></span>' in html


def test_config_has_save_controls_at_top_and_bottom() -> None:
    html = _client().get("/config").text
    assert 'data-action-bar="top"' in html
    assert 'data-action-bar="bottom"' in html
    assert html.count('name="apply" value="0">Save configuration</button>') == 2
    assert html.count('name="apply" value="1">Save and apply configuration</button>') == 2
    assert 'action="/config/basic" id="basic-config-form"' in html


def test_unsaved_changes_indicator_is_wired_to_form_edits_only() -> None:
    html = _client().get("/config").text
    assert 'id="unsaved-changes"' in html
    assert 'Unsaved changes' in html
    assert "basicForm.addEventListener('input', updateUnsavedState)" in html
    assert "basicForm.addEventListener('change', updateUnsavedState)" in html
    assert "key !== 'apply'" in html
    # Theme control is outside the basic form, so toggling it cannot dirty config.
    assert html.index('id="theme-toggle"') < html.index('id="basic-config-form"')


def test_diagnostics_uses_wide_layout_and_no_wrap_target() -> None:
    html = _client().get("/diagnostics").text
    assert 'class="diagnostics-container"' in html
    assert 'width: min(100%, 1880px)' in html
    assert '.visca-table { min-width: 1180px; }' in html
    assert '.visca-target { white-space: nowrap' in html
    assert 'overflow-x: auto' in html


def test_existing_form_backend_routes_are_unchanged() -> None:
    html = _client().get("/config").text
    assert 'method="post" action="/config/basic"' in html
    assert 'method="post" action="/config/raw"' in html
    assert 'Advanced YAML editor' in html
