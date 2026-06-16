from __future__ import annotations

from fastapi.testclient import TestClient

from test_stage29_webui_dashboard import _provider

from ptz_joystick_controller.webui import create_web_app


def _client() -> TestClient:
    return TestClient(create_web_app(_provider()))


def _assert_shared_nav(html: str) -> None:
    assert 'href="/">Dashboard</a>' in html
    assert 'href="/config">Config</a>' in html
    assert 'href="/diagnostics">Diagnostics</a>' in html


def test_dashboard_contains_shared_navigation_links() -> None:
    response = _client().get("/")
    assert response.status_code == 200
    _assert_shared_nav(response.text)


def test_config_contains_shared_navigation_links() -> None:
    response = _client().get("/config")
    assert response.status_code == 200
    _assert_shared_nav(response.text)


def test_diagnostics_contains_shared_navigation_links() -> None:
    response = _client().get("/diagnostics")
    assert response.status_code == 200
    _assert_shared_nav(response.text)


def test_diagnostics_layout_contains_wrapping_and_table_containers() -> None:
    response = _client().get("/diagnostics")
    html = response.text
    assert response.status_code == 200
    assert "overflow-wrap: anywhere" in html
    assert "word-break: break-word" in html
    assert "table-layout: fixed" in html
    assert "table-wrap" in html
    assert "&lt;span class" not in html


def test_dashboard_html_contains_all_ids_used_by_refresh_js() -> None:
    response = _client().get("/")
    html = response.text
    for element_id in ("title", "subtitle", "system", "joystick", "switcher", "ptz", "safety", "config", "cameras", "events"):
        assert f'id="{element_id}"' in html


def test_api_status_behavior_unchanged_after_layout_fix() -> None:
    client = _client()
    data = client.get("/api/status").json()
    assert {"joystick", "switcher", "ptz", "preview", "program", "active_ptz_camera", "uptime"}.issubset(data)
