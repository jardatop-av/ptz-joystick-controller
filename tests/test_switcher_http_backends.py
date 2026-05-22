from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.models.sources import UnsupportedSourceError
from ptz_joystick_controller.models.switcher import SwitcherConnectionState, SwitcherType
from ptz_joystick_controller.switchers import (
    AtemCommandClient,
    AtemSwitcher,
    HttpClient,
    HttpClientError,
    HttpResponse,
    OseeGoStreamDeckSwitcher,
    SwitcherHealthMonitor,
    SwitcherReconnectManager,
    VmixSwitcher,
    create_switcher,
)
from ptz_joystick_controller.switchers.http_client import HttpTransport


@dataclass
class MockHttpTransport(HttpTransport):
    responses: list[HttpResponse | Exception]
    requests: list[tuple[str, str, bytes | None, float]] = field(default_factory=list)

    def request(self, method: str, url: str, *, body: bytes | None = None, timeout: float) -> HttpResponse:
        self.requests.append((method, url, body, timeout))
        if not self.responses:
            raise AssertionError(f"No mock response queued for {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(body: str = "") -> HttpResponse:
    return HttpResponse(status_code=200, body=body, headers={})


def test_http_client_retries_after_transient_failure() -> None:
    transport = MockHttpTransport([HttpClientError("temporary"), ok("done")])
    client = HttpClient("http://switcher.local", retries=1, retry_delay_seconds=0, transport=transport)

    response = client.get("/status")

    assert response.body == "done"
    assert len(transport.requests) == 2


def test_vmix_poll_reads_program_and_preview_from_http_api() -> None:
    transport = MockHttpTransport([ok("<vmix><active>1</active><preview>2</preview></vmix>")])
    switcher = VmixSwitcher(HttpClient("http://127.0.0.1:8088", transport=transport))

    switcher.connect()

    assert switcher.is_connected()
    assert switcher.get_program_source() == "Input 1"
    assert switcher.get_preview_source() == "Input 2"
    assert transport.requests[0][1] == "http://127.0.0.1:8088/api"


def test_vmix_sends_preview_and_cut_commands() -> None:
    transport = MockHttpTransport([ok(), ok()])
    switcher = VmixSwitcher(HttpClient("http://127.0.0.1:8088", transport=transport))
    switcher.program_source_id = "Input 1"
    switcher.preview_source_id = "Input 2"

    switcher.set_preview_source("Input 3")
    switcher.cut()

    assert "Function=PreviewInput" in transport.requests[0][1]
    assert "Input=3" in transport.requests[0][1]
    assert "Function=Cut" in transport.requests[1][1]
    assert switcher.get_program_source() == "Input 3"
    assert switcher.get_preview_source() == "Input 1"


def test_vmix_rejects_unsupported_source() -> None:
    switcher = VmixSwitcher(HttpClient("http://127.0.0.1:8088", transport=MockHttpTransport([])))

    with pytest.raises(UnsupportedSourceError):
        switcher.set_preview_source("Input 101")


def test_osee_backend_polls_json_status_and_sends_modular_payload() -> None:
    transport = MockHttpTransport([ok('{"program":"CH1","preview":"CH2"}'), ok(), ok()])
    switcher = OseeGoStreamDeckSwitcher(HttpClient("http://192.168.1.50", transport=transport))

    switcher.connect()
    switcher.set_preview_source("CH3")
    switcher.auto()

    assert switcher.get_program_source() == "CH3"
    assert switcher.get_preview_source() == "CH1"
    assert transport.requests[0][1] == "http://192.168.1.50/api/status"
    assert transport.requests[1][0] == "POST"
    assert transport.requests[1][2] == b'{"command": "set_preview", "source": "CH3"}'
    assert transport.requests[2][2] == b'{"command": "auto"}'


@dataclass
class FakeAtemClient(AtemCommandClient):
    connected: bool = False
    program: str | None = "CH1"
    preview: str | None = "CH2"
    commands: list[str] = field(default_factory=list)

    def connect(self) -> None:
        self.connected = True
        self.commands.append("connect")

    def disconnect(self) -> None:
        self.connected = False
        self.commands.append("disconnect")

    def poll(self) -> tuple[str | None, str | None]:
        self.commands.append("poll")
        return self.program, self.preview

    def set_preview(self, source_id: str) -> None:
        self.preview = source_id
        self.commands.append(f"set_preview:{source_id}")

    def cut(self) -> None:
        self.program, self.preview = self.preview, self.program
        self.commands.append("cut")

    def auto(self) -> None:
        self.program, self.preview = self.preview, self.program
        self.commands.append("auto")


def test_atem_abstraction_uses_injected_command_client_without_protocol_details() -> None:
    client = FakeAtemClient()
    switcher = AtemSwitcher(SwitcherType.ATEM_MINI_PRO, client)

    switcher.connect()
    switcher.set_preview_source("CH3")
    switcher.cut()

    assert switcher.is_connected()
    assert client.commands == ["connect", "poll", "set_preview:CH3", "cut"]
    assert switcher.get_program_source() == "CH3"
    assert switcher.get_preview_source() == "CH1"


def test_factory_creates_real_vmix_backend_with_mocked_transport() -> None:
    config = parse_config({"switcher": {"type": "vmix", "host": "127.0.0.1", "port": 8088}})
    switcher = create_switcher(config.switcher, offline=False, http_transport=MockHttpTransport([ok()]))

    assert isinstance(switcher, VmixSwitcher)


def test_factory_creates_real_osee_backend_with_mocked_transport() -> None:
    config = parse_config({"switcher": {"type": "osee_gostream_deck", "host": "192.168.1.50"}})
    switcher = create_switcher(config.switcher, offline=False, http_transport=MockHttpTransport([ok()]))

    assert isinstance(switcher, OseeGoStreamDeckSwitcher)


def test_factory_creates_atem_backend_when_client_is_injected() -> None:
    config = parse_config({"switcher": {"type": "atem_mini_pro", "host": "192.168.1.60"}})
    switcher = create_switcher(config.switcher, offline=False, atem_client=FakeAtemClient())

    assert isinstance(switcher, AtemSwitcher)


def test_switcher_health_monitor_reports_poll_failure_and_reconnect_manager_recovers() -> None:
    transport = MockHttpTransport([HttpClientError("down"), ok("<vmix><active>1</active><preview>2</preview></vmix>")])
    switcher = VmixSwitcher(HttpClient("http://127.0.0.1:8088", transport=transport, retries=0))

    result = SwitcherHealthMonitor(switcher).check()
    recovered = SwitcherReconnectManager(switcher).ensure_connected()

    assert not result.healthy
    assert result.status.state == SwitcherConnectionState.ERROR
    assert recovered
    assert switcher.is_connected()
