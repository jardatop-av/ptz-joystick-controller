#!/usr/bin/env python3
"""Manual Stage16 VISCA over IP single-camera test tool.

Examples:
  python scripts/manual_visca_single_camera.py --host 192.168.1.101 --pan 0.5 --tilt 0
  python scripts/manual_visca_single_camera.py --host 192.168.1.101 --zoom 1
  python scripts/manual_visca_single_camera.py --host 192.168.1.101 --stop

The script always attempts a safe PTZ STOP on exit.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.models.ptz import PtzCamera, PtzSpeedConfig
from ptz_joystick_controller.ptz import CameraSession, SafeStopCameraSession, UdpViscaTransport
from ptz_joystick_controller.ptz.transport import ReconnectSafeTransport

LOGGER = logging.getLogger("manual_visca_single_camera")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual VISCA over IP single-camera test")
    parser.add_argument("--host", required=True, help="Camera IP/hostname")
    parser.add_argument("--port", type=int, default=52381, help="VISCA over IP UDP port")
    parser.add_argument("--visca-id", type=int, default=1, help="VISCA camera id 1..7")
    parser.add_argument("--timeout", type=float, default=0.5, help="UDP socket timeout in seconds")
    parser.add_argument("--pan", type=float, default=0.0, help="Pan axis -1.0..1.0")
    parser.add_argument("--tilt", type=float, default=0.0, help="Tilt axis -1.0..1.0")
    parser.add_argument("--zoom", type=float, default=0.0, help="Zoom axis -1.0..1.0")
    parser.add_argument("--hold", type=float, default=0.25, help="Seconds to hold movement before safe stop")
    parser.add_argument("--stop", action="store_true", help="Send STOP only")
    parser.add_argument("--debug", action="store_true", help="Enable debug packet logging")
    return parser.parse_args()


def clamp_axis(value: float) -> float:
    return max(-1.0, min(1.0, value))


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    camera = PtzCamera(
        id="manual",
        name="Manual VISCA camera",
        host=args.host,
        port=args.port,
        visca_id=args.visca_id,
        enabled=True,
        speed=PtzSpeedConfig(),
    )
    transport = ReconnectSafeTransport(UdpViscaTransport.from_camera(camera, timeout_seconds=args.timeout))
    session = CameraSession(camera=camera, transport=transport)

    LOGGER.info("VISCA test camera=%s host=%s port=%s visca_id=%s", camera.name, camera.host, camera.port, camera.visca_id)
    with SafeStopCameraSession(session, reason="manual_script_exit") as active:
        if args.stop:
            active.stop(reason="manual_stop")
            LOGGER.info("Sent STOP")
            return 0

        pan = clamp_axis(args.pan)
        tilt = clamp_axis(args.tilt)
        zoom = clamp_axis(args.zoom)
        if pan or tilt:
            active.pan_tilt_from_axes(pan, tilt)
            LOGGER.info("Sent pan/tilt pan=%.3f tilt=%.3f", pan, tilt)
        if zoom:
            active.zoom_from_axis(zoom)
            LOGGER.info("Sent zoom zoom=%.3f", zoom)
        if not pan and not tilt and not zoom:
            active.stop(reason="manual_no_motion")
            LOGGER.info("No movement requested; sent STOP")
            return 0
        time.sleep(max(0.0, args.hold))
        LOGGER.info("Exiting; safe STOP will be sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
