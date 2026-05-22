#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import platform
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.config import load_config  # noqa: E402
from ptz_joystick_controller.event_bus import EventBus  # noqa: E402
from ptz_joystick_controller.joystick.discovery import AutoJoystickDiscovery  # noqa: E402
from ptz_joystick_controller.joystick.linux_evdev import LinuxEvdevJoystickProvider  # noqa: E402
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor  # noqa: E402
from ptz_joystick_controller.joystick.windows_pygame import WindowsPygameJoystickProvider  # noqa: E402
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo  # noqa: E402


def provider_factory(device: JoystickDeviceInfo):
    if device.backend == "evdev":
        return LinuxEvdevJoystickProvider(device.path)
    if device.backend == "pygame":
        return WindowsPygameJoystickProvider(int(device.path))
    raise RuntimeError(f"Unsupported joystick backend: {device.backend}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual Logitech Extreme 3D Pro joystick monitor")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.example.yaml"))
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    bus = EventBus()

    def log_event(event):
        if event.type == "joystick.connected":
            device = event.payload.get("device")
            device_name = event.payload.get("device_name") or getattr(device, "name", "unknown")
            logging.info("Joystick connected: %s", device_name)
        elif event.type == "joystick.disconnected":
            logging.info("Joystick disconnected: %s", event.payload.get("reason"))
        elif event.type == "joystick.error":
            logging.info("Joystick disconnected: %s", event.payload.get("error"))
        logging.debug("event=%s payload=%s", event.type, event.payload)

    bus.subscribe_all(log_event)
    monitor = JoystickRuntimeMonitor(config, bus, discovery=AutoJoystickDiscovery(), provider_factory=provider_factory)

    logging.info("Platform: %s", platform.platform())
    logging.info("Waiting for joystick. Press Ctrl+C to exit.")
    try:
        while True:
            snapshot = monitor.poll()
            if snapshot is None:
                logging.info("Joystick health status: %s", monitor.health.status_text())
                time.sleep(1.0)
                continue
            velocity = monitor.ptz_velocity(snapshot)
            hat = monitor.hat_step(snapshot)
            logging.info(
                "Joystick health status: %s axes=%s pressed=%s velocity=%s hat_step=%s",
                monitor.health.status_text(),
                snapshot.axes,
                sorted(snapshot.pressed_buttons),
                velocity,
                hat,
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info("Stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
