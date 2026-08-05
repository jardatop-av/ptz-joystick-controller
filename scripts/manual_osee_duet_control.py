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

from ptz_joystick_controller.switchers.osee_duet_gsp import (  # noqa: E402
    OseeDuetGspController,
    OseeDuetSourceError,
    OseeDuetSourceMap,
)
from ptz_joystick_controller.switchers.osee_gsp import (  # noqa: E402
    GspTransportError,
    OseeGspTransport,
    format_gsp_command,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated Osee GoStream Duet 8 ISO GSP control probe")
    parser.add_argument("--host", required=True, help="Osee switcher hostname or IP address")
    parser.add_argument("--port", type=int, default=19010, help="GSP TCP port (default: 19010)")
    parser.add_argument("--timeout", type=float, default=1.0, help="Connect/read timeout in seconds")
    parser.add_argument("--duration", type=float, default=15.0, help="Watch duration in seconds")
    parser.add_argument("--debug", action="store_true", help="Enable raw packet debug logging")
    parser.add_argument("--watch", action="store_true", help="Print incoming state updates for --duration")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--preview", metavar="SOURCE", help="Set Preview: 1..8, Input N, MP1, MP2, M/SRC")
    commands.add_argument("--program", metavar="SOURCE", help="Set Program: 1..8, Input N, MP1, MP2, M/SRC")
    commands.add_argument("--cut", action="store_true", help="Send CUT")
    commands.add_argument("--auto", action="store_true", help="Send AUTO")
    return parser


def _print_state(controller: OseeDuetGspController) -> None:
    preview = controller.state.preview.display_name if controller.state.preview else "unknown"
    program = controller.state.program.display_name if controller.state.program else "unknown"
    transition = controller.state.transition_status
    print(f"STATE preview={preview} program={program} transition={transition}", flush=True)


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
    controller = OseeDuetGspController(transport)
    try:
        transport.connect()
        print(f"Connected to Osee GoStream Duet GSP {args.host}:{args.port}")
        if args.preview is not None:
            canonical = OseeDuetSourceMap.normalize(args.preview)
            controller.set_preview(canonical)
            print(f"Sent Preview {canonical} ({OseeDuetSourceMap.to_gsp_id(canonical)})")
        elif args.program is not None:
            canonical = OseeDuetSourceMap.normalize(args.program)
            controller.set_program(canonical)
            print(f"Sent Program {canonical} ({OseeDuetSourceMap.to_gsp_id(canonical)})")
        elif args.cut:
            controller.cut()
            print("Sent CUT")
        elif args.auto:
            controller.auto()
            print("Sent AUTO")

        if args.watch or not any((args.preview, args.program, args.cut, args.auto)):
            deadline = time.monotonic() + max(0.0, args.duration)
            while time.monotonic() < deadline:
                for command in transport.receive():
                    print(format_gsp_command(command), flush=True)
                    if controller.handle_command(command):
                        _print_state(controller)
    except OseeDuetSourceError as exc:
        print(f"Invalid source: {exc}", file=sys.stderr)
        return 2
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
