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
from ptz_joystick_controller.joystick.runtime_output import JoystickRuntimeOutputFilter  # noqa: E402
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
    parser.add_argument("--debug", action="store_true", help="Enable debug logging, including low-level runtime events")
    parser.add_argument("--verbose", action="store_true", help="Print every runtime snapshot at debug level")
    parser.add_argument("--axis-log-interval", type=float, default=0.25, help="Minimum seconds between repeated axis change logs")
    parser.add_argument("--health-log-interval", type=float, default=5.0, help="Minimum seconds between repeated unchanged health logs")
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
    output = JoystickRuntimeOutputFilter(
        axis_log_interval_seconds=args.axis_log_interval,
        health_log_interval_seconds=args.health_log_interval,
    )

    logging.info("Platform: %s", platform.platform())
    logging.info("Waiting for joystick. Press Ctrl+C to exit.")
    try:
        while True:
            snapshot = monitor.poll()
            if snapshot is None:
                for message in output.health_messages(monitor.health):
                    logging.info(message.message, *message.args)
                time.sleep(1.0)
                continue

            for message in output.health_messages(monitor.health):
                logging.info(message.message, *message.args)
            for message in output.snapshot_messages(monitor, snapshot, verbose=args.verbose):
                if message.message.startswith("Verbose"):
                    logging.debug(message.message, *message.args)
                else:
                    logging.info(message.message, *message.args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info("Stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
