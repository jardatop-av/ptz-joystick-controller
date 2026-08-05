from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import load_config
from .runtime.application import RuntimeApplication


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PTZ Joystick Controller runtime")
    parser.add_argument("--config", default="config.example.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Use offline fake switcher and fake PTZ transports")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=0.05)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    logging.basicConfig(
        level=getattr(logging, config.app.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = RuntimeApplication(config, dry_run=args.dry_run)
    try:
        app.run_forever(interval=args.poll_interval, start_web=not args.no_web)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Runtime stopped by user")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
