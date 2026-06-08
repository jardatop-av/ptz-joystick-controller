#!/usr/bin/env python3
"""Manual joystick + vMix + fake PTZ simulation.

This tool sends real joystick button commands to vMix only when --send-commands
is used. PTZ output is always fake/offline VISCA transport logging; no real
camera sockets are opened.
"""
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
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge  # noqa: E402
from ptz_joystick_controller.switchers.http_client import HttpClient  # noqa: E402
from ptz_joystick_controller.switchers.vmix import VmixSwitcher  # noqa: E402


def provider_factory(device: JoystickDeviceInfo):
    if device.backend == "evdev":
        return LinuxEvdevJoystickProvider(device.path)
    if device.backend == "pygame":
        return WindowsPygameJoystickProvider(int(device.path))
    raise RuntimeError(f"Unsupported joystick backend: {device.backend}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual joystick-to-vMix bridge with fake VISCA PTZ logging")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.example.yaml"))
    parser.add_argument("--host", required=True, help="vMix host or IP address")
    parser.add_argument("--port", type=int, default=8088, help="vMix HTTP API port")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--send-commands", action="store_true", help="Actually send PreviewInput/Cut/Fade commands to vMix")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging including fake PTZ details")
    parser.add_argument("--status-interval", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    bus = EventBus()

    def log_event(event):
        if event.type == "ptz.stop_requested":
            logging.info("PTZ stop requested: camera=%s reason=%s", event.payload.get("camera_id"), event.payload.get("reason"))
        elif event.type == "ptz.active_changed":
            logging.info("Active PTZ changed: %s -> %s", event.payload.get("old_camera_id"), event.payload.get("new_camera_id"))
        elif event.type == "command.failed":
            logging.info("Command failed: %s", event.payload.get("error"))
        elif event.type == "switcher.sync_failed":
            logging.info("vMix sync failed: %s", event.payload.get("error"))
        logging.debug("event=%s payload=%s", event.type, event.payload)

    bus.subscribe_all(log_event)
    joystick_monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=AutoJoystickDiscovery(),
        provider_factory=provider_factory,
    )
    http = HttpClient(
        f"http://{args.host}:{args.port}",
        timeout_seconds=args.timeout,
        retries=args.retries,
        debug=args.debug,
    )
    bridge = JoystickToSwitcherBridge(
        config=config,
        joystick_monitor=joystick_monitor,
        switcher=VmixSwitcher(http),
        event_bus=bus,
        dry_run=not args.send_commands,
    )

    logging.info("Platform: %s", platform.platform())
    logging.info("Joystick-vMix-fakePTZ simulation starting. send_commands=%s", args.send_commands)
    logging.info("PTZ transport mode: fake/offline VISCA logging only")
    if not args.send_commands:
        logging.info("Dry-run mode: vMix switcher commands are logged but not sent")

    bridge.start()
    next_status = 0.0
    try:
        while True:
            status = bridge.poll_once()
            now = time.monotonic()
            if now >= next_status:
                logging.info(
                    "Runtime status: joystick=%s vmix=%s program=%s preview=%s active_ptz=%s fake_ptz_commands=%s error=%s",
                    status.joystick_connected,
                    status.switcher_connected,
                    status.program_source_id,
                    status.preview_source_id,
                    status.active_ptz_camera_id,
                    len(bridge.ptz_router.command_log),
                    status.last_error,
                )
                next_status = now + args.status_interval
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info("Stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
