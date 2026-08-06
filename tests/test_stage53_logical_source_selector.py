from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.webui import create_web_app
from ptz_joystick_controller.webui.config_editor import ConfigEditor, ConfigEditError
from test_stage29_webui_dashboard import _provider


def test_source_id_renders_as_select_and_current_value_is_preselected() -> None:
    html = TestClient(create_web_app(_provider())).get("/config").text
    assert "class='button-source-id'" in html
    assert "<select class='button-source-id'" in html
    assert "list='source-options'" not in html
    assert "name='button_button_3_source_id'" in html
    assert '<option value="Input 1" selected>Input 1</option>' in html


def test_preview_source_is_visible_and_other_actions_are_hidden() -> None:
    html = TestClient(create_web_app(_provider())).get("/config").text
    assert "class='button-source-cell' data-button-id='button_3'>" in html
    assert "class='button-source-cell' data-button-id='trigger' hidden>" in html
    assert "sourceCell.hidden = !showSource" in html
    assert "source.disabled = !showSource" in html


def test_osee_source_selector_contains_all_logical_sources() -> None:
    provider = _provider()
    provider.state.config = provider.state.config.model_copy(
        update={"switcher": provider.state.config.switcher.model_copy(update={"type": "osee_gostream_duet"})}
    )
    html = TestClient(create_web_app(provider)).get("/config").text
    expected = [*(f"Input {index}" for index in range(1, 9)), "MP1", "MP2", "M/SRC"]
    for source_id in expected:
        assert f'<option value="{source_id}"' in html
    assert 'const sourceOptionsBySwitcher = ' in html
    assert '"osee_gostream_duet": ["Input 1", "Input 2", "Input 3", "Input 4", "Input 5", "Input 6", "Input 7", "Input 8", "MP1", "MP2", "M/SRC"]' in html


def test_source_selectors_refresh_when_switcher_type_changes() -> None:
    html = TestClient(create_web_app(_provider())).get("/config").text
    assert "function refreshSourceSelectors()" in html
    assert "sourceOptionsBySwitcher[switcherType?.value]" in html
    assert "select.replaceChildren(...options.map" in html
    assert "refreshSourceSelectors(); updateSwitcherHints()" in html


def test_invalid_source_for_selected_switcher_is_rejected(tmp_path: Path) -> None:
    config = load_config("config.example.yaml", use_local=False)
    editor = ConfigEditor(config, Path("config.example.yaml"), tmp_path / "config.local.yaml")
    payload = editor.editable_payload()
    payload["switcher"]["type"] = "atem_mini_pro"
    payload["joystick"]["buttons"]["button_3"] = {"action": "preview_source", "source_id": "Input 5"}
    try:
        editor.save_patch(payload)
    except ConfigEditError as exc:
        assert "unsupported source_id" in str(exc)
    else:
        raise AssertionError("invalid source_id was accepted")
    assert not (tmp_path / "config.local.yaml").exists()


def test_configuration_serialization_remains_source_id_string(tmp_path: Path) -> None:
    config = load_config("config.example.yaml", use_local=False)
    editor = ConfigEditor(config, Path("config.example.yaml"), tmp_path / "config.local.yaml")
    payload = editor.editable_payload()
    payload["joystick"]["buttons"]["button_3"] = {"action": "preview_source", "source_id": "Input 4"}
    editor.save_patch(payload)
    saved = yaml.safe_load((tmp_path / "config.local.yaml").read_text())
    assert saved["joystick"]["buttons"]["button_3"] == {
        "action": "preview_source",
        "source_id": "Input 4",
    }
