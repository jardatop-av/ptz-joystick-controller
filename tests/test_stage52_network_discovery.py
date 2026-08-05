from __future__ import annotations

import ipaddress
import threading
import time
from pathlib import Path

import pytest

from ptz_joystick_controller.discovery.network_probe import (
    DiscoveryResult,
    ProbeContext,
    build_visca_version_inquiry,
    probe_atem,
    probe_osee,
    probe_visca,
    probe_vmix,
    scan_network,
    validate_scan_network,
)
from ptz_joystick_controller.ptz.packet import ViscaPacketEncoder
from ptz_joystick_controller.switchers.osee_gsp import GspCommand


def test_private_cidr_accepted() -> None:
    assert validate_scan_network("192.168.1.0/24") == ipaddress.IPv4Network("192.168.1.0/24")
    assert validate_scan_network("169.254.5.0/24").is_link_local


def test_public_cidr_rejected() -> None:
    with pytest.raises(ValueError, match="private"):
        validate_scan_network("8.8.8.0/24")


def test_invalid_cidr_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid"):
        validate_scan_network("not-a-network")


def test_range_larger_than_slash_16_rejected() -> None:
    with pytest.raises(ValueError, match="larger than /16"):
        validate_scan_network("10.0.0.0/15")


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


def test_valid_vmix_xml_confirms_vmix() -> None:
    result = probe_vmix(
        "192.168.1.42",
        ProbeContext(),
        opener=lambda *args, **kwargs: FakeHttpResponse(b"<vmix><version>28.0</version><edition>4K</edition></vmix>"),
    )
    assert result is not None
    assert result.type == "VMIX"
    assert "28.0" in result.details


def test_arbitrary_http_response_does_not_confirm_vmix() -> None:
    assert probe_vmix("192.168.1.42", ProbeContext(), opener=lambda *a, **k: FakeHttpResponse(b"hello")) is None
    assert probe_vmix("192.168.1.42", ProbeContext(), opener=lambda *a, **k: FakeHttpResponse(b"<html/>")) is None


class FakeGspTransport:
    def __init__(self, *args, commands=(), **kwargs) -> None:
        self.commands = tuple(commands)
        self.sent_gets: list[str] = []
        self.closed = False

    def connect(self) -> None:
        pass

    def send_get(self, command_id: str) -> None:
        self.sent_gets.append(command_id)

    def receive(self):
        commands, self.commands = self.commands, ()
        return commands

    def disconnect(self) -> None:
        self.closed = True


def test_valid_osee_gsp_res_confirms_osee() -> None:
    created: list[FakeGspTransport] = []

    def factory(*args, **kwargs):
        transport = FakeGspTransport(commands=(GspCommand("pvwIndex", "res", (1,)),))
        created.append(transport)
        return transport

    result = probe_osee("192.168.1.58", ProbeContext(), transport_factory=factory)
    assert result is not None and result.type == "OSEE"
    assert created[0].sent_gets == ["pgmIndex", "pvwIndex", "transitionStatus"]
    assert created[0].closed


def test_open_osee_port_without_valid_gsp_does_not_confirm() -> None:
    assert probe_osee("192.168.1.58", ProbeContext(), transport_factory=lambda *a, **k: FakeGspTransport()) is None


class FakeUdpSocket:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        pass

    def sendto(self, packet: bytes, target: tuple[str, int]) -> None:
        self.sent.append((packet, target))

    def recvfrom(self, size: int):
        return self.response, ("192.168.1.110", 52381)

    def close(self) -> None:
        self.closed = True


def test_visca_probe_never_generates_movement_packet() -> None:
    payload = build_visca_version_inquiry()[8:]
    assert payload == b"\x81\x09\x00\x02\xff"
    assert b"\x01\x06\x01" not in payload
    assert b"\x01\x04\x07" not in payload


def test_valid_visca_inquiry_reply_confirms_camera() -> None:
    reply = ViscaPacketEncoder(payload_type=b"\x01\x11").encode(b"\x90\x50\x00\x01\xff")
    sock = FakeUdpSocket(reply)
    result = probe_visca("192.168.1.110", ProbeContext(), socket_factory=lambda *a, **k: sock)
    assert result is not None and result.type == "VISCA"
    assert sock.sent[0][0][8:] == b"\x81\x09\x00\x02\xff"
    assert sock.closed


def test_atem_probe_sends_no_switching_commands() -> None:
    assert probe_atem("192.168.1.50", ProbeContext()) is None


def test_scan_uses_bounded_concurrency() -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()

    def probe(host: str, context: ProbeContext):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return None

    scan_network(
        ipaddress.IPv4Network("192.168.1.0/29"),
        local_ip=None,
        protocols=("vmix",),
        concurrency=2,
        probes={"vmix": probe},
    )
    assert maximum <= 2


def test_cancelled_scan_does_not_start_probes() -> None:
    cancel = threading.Event()
    cancel.set()
    calls: list[str] = []

    def probe(host: str, context: ProbeContext):
        calls.append(host)
        return None

    assert scan_network(
        ipaddress.IPv4Network("192.168.1.0/30"),
        local_ip=None,
        protocols=("vmix",),
        cancel_event=cancel,
        probes={"vmix": probe},
    ) == []
    assert calls == []


def test_discovery_script_does_not_write_configuration() -> None:
    text = Path("scripts/manual_network_discovery.py").read_text(encoding="utf-8")
    assert "config.local.yaml" not in text
    assert "RuntimeApplication" not in text
