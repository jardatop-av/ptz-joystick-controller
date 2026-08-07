from __future__ import annotations

import ipaddress
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ptz_joystick_controller.discovery.network_probe import DiscoveryResult
from ptz_joystick_controller.webui import create_web_app
from ptz_joystick_controller.webui.discovery_panel import DiscoveryJobManager
from test_stage29_webui_dashboard import _provider


def fake_scanner(network, *, local_ip, protocols, timeout, concurrency, cancel_event, progress_callback=None, **kwargs):
    total = 3
    if progress_callback:
        progress_callback(0, total)
    rows = [
        DiscoveryResult("OSEE", "confirmed", "192.168.1.58", 19010, "Valid GSP response: pvwIndex"),
        DiscoveryResult("VMIX", "confirmed", "192.168.1.42", 8088, "vMix API version 28"),
    ]
    for index in range(total):
        if cancel_event.is_set():
            break
        if progress_callback:
            progress_callback(index + 1, total)
    return rows


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    local = tmp_path / "config.local.yaml"
    manager = DiscoveryJobManager(scanner=fake_scanner)
    app = create_web_app(_provider(), config_local_path=local, discovery_manager=manager)
    return TestClient(app), local


def test_discovery_panel_exists_and_is_collapsed_by_default(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    html = client.get("/config").text
    assert '<details id="discovery-panel" class="discovery-panel">' in html
    assert '<summary>Discovery</summary>' in html
    assert '<details id="discovery-panel" class="discovery-panel" open>' not in html
    assert "Scan Local Network" in html
    assert "Read-only discovery not yet implemented." in html


def test_expansion_state_is_stored_in_local_storage(tmp_path: Path) -> None:
    html = _client(tmp_path)[0].get("/config").text
    assert "localStorage.getItem('ptz-discovery-expanded')" in html
    assert "localStorage.setItem('ptz-discovery-expanded', String(panel.open))" in html


def test_scan_endpoint_invokes_existing_backend_and_returns_results(tmp_path: Path) -> None:
    client, local = _client(tmp_path)
    response = client.post(
        "/api/discovery/scan",
        json={"cidr": "192.168.1.0/30", "timeout": 0.1, "concurrency": 2, "protocols": ["osee", "vmix", "visca"]},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    for _ in range(50):
        status = client.get(f"/api/discovery/jobs/{job_id}").json()
        if status["status"] == "complete":
            break
        time.sleep(0.01)
    assert status["status"] == "complete"
    assert {item["type"] for item in status["results"]} == {"OSEE", "VMIX"}
    assert not local.exists()


def test_copy_ip_and_ip_port_use_clipboard_api(tmp_path: Path) -> None:
    html = _client(tmp_path)[0].get("/config").text
    assert "copyTextToClipboard(text)" in html
    assert "navigator.clipboard.writeText(value)" in html
    assert "document.execCommand('copy')" in html
    assert "Copy IP</button>" in html
    assert "Copy IP:Port</button>" in html
    assert "copied ? 'Copied' : 'Copy failed'" in html


def test_results_table_and_scan_progress_are_present(tmp_path: Path) -> None:
    html = _client(tmp_path)[0].get("/config").text
    for heading in ("Type", "Status", "IP Address", "Port", "Details", "Copy"):
        assert f">{heading}<" in html
    assert 'id="discovery-progress"' in html
    assert 'id="discovery-cancel"' in html
    assert "setInterval(() => poll()" in html


def test_discovery_does_not_change_config_or_runtime(tmp_path: Path) -> None:
    client, local = _client(tmp_path)
    before = client.get("/api/status").json()
    response = client.post("/api/discovery/scan", json={"cidr": "10.0.0.0/30", "protocols": ["osee"], "timeout": 0.1, "concurrency": 1})
    assert response.status_code == 202
    time.sleep(0.03)
    after = client.get("/api/status").json()
    assert before["switcher"] == after["switcher"]
    assert before["ptz"] == after["ptz"]
    assert not local.exists()
