#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.switchers.osee_gsp import (  # noqa: E402
    GspTransportError,
    OseeGspTransport,
    format_gsp_command,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Osee GoStream Series GSP probe")
    parser.add_argument("--host", required=True, help="Osee switcher hostname or IP address")
    parser.add_argument("--port", type=int, default=19010, help="GSP TCP port (default: 19010)")
    parser.add_argument("--timeout", type=float, default=1.0, help="Connect/read timeout in seconds")
    parser.add_argument("--duration", type=float, default=15.0, help="Probe duration in seconds")
    parser.add_argument("--debug", action="store_true", help="Enable raw packet debug logging")
    parser.add_argument("--get", dest="get_ids", action="append", default=[], metavar="COMMAND_ID",
                        help="Send an explicit read-only get command; may be repeated")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    transport = OseeGspTransport(
        args.host,
        args.port,
        connect_timeout=args.timeout,
        read_timeout=args.timeout,
        debug=args.debug,
    )
    try:
        transport.connect()
        print(f"Connected to Osee GSP {args.host}:{args.port}")
        for command_id in args.get_ids:
            transport.send_get(command_id)
            print(f"Sent GET id={command_id}")
        deadline = time.monotonic() + max(0.0, args.duration)
        while time.monotonic() < deadline:
            for command in transport.receive():
                print(format_gsp_command(command), flush=True)
    except KeyboardInterrupt:
        print("Interrupted by user")
    except GspTransportError as exc:
        print(f"GSP transport error: {exc}", file=sys.stderr)
        return 1
    finally:
        transport.disconnect()
        print("Disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
