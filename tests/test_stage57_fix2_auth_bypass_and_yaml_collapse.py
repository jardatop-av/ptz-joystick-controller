from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app
from ptz_joystick_controller.webui.auth import AuthStore


def _provider() -> RuntimeStatusProvider:
    return RuntimeStatusProvider(AppState(load_config("config.example.yaml", use_local=False)))


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    auth_file = tmp_path / "config.auth.yaml"
    app = create_web_app(_provider(), auth_file_path=auth_file, auth_enabled=True)
    return TestClient(app), auth_file


def _csrf_from_config(client: TestClient) -> str:
    html = client.get("/config").text
    marker = 'name="csrf_token" value="'
    return html.split(marker, 1)[1].split('"', 1)[0]


def test_missing_auth_file_still_requires_first_run_setup(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_configured_empty_password_bypasses_login_and_protects_hash(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    store = AuthStore(auth_file)
    store.set_password("")
    assert store.authentication_disabled is True
    text = auth_file.read_text(encoding="utf-8")
    assert "$argon2id$" in text
    assert "admin_password_hash:" in text

    assert client.get("/").status_code == 200
    assert client.get("/config").status_code == 200
    assert client.get("/diagnostics").status_code == 200
    assert client.get("/api/status").status_code == 200
    login = client.get("/login", follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/"


def test_empty_password_setup_immediately_enters_auth_disabled_mode(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    response = client.post(
        "/setup",
        data={"new_password": "", "confirm_password": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert AuthStore(auth_file).authentication_disabled
    assert client.get("/").status_code == 200


def test_logout_hidden_when_authentication_disabled(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("")
    for path in ("/", "/config", "/diagnostics"):
        html = client.get(path).text
        assert 'action="/logout"' not in html
        assert ">Logout<" not in html


def test_sessionless_csrf_allows_config_write_but_rejects_bad_token(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("")
    assert client.post("/api/config/apply").status_code == 403
    token = _csrf_from_config(client)
    response = client.post("/api/config/apply", headers={"x-csrf-token": token})
    # The endpoint may return an application validation result, but it must get past CSRF.
    assert response.status_code != 403


def test_setting_nonempty_password_enables_authentication_immediately(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("")
    token = _csrf_from_config(client)
    response = client.post(
        "/security/change-password",
        data={
            "csrf_token": token,
            "current_password": "",
            "new_password": "x",
            "confirm_password": "x",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert not AuthStore(auth_file).authentication_disabled
    protected = client.get("/", follow_redirects=False)
    assert protected.status_code == 303
    assert protected.headers["location"] == "/login"


def test_changing_nonempty_password_to_empty_disables_authentication(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("x")
    login = client.post(
        "/login",
        data={"username": "admin", "password": "x"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    csrf = _csrf_from_config(client)
    changed = client.post(
        "/security/change-password",
        data={
            "csrf_token": csrf,
            "current_password": "x",
            "new_password": "",
            "confirm_password": "",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/config"
    assert AuthStore(auth_file).authentication_disabled
    client.cookies.clear()
    assert client.get("/").status_code == 200


def test_security_section_reports_authentication_disabled(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("")
    html = client.get("/config").text
    assert "Authentication:" in html
    assert "Disabled (empty admin password)" in html


def test_advanced_yaml_editor_is_collapsed_by_default_and_persisted(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("")
    html = client.get("/config").text
    assert '<details id="advanced-yaml-panel" class="discovery-panel advanced-yaml-panel">' in html
    assert '<details id="advanced-yaml-panel" class="discovery-panel advanced-yaml-panel" open' not in html
    assert "ptz.config.advancedYamlExpanded" in html
    assert "panel.open = localStorage.getItem(key) === 'true'" in html
    assert "localStorage.setItem(key, String(panel.open))" in html


def test_advanced_yaml_contents_remain_in_dom_when_collapsed(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("")
    html = client.get("/config").text
    assert 'id="advanced-yaml-text"' in html
    assert 'name="raw_yaml"' in html
    assert 'action="/config/raw"' in html
    assert "textarea.value !== initialYaml" in html
    assert "advanced-yaml-unsaved" in html


def test_raw_yaml_save_still_works_in_auth_disabled_mode(tmp_path: Path) -> None:
    client, auth_file = _client(tmp_path)
    AuthStore(auth_file).set_password("")
    token = _csrf_from_config(client)
    import yaml
    payload = client.get("/api/config").json()["editable_config"]
    yaml_text = yaml.safe_dump(payload, sort_keys=False)
    response = client.post(
        "/config/raw",
        data={"csrf_token": token, "raw_yaml": yaml_text, "apply": "0"},
    )
    assert response.status_code == 200
