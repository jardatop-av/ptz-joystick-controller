#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.switchers.atem_probe import (  # noqa: E402
    ATEM_DEFAULT_PORT,
    AtemProbeError,
    AtemReadOnlyProbeClient,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe read-only ATEM UDP probe")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=ATEM_DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--debug", action="store_true")
    return parser


def print_state(client: AtemReadOnlyProbeClient) -> None:
    state = client.state
    product = state.product_name or state.model_name or "unknown"
    print(f"ATEM confirmed: {client.host}:{client.port}")
    print(f"Product: {product}")
    if state.model_name:
        print(f"Model: {state.model_name}")
    print(f"Protocol version: {state.protocol_version or 'unknown'}")
    print(f"Program: {state.program_source_id} -> {state.source_label(state.program_source_id)}")
    print(f"Preview: {state.preview_source_id} -> {state.source_label(state.preview_source_id)}")
    print("Inputs:")
    for source_id in sorted(state.inputs):
        item = state.inputs[source_id]
        suffix = f" ({item.short_name})" if item.short_name and item.short_name != item.long_name else ""
        print(f"  {source_id:<5} {item.long_name or 'unnamed'}{suffix}")


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    client = AtemReadOnlyProbeClient(
        args.host,
        args.port,
        timeout=args.timeout,
        debug=args.debug,
    )
    try:
        client.connect()
        print_state(client)
        end = time.monotonic() + max(0.0, args.duration)
        last_program = client.state.program_source_id
        last_preview = client.state.preview_source_id
        while time.monotonic() < end:
            try:
                client.receive_once()
            except TimeoutError:
                continue
            if client.state.program_source_id != last_program:
                last_program = client.state.program_source_id
                print(f"Program changed: {last_program} -> {client.state.source_label(last_program)}")
            if client.state.preview_source_id != last_preview:
                last_preview = client.state.preview_source_id
                print(f"Preview changed: {last_preview} -> {client.state.source_label(last_preview)}")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except AtemProbeError as exc:
        print(f"ATEM not confirmed: {exc}", file=sys.stderr)
        return 2
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
