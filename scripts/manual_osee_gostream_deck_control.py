#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.switchers.osee_deck_gsp import (  # noqa: E402
    OseeDeckManualControlClient,
    OseeDeckSourceError,
    OseeDeckSourceMap,
)
from ptz_joystick_controller.switchers.osee_gsp import GspTransportError, OseeGspTransport  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated Osee GoStream Deck write/control probe")
    parser.add_argument("--host", required=True, help="Osee GoStream Deck hostname or IP")
    parser.add_argument("--port", type=int, default=19010, help="GSP TCP port (default: 19010)")
    parser.add_argument("--timeout", type=float, default=2.0, help="Connect/read/feedback timeout in seconds")
    parser.add_argument("--debug", action="store_true", help="Enable GSP debug logging including raw packet hex")
    return parser


def print_state(client: OseeDeckManualControlClient) -> None:
    state = client.snapshot()
    print(f"Program: {state.program or 'unknown'}")
    print(f"Preview: {state.preview or 'unknown'}")
    print(f"Transition: {state.transition}")


def print_help() -> None:
    print(
        "Commands:\n"
        "  state\n"
        "  preview input1|input2|input3|input4|aux|still1|still2|ssrc\n"
        "  cut\n"
        "  auto\n"
        "  copy-program-to-preview\n"
        "  quit"
    )


def execute_command(client: OseeDeckManualControlClient, line: str) -> bool:
    command = line.strip()
    if not command:
        return True
    parts = command.split()
    verb = parts[0].lower()

    if verb in {"quit", "exit", "q"}:
        return False
    if verb in {"help", "?"}:
        print_help()
        return True
    if verb == "state":
        print_state(client)
        return True
    if verb == "preview":
        if len(parts) != 2:
            print("Usage: preview input1|input2|input3|input4|aux|still1|still2|ssrc")
            return True
        canonical = OseeDeckSourceMap.normalize(parts[1])
        source_id = OseeDeckSourceMap.to_gsp_id(canonical)
        print(f"Sending Preview -> {canonical} ({source_id})")
        if client.set_preview(canonical):
            print(f"Preview confirmed: {canonical}")
        else:
            print("Preview feedback timeout")
        return True
    if verb == "cut":
        confirmed = client.cut()
        print("CUT sent")
        if not confirmed:
            print("CUT state feedback timeout")
        print_state(client)
        return True
    if verb == "auto":
        started, completed = client.auto()
        print("AUTO started" if started else "AUTO start feedback timeout")
        if started:
            print("AUTO completed" if completed else "AUTO completion feedback timeout")
        print_state(client)
        return True
    if verb in {"copy-program-to-preview", "copy_program_to_preview", "copy"}:
        before = client.snapshot()
        if before.program is None:
            print("Program state is not known yet")
            return True
        print(f"Copying Program {before.program} -> Preview")
        try:
            source, confirmed = client.copy_program_to_preview()
        except RuntimeError as exc:
            print(str(exc))
            return True
        print(f"Preview confirmed: {source}" if confirmed else "Preview feedback timeout")
        return True

    print(f"Unknown command: {command}")
    print_help()
    return True


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
        read_timeout=min(args.timeout, 0.25),
        debug=args.debug,
    )
    client = OseeDeckManualControlClient(transport, feedback_timeout=args.timeout)
    try:
        client.connect()
        print(f"Connected to Osee GoStream Deck {args.host}:{args.port}")
        client.wait_for_initial_state(args.timeout)
        print_state(client)
        print_help()
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not execute_command(client, line):
                break
    except OseeDeckSourceError as exc:
        print(f"Invalid source: {exc}", file=sys.stderr)
        return 2
    except GspTransportError as exc:
        print(f"GSP transport error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        client.disconnect()
        print("Disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
