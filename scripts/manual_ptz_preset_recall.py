#!/usr/bin/env python3
"""Manual PTZ preset recall test.

Recall a preset on a single configured VISCA camera without GUI or switcher
control. This is intentionally recall-only; preset editing/storage is not part
of Stage24.

Some cameras, including NewTek PTZ1, need the UDP socket/transport to stay
alive briefly after the preset recall packet is sent. The --hold-after-send
option keeps the transport open without sending any stop-after-recall command.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.ptz.session import CameraSession
from ptz_joystick_controller.ptz.transport import build_real_udp_transport


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual VISCA PTZ preset recall test")
    parser.add_argument("--config", default="config.example.yaml", help="Base config YAML path")
    parser.add_argument("--camera-id", default="cam1", help="Configured PTZ camera id")
    parser.add_argument("--preset", type=int, required=True, help="Preset number to recall")
    parser.add_argument("--timeout", type=float, default=0.5, help="UDP timeout seconds")
    parser.add_argument(
        "--hold-after-send",
        type=float,
        default=2.0,
        help="Seconds to keep the UDP transport/socket alive after preset recall before disconnecting. Default: 2.0.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--stop-before-recall",
        dest="stop_before_recall",
        action="store_true",
        default=True,
        help="Stop already tracked continuous movement before recall. Default: enabled.",
    )
    parser.add_argument(
        "--no-stop-before-recall",
        dest="stop_before_recall",
        action="store_false",
        help="Do not stop tracked continuous movement before recall.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(Path(args.config))
    camera = next((item for item in config.ptz.cameras if item.id == args.camera_id), None)
    if camera is None:
        raise SystemExit(f"Camera not found in config: {args.camera_id}")
    if not camera.enabled:
        raise SystemExit(f"Camera is disabled: {args.camera_id}")
    if not camera.host:
        raise SystemExit(f"Camera host is not configured: {args.camera_id}")

    session = CameraSession(camera=camera, transport=build_real_udp_transport(camera, timeout_seconds=args.timeout))
    try:
        # Preset recall is a discrete VISCA command. Do not send a safe stop
        # after recall, because some cameras cancel preset movement when STOP is
        # received immediately after the recall command.
        if args.stop_before_recall and (session.state.pan_tilt_active or session.state.zoom_active):
            logging.info("PTZ STOP BEFORE PRESET RECALL camera=%s", camera.id)
            session.stop_all(reason="before_preset_recall")
        logging.info("PTZ PRESET RECALL camera=%s preset=%s", camera.id, args.preset)
        session.recall_preset(args.preset)
        if args.hold_after_send > 0:
            logging.info("Waiting after preset recall: %.3f seconds", args.hold_after_send)
            time.sleep(args.hold_after_send)
    finally:
        session.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
