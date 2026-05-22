#!/usr/bin/env python3
"""Manual vMix integration smoke test.

Usage:
    python scripts/manual_vmix_integration.py --host 192.168.1.100 --port 8088

This script performs a safe read-only state poll by default. Use --send-commands
only when you intentionally want to send PreviewInput, Cut or Fade commands.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptz_joystick_controller.switchers import HttpClient, VmixSwitcher  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual vMix HTTP API integration test")
    parser.add_argument("--host", required=True, help="vMix host or IP address")
    parser.add_argument("--port", type=int, default=8088, help="vMix HTTP API port, usually 8088")
    parser.add_argument("--timeout", type=float, default=2.0, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=1, help="Retry count after the first attempt")
    parser.add_argument("--preview-input", default=None, help="Optional input number or source id, e.g. 2 or 'Input 2'")
    parser.add_argument("--send-commands", action="store_true", help="Allow command sending; otherwise only polling is performed")
    parser.add_argument("--cut", action="store_true", help="Send Cut command; requires --send-commands")
    parser.add_argument("--fade", action="store_true", help="Send Fade command; requires --send-commands")
    parser.add_argument("--debug", action="store_true", help="Enable HTTP request/response debug logging")
    return parser.parse_args()


def normalize_source(value: str) -> str:
    return value if value.startswith("Input ") else f"Input {value}"


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)s: %(message)s")

    client = HttpClient(
        f"http://{args.host}:{args.port}",
        timeout_seconds=args.timeout,
        retries=args.retries,
        debug=args.debug,
    )
    switcher = VmixSwitcher(client)

    switcher.connect()
    status = switcher.get_status()
    print(f"status={status.state} message={status.message}")
    print(f"program={switcher.get_program_source()} preview={switcher.get_preview_source()}")

    if not switcher.is_connected():
        return 2

    if not args.send_commands:
        if args.preview_input or args.cut or args.fade:
            print("Commands were requested but not sent because --send-commands is missing.")
        return 0

    if args.preview_input:
        switcher.set_preview_source(normalize_source(args.preview_input))
        print(f"sent PreviewInput for {normalize_source(args.preview_input)}")
    if args.cut:
        switcher.cut()
        print("sent Cut")
    if args.fade:
        switcher.fade()
        print("sent Fade")

    switcher.poll()
    print(f"after commands: program={switcher.get_program_source()} preview={switcher.get_preview_source()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
