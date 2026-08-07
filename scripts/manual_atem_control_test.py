#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.switchers.atem_control_probe import (  # noqa: E402
    ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME,
    AtemCommandTimeout,
    AtemControlError,
    AtemManualControlClient,
    AtemStateFeedbackTimeout,
    AtemTransportAckTimeout,
)
from ptz_joystick_controller.switchers.atem_probe import ATEM_DEFAULT_PORT, AtemProbeError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manual ATEM Television Studio 4K8 Preview/CUT/AUTO test")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=ATEM_DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--debug", action="store_true")
    return parser


def print_state(client: AtemManualControlClient) -> None:
    state = client.state
    print(f"Program: {state.program_source_id} -> {state.source_label(state.program_source_id)}")
    print(f"Preview: {state.preview_source_id} -> {state.source_label(state.preview_source_id)}")
    if state.transition_in_progress:
        print(
            "Transition: in progress"
            f" frames_remaining={state.transition_frames_remaining}"
            f" position={state.transition_position}"
        )
    else:
        print("Transition: idle")


def print_inputs(client: AtemManualControlClient) -> None:
    print("Inputs:")
    for source_id in sorted(client.state.inputs):
        item = client.state.inputs[source_id]
        suffix = f" ({item.short_name})" if item.short_name and item.short_name != item.long_name else ""
        print(f"  {source_id:<5} {item.long_name or 'unnamed'}{suffix}")


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    client = AtemManualControlClient(args.host, args.port, timeout=args.timeout, debug=args.debug)
    try:
        client.connect()
        product = client.state.product_name or client.state.model_name or ATEM_TELEVISION_STUDIO_4K8_PRODUCT_NAME
        print(f"{product} connected")
        print(f"Protocol: {client.state.protocol_version or 'unknown'}")
        print_state(client)
        print("Commands: preview SOURCE_ID | cut | auto | state | inputs | quit")

        while True:
            try:
                line = input("\n> ").strip()
            except EOFError:
                line = "quit"
            if not line:
                continue
            parts = shlex.split(line)
            command = parts[0].lower()
            try:
                if command in {"quit", "exit", "q"}:
                    return 0
                if command == "state":
                    print_state(client)
                    continue
                if command == "inputs":
                    print_inputs(client)
                    continue
                if command == "preview":
                    if len(parts) != 2:
                        print("Usage: preview SOURCE_ID")
                        continue
                    source_id = int(parts[1], 10)
                    print(f"Sending CPvI packet_id={client.next_local_packet_id_value}")
                    client.set_preview(source_id)
                    print(f"Transport ACK received packet_id={client.last_command_packet_id}")
                    print(f"PrvI confirmed: {source_id} -> {client.state.source_label(source_id)}")
                    continue
                if command == "cut":
                    print(f"Sending DCut packet_id={client.next_local_packet_id_value}")
                    client.cut()
                    print(f"Transport ACK received packet_id={client.last_command_packet_id}")
                    print("PrgI/PrvI confirmed: CUT completed")
                    print_state(client)
                    continue
                if command == "auto":
                    print(f"Sending DAut packet_id={client.next_local_packet_id_value}")
                    print("AUTO started")
                    client.auto()
                    print(f"Transport ACK received packet_id={client.last_command_packet_id}")
                    print("PrgI/PrvI confirmed: AUTO completed")
                    print_state(client)
                    continue
                print("Unknown command. Use: preview SOURCE_ID | cut | auto | state | inputs | quit")
            except ValueError as exc:
                print(f"Invalid command: {exc}")
            except AtemTransportAckTimeout as exc:
                print(f"Transport ACK timeout: {exc}")
            except AtemStateFeedbackTimeout as exc:
                print(f"State feedback timeout: {exc}")
            except AtemCommandTimeout as exc:
                print(f"Command timeout: {exc}")
            except AtemControlError as exc:
                print(f"ATEM control error: {exc}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except AtemProbeError as exc:
        print(f"ATEM connection failed: {exc}", file=sys.stderr)
        return 2
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
