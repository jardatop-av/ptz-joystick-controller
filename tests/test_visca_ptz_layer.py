from __future__ import annotations

import pytest

from ptz_joystick_controller.models.ptz import PtzCamera, PtzSpeedConfig
from ptz_joystick_controller.ptz import (
    CameraSession,
    FakeViscaTransport,
    OfflinePtzSimulation,
    PanDirection,
    PanTiltCommand,
    TiltDirection,
    PtzStopWatchdog,
    ReconnectSafeTransport,
    ViscaCommandBuilder,
    ViscaPacketEncoder,
    ZoomCommand,
    ZoomDirection,
)
from ptz_joystick_controller.ptz.speed import PtzSpeedMapper, throttle_to_multiplier


def test_visca_packet_generation_adds_header_and_sequence() -> None:
    encoder = ViscaPacketEncoder()
    packet = encoder.encode(bytes([0x81, 0x01, 0x06, 0x01, 0x00, 0x00, 0x03, 0x03, 0xFF]))

    assert packet[:2] == b"\x01\x00"
    assert packet[2:4] == b"\x00\x09"
    assert packet[4:8] == b"\x00\x00\x00\x00"
    assert packet[8:] == b"\x81\x01\x06\x01\x00\x00\x03\x03\xff"
    assert encoder.encode(b"\x81\xff")[4:8] == b"\x00\x00\x00\x01"


def test_pan_tilt_command_generation_and_speed_scaling() -> None:
    builder = ViscaCommandBuilder(visca_id=1)
    command = builder.pan_tilt(PanTiltCommand(12, 7, PanDirection.RIGHT, TiltDirection.STOP))  # type: ignore[arg-type]
    assert command.payload == b"\x81\x01\x06\x01\x0c\x07\x02\x03\xff"

    speed = PtzSpeedMapper(PtzSpeedConfig(pan_min=1, pan_max=24, tilt_min=1, tilt_max=20))
    scaled = builder.pan_tilt_from_axes(-0.5, 1.0, speed)
    assert scaled.payload == b"\x81\x01\x06\x01\x0c\x14\x01\x01\xff"


def test_zoom_scaling_and_stop_generation() -> None:
    builder = ViscaCommandBuilder(visca_id=2)
    speed = PtzSpeedMapper(PtzSpeedConfig(zoom_min=1, zoom_max=7))

    tele = builder.zoom_from_axis(1.0, speed)
    wide = builder.zoom_from_axis(-0.5, speed)
    stop_zoom = builder.zoom(ZoomCommand(0, ZoomDirection.STOP))
    stop_pt = builder.stop()

    assert tele.payload == b"\x82\x01\x04\x07\x27\xff"
    assert wide.payload == b"\x82\x01\x04\x07\x34\xff"
    assert stop_zoom.payload == b"\x82\x01\x04\x07\x00\xff"
    assert stop_pt.payload == b"\x82\x01\x06\x01\x00\x00\x03\x03\xff"


def test_throttle_mapping() -> None:
    assert throttle_to_multiplier(-1.0) == 0.2
    assert throttle_to_multiplier(1.0) == 1.0
    assert round(throttle_to_multiplier(0.0), 6) == 0.6


def test_offline_ptz_simulation_tracks_state_and_logs_commands() -> None:
    simulation = OfflinePtzSimulation.for_camera(PtzCamera(id="cam1", name="Camera 1", visca_id=1))

    simulation.session.pan_tilt_from_axes(1.0, 0.0)
    simulation.session.zoom_from_axis(-1.0)

    assert simulation.session.state.moving is True
    assert simulation.session.state.pan == 1.0
    assert simulation.session.state.zoom == -1.0
    assert len(simulation.session.command_log) == 2
    assert len(simulation.fake_transport.sent_packets) == 2

    simulation.session.stop(reason="transition")
    assert simulation.session.state.moving is False
    assert simulation.session.state.last_command == "stop:transition"
    assert simulation.session.command_log[-2].command.payload.endswith(b"\x03\x03\xff")
    assert simulation.session.command_log[-1].command.payload.endswith(b"\x00\xff")


def test_stop_watchdog_stops_moving_camera_after_timeout() -> None:
    simulation = OfflinePtzSimulation.for_camera(PtzCamera(id="cam1", name="Camera 1"))
    simulation.session.pan_tilt_from_axes(1.0, 0.0)
    watchdog = PtzStopWatchdog(timeout_ms=500, session=simulation.session)

    watchdog.tick(1000)
    assert watchdog.check(1400) is False
    assert simulation.session.state.moving is True

    assert watchdog.check(1500) is True
    assert simulation.session.state.moving is False
    assert watchdog.stopped_by_watchdog is True


def test_ptz_transition_safety_stop_packet_before_next_move() -> None:
    simulation = OfflinePtzSimulation.for_camera(PtzCamera(id="cam1", name="Camera 1"))
    simulation.session.pan_tilt_from_axes(1.0, 0.0)
    stop_packet = simulation.session.stop(reason="before_cut")
    next_packet = simulation.session.pan_tilt_from_axes(-1.0, 0.0)

    assert simulation.fake_transport.sent_packets[-3] == stop_packet
    assert simulation.fake_transport.sent_packets[-1] == next_packet
    assert simulation.session.command_log[-3].command.payload == b"\x81\x01\x06\x01\x00\x00\x03\x03\xff"
    assert simulation.session.command_log[-2].command.payload == b"\x81\x01\x04\x07\x00\xff"


def test_reconnect_handling_retries_after_connection_loss() -> None:
    class OneTimeFailTransport(FakeViscaTransport):
        failed_once: bool = False

        def send(self, packet: bytes) -> None:  # type: ignore[override]
            if not self.failed_once:
                self.failed_once = True
                self.connected = False
                raise ConnectionError("simulated drop")
            super().send(packet)

    fake = OneTimeFailTransport()
    session = CameraSession(
        camera=PtzCamera(id="cam1", name="Camera 1"),
        transport=ReconnectSafeTransport(fake, reconnect_attempts=2),
    )

    session.stop(reason="reconnect_test")

    assert fake.failed_once is True
    assert fake.connect_count >= 1
    assert len(fake.sent_packets) == 2


def test_invalid_visca_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        ViscaCommandBuilder(visca_id=8).stop()
    with pytest.raises(ValueError):
        ViscaCommandBuilder().pan_tilt(PanTiltCommand(25, 1, PanDirection.RIGHT, TiltDirection.STOP))  # type: ignore[arg-type]
