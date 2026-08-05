#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.discovery.network_probe import auto_detect_network, network_for_interface, parse_protocols, scan_network, validate_scan_network


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe read-only network discovery probe")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--cidr", help="Private/link-local IPv4 CIDR to scan")
    target.add_argument("--interface", help="Local interface whose IPv4 subnet should be scanned")
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--protocols", default="vmix,osee,atem,visca")
    parser.add_argument("--debug", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        if args.cidr:
            network, local_ip = validate_scan_network(args.cidr), None
        elif args.interface:
            network, local_ip = network_for_interface(args.interface)
        else:
            network, local_ip = auto_detect_network()
        protocols = parse_protocols(args.protocols)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Scanning {network} protocols={','.join(protocols)} timeout={args.timeout}s concurrency={args.concurrency}")
    cancel = threading.Event()
    try:
        results = scan_network(network, local_ip=local_ip, protocols=protocols, timeout=args.timeout, concurrency=args.concurrency, cancel_event=cancel, debug=args.debug)
    except KeyboardInterrupt:
        cancel.set()
        print("\nDiscovery cancelled.")
        return 130

    print("TYPE   | STATUS    | IP              | PORT  | DEVICE / DETAILS")
    print("-------+-----------+-----------------+-------+------------------------------")
    for result in results:
        port = "-" if result.port is None else str(result.port)
        print(f"{result.type:<6} | {result.status:<9} | {result.ip:<15} | {port:<5} | {result.details}")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.type] = counts.get(result.type, 0) + 1
    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())) or "no confirmed devices"
    print(f"Summary: total={len(results)}; {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
