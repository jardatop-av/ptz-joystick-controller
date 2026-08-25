from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

DEFAULT_INTERFACE = "eth0"
NETWORK_BACKUP_PATH = Path("/var/lib/ptz-joystick-controller/network-backup.json")
DEFAULT_HELPER = "/usr/local/libexec/ptz-network-helper"

class NetworkManagerError(RuntimeError):
    pass

@dataclass(frozen=True)
class NetworkState:
    interface: str
    connected: bool
    connection: str | None
    mode: str | None
    address: str | None
    prefix: int | None
    gateway: str | None
    dns: tuple[str, ...]

@dataclass(frozen=True)
class StaticIPv4:
    address: str
    prefix: int
    gateway: str
    dns: tuple[str, ...]


def normalize_prefix(value: str | int) -> int:
    text = str(value).strip()
    if text.startswith('/'):
        text = text[1:]
    if '.' in text:
        try:
            return ipaddress.IPv4Network(f"0.0.0.0/{text}").prefixlen
        except Exception as exc:
            raise ValueError("Invalid IPv4 subnet mask") from exc
    try:
        prefix = int(text)
    except ValueError as exc:
        raise ValueError("Invalid IPv4 prefix") from exc
    if not 0 <= prefix <= 32:
        raise ValueError("IPv4 prefix must be between 0 and 32")
    return prefix


def validate_static(address: str, prefix: str | int, gateway: str, dns1: str, dns2: str = "") -> StaticIPv4:
    try:
        ip = ipaddress.IPv4Address(address.strip())
    except Exception as exc:
        raise ValueError("Invalid IPv4 address") from exc
    pfx = normalize_prefix(prefix)
    network = ipaddress.IPv4Network(f"{ip}/{pfx}", strict=False)
    if ip.is_unspecified or ip == network.network_address:
        raise ValueError("IPv4 address cannot be the network address")
    if ip == network.broadcast_address:
        raise ValueError("IPv4 address cannot be the subnet broadcast address")
    try:
        gw = ipaddress.IPv4Address(gateway.strip())
    except Exception as exc:
        raise ValueError("Invalid default gateway") from exc
    if gw.is_unspecified or gw not in network:
        raise ValueError("Default gateway must be inside the configured subnet")
    dns: list[str] = []
    for idx, raw in enumerate((dns1, dns2), 1):
        raw = raw.strip()
        if not raw:
            if idx == 1:
                raise ValueError("DNS 1 is required for Static IPv4")
            continue
        try:
            item = ipaddress.IPv4Address(raw)
        except Exception as exc:
            raise ValueError(f"Invalid DNS {idx} address") from exc
        if item.is_unspecified:
            raise ValueError(f"DNS {idx} cannot be 0.0.0.0")
        dns.append(str(item))
    return StaticIPv4(str(ip), pfx, str(gw), tuple(dns))


class NetworkManagerBackend:
    def __init__(self, interface: str = DEFAULT_INTERFACE, *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run, helper: str = DEFAULT_HELPER):
        self.interface = interface
        self._run = runner
        self.helper = helper

    def _nmcli(self, args: list[str]) -> str:
        try:
            cp = self._run(["nmcli", *args], text=True, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NetworkManagerError(f"NetworkManager unavailable: {exc}") from exc
        if cp.returncode != 0:
            raise NetworkManagerError((cp.stderr or cp.stdout or "NetworkManager command failed").strip())
        return cp.stdout

    def available(self) -> bool:
        try:
            return self._nmcli(["-t", "-f", "RUNNING", "general"]).strip().lower() == "running"
        except NetworkManagerError:
            return False

    def read_state(self) -> NetworkState:
        if not self.available():
            raise NetworkManagerError("NetworkManager is unavailable or inactive")
        # Escaped terse output safely preserves connection names with spaces.
        dev = self._nmcli(["-t", "-e", "yes", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", self.interface])
        values: dict[str, list[str]] = {}
        for line in dev.splitlines():
            if ':' not in line: continue
            key, value = line.split(':', 1)
            values.setdefault(key, []).append(value.replace('\\:', ':').replace('\\\\', '\\'))
        connection = (values.get("GENERAL.CONNECTION") or [None])[0]
        state_text = (values.get("GENERAL.STATE") or [""])[0]
        addr_raw = (values.get("IP4.ADDRESS[1]") or values.get("IP4.ADDRESS") or [None])[0]
        address = None; prefix = None
        if addr_raw:
            iface = ipaddress.IPv4Interface(addr_raw)
            address, prefix = str(iface.ip), iface.network.prefixlen
        gateway = (values.get("IP4.GATEWAY") or [None])[0] or None
        dns = tuple(v for k, vals in values.items() if k.startswith("IP4.DNS") for v in vals if v)
        mode = None
        if connection and connection != "--":
            method = self._nmcli(["-g", "ipv4.method", "connection", "show", connection]).strip()
            mode = "dhcp" if method == "auto" else ("static" if method == "manual" else method)
        return NetworkState(self.interface, "connected" in state_text.lower() or state_text.startswith("100"), connection, mode, address, prefix, gateway, dns)

    def _apply(self, payload: dict) -> None:
        raw = json.dumps(payload, separators=(",", ":"))
        try:
            cp = self._run(["sudo", "-n", self.helper], input=raw, text=True, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NetworkManagerError(f"Network apply helper failed: {exc}") from exc
        if cp.returncode != 0:
            raise NetworkManagerError((cp.stderr or cp.stdout or "Network apply failed").strip())

    def apply_dhcp(self) -> None:
        self._apply({"operation":"dhcp", "interface":self.interface})

    def apply_static(self, config: StaticIPv4) -> None:
        self._apply({"operation":"static", "interface":self.interface, **asdict(config), "dns":list(config.dns)})
