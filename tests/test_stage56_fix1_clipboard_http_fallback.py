from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ptz_joystick_controller.webui import create_web_app
from test_stage29_webui_dashboard import _provider


def _html(tmp_path: Path) -> str:
    app = create_web_app(_provider(), config_local_path=tmp_path / "config.local.yaml")
    return TestClient(app).get("/config").text


def test_clipboard_helper_prefers_modern_api(tmp_path: Path) -> None:
    html = _html(tmp_path)
    assert "async function copyTextToClipboard(text)" in html
    assert "navigator.clipboard && typeof navigator.clipboard.writeText === 'function'" in html
    assert "await navigator.clipboard.writeText(value)" in html


def test_clipboard_fallback_uses_exec_command_when_modern_api_unavailable_or_fails(tmp_path: Path) -> None:
    html = _html(tmp_path)
    assert "document.createElement('textarea')" in html
    assert "document.execCommand('copy') === true" in html
    # The modern call is guarded and a rejected promise falls through to the legacy path.
    assert "catch (error)" in html


def test_clipboard_fallback_removes_temporary_element(tmp_path: Path) -> None:
    html = _html(tmp_path)
    assert "temporary.parentNode.removeChild(temporary)" in html


def test_copy_buttons_keep_exact_ip_and_ip_port_payloads(tmp_path: Path) -> None:
    html = _html(tmp_path)
    assert 'data-copy="${esc(row.ip)}">Copy IP</button>' in html
    assert 'const target = row.port == null ? row.ip : `${row.ip}:${row.port}`;' in html
    assert 'data-copy="${esc(target)}">Copy IP:Port</button>' in html
    assert "const text = button.dataset.copy || '';" in html


def test_copy_feedback_reports_success_and_failure(tmp_path: Path) -> None:
    html = _html(tmp_path)
    assert "copied ? 'Copied' : 'Copy failed'" in html
    assert "showCopyFeedback" in html


def test_clipboard_fix_does_not_submit_or_modify_config(tmp_path: Path) -> None:
    html = _html(tmp_path)
    clipboard_section = html[html.index("async function copyTextToClipboard"):html.index("fetch('/api/discovery/defaults")]
    assert "basicForm.submit" not in clipboard_section
    assert "fetch('/config" not in clipboard_section
    assert "fetch('/api/config/apply" not in clipboard_section
