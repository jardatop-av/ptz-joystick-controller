#!/usr/bin/env python3
"""Manual end-to-end joystick -> vMix -> VISCA PTZ integration tool.

This is the first real runtime path that can drive both vMix and PTZ cameras:

- joystick buttons execute vMix PreviewInput/Cut/Fade/Copy Program To Preview
- joystick axes control the PTZ camera mapped to the current vMix Preview input
- Input 1 -> cam1 and Input 2 -> cam2 with config.example.yaml
- Input 3/Input 4 are valid vMix sources but intentionally have no PTZ mapping

Safe defaults:

- vMix commands are dry-run unless --send-switcher-commands is supplied
- PTZ uses fake VISCA logging unless --real-ptz is supplied
"""
from __future__ import annotations

import argparse
import logging
import platform
import sys
import threading
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
from ptz_joystick_controller.models.ptz import PtzCamera  # noqa: E402
from ptz_joystick_controller.ptz.transport import build_real_udp_transport  # noqa: E402
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge  # noqa: E402
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app  # noqa: E402
from ptz_joystick_controller.switchers.http_client import HttpClient  # noqa: E402
from ptz_joystick_controller.switchers.vmix import VmixSwitcher  # noqa: E402


def provider_factory(device: JoystickDeviceInfo):
    if device.backend == "evdev":
        return LinuxEvdevJoystickProvider(device.path)
    if device.backend == "pygame":
        return WindowsPygameJoystickProvider(int(device.path))
    raise RuntimeError(f"Unsupported joystick backend: {device.backend}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual joystick/vMix/real-VISCA PTZ integration test")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.example.yaml"))
    parser.add_argument("--vmix-host", required=True, help="vMix host or IP address")
    parser.add_argument("--vmix-port", type=int, default=8088, help="vMix HTTP API port")
    parser.add_argument("--vmix-timeout", type=float, default=2.0)
    parser.add_argument("--vmix-retries", type=int, default=1)
    parser.add_argument("--visca-timeout", type=float, default=0.5)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--status-interval", type=float, default=3.0)
    parser.add_argument("--send-switcher-commands", action="store_true", help="Actually send PreviewInput/Cut/Fade commands to vMix")
    parser.add_argument("--real-ptz", action="store_true", help="Open real VISCA-over-IP UDP transports for configured cameras")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable the live read-only dashboard")
    parser.add_argument("--dashboard-host", default=None, help="Override dashboard listen host")
    parser.add_argument("--dashboard-port", type=int, default=None, help="Override dashboard listen port")
    return parser.parse_args()


def start_dashboard_thread(bridge: JoystickToSwitcherBridge, *, host: str, port: int, debug: bool = False) -> None:
    """Start the read-only dashboard against live bridge state in a daemon thread."""

    def _run() -> None:
        try:
            import uvicorn

            provider = RuntimeStatusProvider.from_bridge(bridge)
            app = create_web_app(provider)
            logging.info("Live dashboard: http://%s:%s", host, port)
            uvicorn.run(app, host=host, port=port, log_level="debug" if debug else "warning")
        except Exception as exc:  # dashboard must not stop joystick/switcher/PTZ runtime
            logging.info("Live dashboard failed safely: %s", exc)

    thread = threading.Thread(target=_run, name="ptz-dashboard", daemon=True)
    thread.start()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    bus = EventBus()

    def log_event(event):
        if event.type == "ptz.active_changed":
            logging.info("Active PTZ camera: %s -> %s", event.payload.get("old_camera_id"), event.payload.get("new_camera_id"))
        elif event.type == "ptz.stop_requested":
            logging.info("PTZ stop requested: camera=%s reason=%s", event.payload.get("camera_id"), event.payload.get("reason"))
        elif event.type == "preview.changed":
            logging.info("Preview source: %s -> %s active_ptz=%s", event.payload.get("old_source_id"), event.payload.get("source_id"), event.payload.get("active_ptz_camera_id"))
        elif event.type == "program.changed":
            logging.info("Program source: %s -> %s", event.payload.get("old_source_id"), event.payload.get("source_id"))
        elif event.type in {"command.failed", "switcher.sync_failed", "switcher.connect_failed"}:
            logging.info("Runtime warning %s: %s", event.type, event.payload.get("error"))
        logging.debug("event=%s payload=%s", event.type, event.payload)

    bus.subscribe_all(log_event)

    joystick_monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=AutoJoystickDiscovery(),
        provider_factory=provider_factory,
    )
    http = HttpClient(
        f"http://{args.vmix_host}:{args.vmix_port}",
        timeout_seconds=args.vmix_timeout,
        retries=args.vmix_retries,
        debug=args.debug,
    )

    ptz_transport_factory = None
    if args.real_ptz:
        def ptz_transport_factory(camera: PtzCamera):
            return build_real_udp_transport(camera, timeout_seconds=args.visca_timeout)

    bridge = JoystickToSwitcherBridge(
        config=config,
        joystick_monitor=joystick_monitor,
        switcher=VmixSwitcher(http),
        event_bus=bus,
        dry_run=not args.send_switcher_commands,
        ptz_transport_factory=ptz_transport_factory,
    )

    logging.info("Platform: %s", platform.platform())
    logging.info("vMix commands enabled: %s", args.send_switcher_commands)
    logging.info("PTZ transport: %s", "real VISCA UDP" if args.real_ptz else "fake VISCA logging")
    logging.info("Expected mapping: Preview Input 1 -> cam1, Input 2 -> cam2, Input 3/4 -> no PTZ")

    bridge.start()

    if config.webui.enabled and not args.no_dashboard:
        start_dashboard_thread(
            bridge,
            host=args.dashboard_host or config.webui.listen_host,
            port=args.dashboard_port or config.webui.listen_port,
            debug=args.debug,
        )

    next_status = 0.0
    try:
        while True:
            status = bridge.poll_once()
            now = time.monotonic()
            if now >= next_status:
                diag = status.active_ptz_diagnostics
                logging.info(
                    "Runtime: joystick=%s vmix=%s program=%s preview=%s active_ptz=%s moving=%s last_ptz=%s ptz_log=%s error=%s",
                    status.joystick_connected,
                    status.switcher_connected,
                    status.program_source_id,
                    status.preview_source_id,
                    status.active_ptz_camera_id,
                    diag.active_camera_moving if diag else None,
                    diag.active_camera_last_command if diag else None,
                    diag.total_logged_commands if diag else None,
                    status.last_error,
                )
                next_status = now + args.status_interval
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info("Stopping: sending safe PTZ stop if an active camera exists")
        bridge.ptz_router.stop("script_exit")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
