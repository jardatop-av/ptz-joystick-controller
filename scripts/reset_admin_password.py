#!/usr/bin/env python3
from __future__ import annotations
import argparse
from getpass import getpass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ptz_joystick_controller.webui.auth import AuthError, AuthStore

def main() -> int:
    parser = argparse.ArgumentParser(description="Reset PTZ Controller admin password.")
    parser.add_argument("--auth-file", default="config.auth.yaml", help="Authentication file path")
    args = parser.parse_args()
    first = getpass("New admin password: ")
    second = getpass("Confirm new admin password: ")
    if first != second:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        AuthStore(args.auth_file).set_password(first)
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Admin password hash updated in {args.auth_file}. Restart the service to invalidate in-memory sessions.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
