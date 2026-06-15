#!/usr/bin/env python3
"""Run the read-only local status dashboard.

This tool is intentionally monitor-only. It does not configure hardware, PTZ,
switchers or joystick devices. It is useful for checking that the web layer can
start from config and render safe disconnected states.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PTZ Joystick Controller read-only dashboard")
    parser.add_argument("--config", default="config.example.yaml", help="Config file path")
    parser.add_argument("--host", default=None, help="Override webui.listen_host")
    parser.add_argument("--port", type=int, default=None, help="Override webui.listen_port")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    config = load_config(Path(args.config))
    state = AppState(config=config)
    event_bus = EventBus()
    provider = RuntimeStatusProvider(state=state, event_bus=event_bus)
    app = create_web_app(provider)
    host = args.host or config.webui.listen_host
    port = args.port or config.webui.listen_port
    logging.info("Starting read-only dashboard on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="debug" if args.debug else "info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
