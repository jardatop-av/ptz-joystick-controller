from pathlib import Path
import yaml
from fastapi.testclient import TestClient

from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app
from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.webui.auth import AuthStore


def provider():
    cfg = load_config("config.example.yaml", use_local=False)
    return RuntimeStatusProvider(AppState(cfg))


def app_client(tmp_path):
    auth = tmp_path / "config.auth.yaml"
    app = create_web_app(provider(), auth_file_path=auth, auth_enabled=True)
    return TestClient(app), auth


def setup_password(client, password="heslo1234"):
    r = client.post("/setup", data={"new_password": password, "confirm_password": password}, follow_redirects=False)
    assert r.status_code == 303


def login(client, password="heslo1234"):
    return client.post("/login", data={"username": "admin", "password": password}, follow_redirects=False)


def csrf_from_config(client):
    html = client.get("/config").text
    marker = 'name="csrf_token" value="'
    return html.split(marker, 1)[1].split('"', 1)[0]


def test_first_run_does_not_expose_gui(tmp_path):
    client, _ = app_client(tmp_path)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert client.get("/api/status").status_code == 401


def test_setup_hashes_password_without_plaintext(tmp_path):
    client, auth = app_client(tmp_path)
    setup_password(client)
    text = auth.read_text()
    assert "heslo1234" not in text
    assert "$argon2id$" in text
    assert AuthStore(auth).verify("heslo1234")
    assert not AuthStore(auth).verify("wrong")


def test_setup_requires_matching_and_minimum_passwords(tmp_path):
    client, auth = app_client(tmp_path)
    assert client.post("/setup", data={"new_password": "abcdefgh", "confirm_password": "abcdefgi"}).status_code == 400
    assert client.post("/setup", data={"new_password": "short", "confirm_password": "short"}).status_code == 400
    assert not auth.exists()


def test_login_cookie_and_protected_pages(tmp_path):
    client, _ = app_client(tmp_path)
    setup_password(client)
    bad = login(client, "wrongpass")
    assert bad.status_code == 401
    good = login(client)
    assert good.status_code == 303
    cookie = good.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert client.get("/").status_code == 200
    assert client.get("/config").status_code == 200
    assert client.get("/diagnostics").status_code == 200


def test_logout_requires_csrf_and_invalidates_session(tmp_path):
    client, _ = app_client(tmp_path)
    setup_password(client); login(client)
    assert client.post("/logout", data={"csrf_token": "bad"}).status_code == 403
    csrf = csrf_from_config(client)
    r = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_config_write_requires_csrf(tmp_path):
    client, _ = app_client(tmp_path)
    setup_password(client); login(client)
    assert client.post("/api/config/apply").status_code == 403


def test_password_change_invalidates_sessions_and_old_password(tmp_path):
    client, auth = app_client(tmp_path)
    setup_password(client); login(client)
    csrf = csrf_from_config(client)
    r = client.post("/security/change-password", data={
        "csrf_token": csrf, "current_password": "heslo1234",
        "new_password": "nové heslo 123", "confirm_password": "nové heslo 123"
    }, follow_redirects=False)
    assert r.status_code == 303
    assert not AuthStore(auth).verify("heslo1234")
    assert AuthStore(auth).verify("nové heslo 123")
    assert client.get("/", follow_redirects=False).status_code == 303
    assert login(client, "heslo1234").status_code == 401
    assert login(client, "nové heslo 123").status_code == 303


def test_login_rate_limit(tmp_path):
    client, _ = app_client(tmp_path)
    setup_password(client)
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "wrongpass"})
    assert client.post("/login", data={"username": "admin", "password": "heslo1234"}).status_code == 429


def test_login_page_dark_theme_and_admin_name(tmp_path):
    client, _ = app_client(tmp_path)
    setup_password(client)
    html = client.get("/login").text
    assert 'data-theme="dark"' in html
    assert "Username: <strong>admin</strong>" in html
    assert "ptz-theme" in html


def test_security_section_and_logout_in_authenticated_gui(tmp_path):
    client, _ = app_client(tmp_path)
    setup_password(client); login(client)
    html = client.get("/config").text
    assert "Security" in html
    assert "Change password" in html
    assert 'action="/logout"' in html
    assert 'name="csrf_token"' in html
