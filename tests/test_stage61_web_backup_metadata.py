from pathlib import Path
import re
import yaml
from fastapi.testclient import TestClient
from ptz_joystick_controller.config import parse_config, load_config
from ptz_joystick_controller.constants import DEFAULT_WEB_PORT
from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app
from ptz_joystick_controller.webui.auth import AuthStore
from ptz_joystick_controller.version import __stage__, __version__


def provider():
    cfg=load_config('config.example.yaml', use_local=False)
    return RuntimeStatusProvider(AppState(cfg))


def csrf(html):
    return re.search(r'name="csrf_token" value="([^"]*)"', html).group(1)


def test_default_web_port_80_and_override():
    assert DEFAULT_WEB_PORT == 80
    assert provider().state.config.webui.listen_port == 80
    assert parse_config({'switcher':{'type':'vmix'},'webui':{'listen_port':8080}}).webui.listen_port == 8080


def test_systemd_nonroot_capability_only():
    text=Path('deploy/ptz-joystick-controller.service').read_text()
    assert 'AmbientCapabilities=CAP_NET_BIND_SERVICE' in text
    assert 'CapabilityBoundingSet=CAP_NET_BIND_SERVICE' in text
    assert 'User=root' not in text


def test_metadata_authoritative_and_transition_not_json_stringified():
    assert __version__ == '0.10.0'
    assert __stage__ == 'Stage61'
    assert 'Stage55 Fix 2' not in Path('src/ptz_joystick_controller/version.py').read_text()
    assert 'JSON.stringify(s.transition' not in Path('src/ptz_joystick_controller/webui/app.py').read_text()


def test_export_roundtrip_and_excludes_auth(tmp_path):
    auth=tmp_path/'config.auth.yaml'; AuthStore(auth).set_password('secret')
    example=tmp_path/'config.example.yaml'; example.write_text('switcher:\n  type: vmix\nwebui:\n  listen_port: 80\n')
    local=tmp_path/'config.local.yaml'; local.write_text('switcher:\n  host: 192.0.2.1\n')
    app=create_web_app(provider(), config_example_path=example, config_local_path=local, auth_file_path=auth, auth_enabled=False)
    r=TestClient(app).get('/config/export')
    assert r.status_code == 200
    data=yaml.safe_load(r.text); assert data['switcher']['host']=='192.0.2.1'
    parse_config(data)
    assert 'argon2' not in r.text.lower() and 'password' not in r.text.lower() and 'session' not in r.text.lower()


def test_import_requires_validation_confirmation_and_backs_up(tmp_path):
    example=tmp_path/'config.example.yaml'; example.write_text('switcher:\n  type: vmix\nwebui:\n  listen_port: 80\n')
    local=tmp_path/'config.local.yaml'; local.write_text('switcher:\n  type: vmix\n  host: 192.0.2.1\n')
    app=create_web_app(provider(), config_example_path=example, config_local_path=local, auth_enabled=False)
    c=TestClient(app); page=c.get('/config'); token=csrf(page.text)
    new=b'switcher:\n  type: vmix\n  host: 192.0.2.2\nwebui:\n  listen_port: 80\n'
    r=c.post('/config/import/validate', data={'csrf_token':token}, files={'config_file':('backup.yaml',new,'application/yaml')})
    assert r.status_code==200 and 'Validation succeeded' in r.text
    assert '192.0.2.1' in local.read_text()
    import_token=re.search(r'name="import_token" value="([^"]+)"',r.text).group(1)
    r=c.post('/config/import/confirm', data={'csrf_token':token,'import_token':import_token,'apply':'0'})
    assert r.status_code==200 and '192.0.2.2' in local.read_text()
    assert '192.0.2.1' in Path(str(local)+'.bak').read_text()


def test_invalid_and_oversized_import_do_not_write(tmp_path):
    example=tmp_path/'config.example.yaml'; example.write_text('switcher:\n  type: vmix\n')
    local=tmp_path/'config.local.yaml'; local.write_text('switcher:\n  type: vmix\n')
    app=create_web_app(provider(), config_example_path=example, config_local_path=local, auth_enabled=False)
    c=TestClient(app); token=csrf(c.get('/config').text); before=local.read_text()
    assert c.post('/config/import/validate',data={'csrf_token':token},files={'config_file':('x.yaml',b'::: bad: [','application/yaml')}).status_code==400
    assert c.post('/config/import/validate',data={'csrf_token':token},files={'config_file':('x.yaml',b'x'*(1024*1024+1),'application/yaml')}).status_code==413
    assert local.read_text()==before
