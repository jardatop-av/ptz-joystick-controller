from __future__ import annotations

from pathlib import Path

from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.calibration import AxisCalibration, JoystickCalibration
from ptz_joystick_controller.joystick.calibration_storage import JoystickCalibrationStorage
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.discovery import StaticJoystickDiscovery
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import HatState, JoystickSnapshot, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickConnectionState, JoystickDeviceInfo


def test_runtime_starts_without_joystick() -> None:
    config = load_config("config.example.yaml")
    monitor = JoystickRuntimeMonitor(config, EventBus(), discovery=StaticJoystickDiscovery())

    monitor.start()

    assert monitor.health.state == JoystickConnectionState.DISCONNECTED
    assert monitor.provider is None


def test_runtime_connects_to_fake_provider_and_tracks_snapshot() -> None:
    config = load_config("config.example.yaml")
    device = JoystickDeviceInfo(name="Logitech Extreme 3D Pro", path="fake0", backend="fake")
    provider = FakeJoystickInputProvider(JoystickSnapshot(axes=RawAxisState(pan=1000), hat=HatState(x=1, y=0)))
    monitor = JoystickRuntimeMonitor(
        config,
        EventBus(),
        discovery=StaticJoystickDiscovery((device,)),
        provider_factory=lambda _device: provider,
    )

    monitor.start()
    snapshot = monitor.poll()

    assert monitor.health.connected
    assert snapshot is not None
    assert snapshot.axes.pan == 1000
    assert monitor.hat_step(snapshot).pan_speed == config.joystick.hat.fine_pan_speed


def test_runtime_disconnect_is_safe_after_provider_failure() -> None:
    config = load_config("config.example.yaml")
    device = JoystickDeviceInfo(name="Logitech Extreme 3D Pro", path="fake0", backend="fake")

    class FailingProvider(FakeJoystickInputProvider):
        def snapshot(self):  # type: ignore[override]
            raise RuntimeError("device unplugged")

    monitor = JoystickRuntimeMonitor(
        config,
        EventBus(),
        discovery=StaticJoystickDiscovery((device,)),
        provider_factory=lambda _device: FailingProvider(),
    )

    monitor.start()

    assert monitor.provider is None
    assert monitor.health.state == JoystickConnectionState.ERROR
    assert "device unplugged" in str(monitor.health.last_error)


def test_calibration_persistence_roundtrip(tmp_path: Path) -> None:
    storage = JoystickCalibrationStorage(tmp_path / "calibration.yaml")
    calibration = JoystickCalibration(
        pan=AxisCalibration(minimum=-100, center=5, maximum=200),
        tilt=AxisCalibration(minimum=-200, center=0, maximum=200),
    )

    storage.save(calibration)
    loaded = storage.load()

    assert loaded.pan.minimum == -100
    assert loaded.pan.center == 5
    assert loaded.pan.maximum == 200
    assert loaded.tilt.minimum == -200


def test_normalized_velocity_uses_runtime_pipeline() -> None:
    config = load_config("config.example.yaml")
    monitor = JoystickRuntimeMonitor(config, EventBus(), discovery=StaticJoystickDiscovery())
    snapshot = JoystickSnapshot(axes=RawAxisState(pan=32767, tilt=0, zoom=0, throttle=32767))

    velocity = monitor.ptz_velocity(snapshot)

    assert velocity.pan == 1.0
    assert velocity.speed_multiplier == config.joystick.throttle.max_multiplier


def test_poll_after_hotplug_returns_snapshot_immediately() -> None:
    config = load_config("config.example.yaml")
    device = JoystickDeviceInfo(name="Logitech Extreme 3D Pro", path="fake0", backend="fake")
    provider = FakeJoystickInputProvider(JoystickSnapshot(axes=RawAxisState(pan=1234)))
    monitor = JoystickRuntimeMonitor(
        config,
        EventBus(),
        discovery=StaticJoystickDiscovery((device,)),
        provider_factory=lambda _device: provider,
    )

    snapshot = monitor.poll()

    assert snapshot is not None
    assert monitor.health.connected
    assert monitor.health.device == device
    assert snapshot.axes.pan == 1234
    assert "connected=True" in monitor.health.status_text()
    assert "Logitech Extreme 3D Pro" in monitor.health.status_text()
