from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import ipaddress
import logging
import socket
import struct
import sys
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Iterable, Sequence

from ..ptz.packet import ViscaPacketEncoder
from ..switchers.osee_gsp import GspTransportError, OseeGspTransport
from ..switchers.atem_probe import AtemReadOnlyProbeClient, AtemProbeError, AtemTimeoutError, ATEM_DEFAULT_PORT

LOGGER = logging.getLogger(__name__)
DEFAULT_PORTS = {"vmix": 8088, "osee": 19010, "atem": 9910, "visca": 52381}
SUPPORTED_PROTOCOLS = frozenset({"vmix", "osee", "atem", "visca"})


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    type: str
    status: str
    ip: str
    port: int | None
    details: str


@dataclass(frozen=True, slots=True)
class ProbeContext:
    timeout: float = 0.5
    debug: bool = False


def validate_scan_network(cidr: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid IPv4 CIDR: {cidr}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("Only IPv4 networks are supported")
    if network.prefixlen < 16:
        raise ValueError("Subnet ranges larger than /16 are not allowed")
    if not (network.is_private or network.is_link_local):
        raise ValueError("Only RFC1918 private or IPv4 link-local networks are allowed")
    if network.is_loopback:
        raise ValueError("Loopback networks are not allowed")
    return network


def primary_ipv4_address() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()


def network_for_interface(interface: str) -> tuple[ipaddress.IPv4Network, str]:
    if not interface.strip():
        raise ValueError("Interface name must not be empty")
    try:
        import psutil  # type: ignore

        for address in psutil.net_if_addrs().get(interface, ()):
            if address.family == socket.AF_INET and address.address and address.netmask:
                ip = ipaddress.IPv4Address(address.address)
                network = ipaddress.IPv4Network(f"{address.address}/{address.netmask}", strict=False)
                return validate_scan_network(str(network)), str(ip)
    except ImportError:
        pass
    if sys.platform.startswith("linux"):
        try:
            import fcntl

            def ioctl_ipv4(request: int) -> str:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    packed = struct.pack("256s", interface.encode("utf-8")[:15])
                    return socket.inet_ntoa(fcntl.ioctl(sock.fileno(), request, packed)[20:24])
                finally:
                    sock.close()

            address = ioctl_ipv4(0x8915)  # SIOCGIFADDR
            netmask = ioctl_ipv4(0x891B)  # SIOCGIFNETMASK
            network = ipaddress.IPv4Network(f"{address}/{netmask}", strict=False)
            return validate_scan_network(str(network)), address
        except (OSError, ValueError):
            pass
    raise ValueError(f"Unable to determine IPv4 subnet for interface: {interface}")


def auto_detect_network() -> tuple[ipaddress.IPv4Network, str]:
    ip = ipaddress.IPv4Address(primary_ipv4_address())
    if not (ip.is_private or ip.is_link_local):
        raise ValueError(f"Primary IPv4 address is not private/link-local: {ip}")
    network = ipaddress.IPv4Network(f"{ip}/24", strict=False)
    return validate_scan_network(str(network)), str(ip)


def parse_protocols(value: str | Iterable[str]) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else list(value)
    protocols = tuple(dict.fromkeys(item.strip().lower() for item in raw if item.strip()))
    unknown = set(protocols) - SUPPORTED_PROTOCOLS
    if unknown:
        raise ValueError(f"Unsupported protocols: {', '.join(sorted(unknown))}")
    return protocols or tuple(sorted(SUPPORTED_PROTOCOLS))


def probe_vmix(host: str, context: ProbeContext, *, opener: Callable[..., object] = urllib.request.urlopen) -> DiscoveryResult | None:
    url = f"http://{host}:{DEFAULT_PORTS['vmix']}/api"
    try:
        response = opener(url, timeout=context.timeout)
        body = response.read()  # type: ignore[attr-defined]
    except (OSError, TimeoutError, urllib.error.URLError):
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    if root.tag.lower() != "vmix":
        return None
    version = root.findtext("version") or root.attrib.get("version") or "unknown"
    edition = root.findtext("edition")
    details = f"vMix API version {version}" + (f", {edition}" if edition else "")
    return DiscoveryResult("VMIX", "confirmed", host, DEFAULT_PORTS["vmix"], details)


def probe_osee(host: str, context: ProbeContext, *, transport_factory: Callable[..., OseeGspTransport] = OseeGspTransport) -> DiscoveryResult | None:
    transport = transport_factory(host, DEFAULT_PORTS["osee"], connect_timeout=context.timeout, read_timeout=context.timeout, debug=context.debug)
    try:
        transport.connect()
        for command_id in ("pgmIndex", "pvwIndex", "transitionStatus"):
            transport.send_get(command_id)
        for _ in range(3):
            for command in transport.receive():
                if command.type in {"res", "pus"}:
                    return DiscoveryResult("OSEE", "confirmed", host, DEFAULT_PORTS["osee"], f"Valid GSP response: {command.id}")
    except (GspTransportError, OSError, TimeoutError, ValueError):
        return None
    finally:
        transport.disconnect()
    return None


VISCA_VERSION_INQUIRY_PAYLOAD = b"\x81\x09\x00\x02\xFF"


def build_visca_version_inquiry() -> bytes:
    return ViscaPacketEncoder().encode(VISCA_VERSION_INQUIRY_PAYLOAD)


def is_valid_visca_inquiry_reply(data: bytes) -> bool:
    if len(data) < 10:
        return False
    payload_length = int.from_bytes(data[2:4], "big")
    if payload_length <= 0 or len(data) < 8 + payload_length:
        return False
    payload = data[8 : 8 + payload_length]
    return len(payload) >= 3 and payload[0] & 0xF0 == 0x90 and payload[-1] == 0xFF and payload[1] in {0x50, 0x51}


def probe_visca(host: str, context: ProbeContext, *, socket_factory: Callable[..., socket.socket] = socket.socket) -> DiscoveryResult | None:
    sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(context.timeout)
        sock.sendto(build_visca_version_inquiry(), (host, DEFAULT_PORTS["visca"]))
        data, _address = sock.recvfrom(4096)
        if not is_valid_visca_inquiry_reply(data):
            return None
        return DiscoveryResult("VISCA", "confirmed", host, DEFAULT_PORTS["visca"], "Valid VISCA version/interface inquiry reply")
    except (OSError, TimeoutError, socket.timeout):
        return None
    finally:
        sock.close()


def probe_atem(host: str, context: ProbeContext) -> DiscoveryResult | None:
    client = AtemReadOnlyProbeClient(host, DEFAULT_PORTS["atem"], timeout=context.timeout, debug=context.debug, trace_packets=False)
    try:
        state = client.connect()
        if not client.confirmed:
            return None
        product = state.product_name or "ATEM device"
        version = state.protocol_version
        details = product + (f" | Protocol {version}" if version else "")
        return DiscoveryResult("ATEM", "confirmed", host, DEFAULT_PORTS["atem"], details)
    except (AtemProbeError, AtemTimeoutError, OSError, ValueError):
        return None
    finally:
        client.disconnect()


ProbeFunction = Callable[[str, ProbeContext], DiscoveryResult | None]
DEFAULT_PROBES: dict[str, ProbeFunction] = {"vmix": probe_vmix, "osee": probe_osee, "atem": probe_atem, "visca": probe_visca}


def scan_network(
    network: ipaddress.IPv4Network,
    *,
    local_ip: str | None,
    protocols: Sequence[str],
    timeout: float = 0.5,
    concurrency: int = 32,
    cancel_event: threading.Event | None = None,
    probes: dict[str, ProbeFunction] | None = None,
    debug: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[DiscoveryResult]:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if not 1 <= concurrency <= 256:
        raise ValueError("concurrency must be in range 1..256")
    selected = parse_protocols(protocols)
    probe_map = probes or DEFAULT_PROBES
    cancel = cancel_event or threading.Event()
    hosts = [str(ip) for ip in network.hosts() if str(ip) != local_ip and not ip.is_loopback]
    results: list[DiscoveryResult] = []
    total_tasks = len(hosts) * len(selected)
    completed_tasks = 0
    if progress_callback is not None:
        progress_callback(0, total_tasks)

    def task(host: str, protocol: str) -> DiscoveryResult | None:
        if cancel.is_set():
            return None
        return probe_map[protocol](host, ProbeContext(timeout=timeout, debug=debug))

    executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="discovery")
    futures = []
    try:
        for host in hosts:
            for protocol in selected:
                if cancel.is_set():
                    break
                futures.append(executor.submit(task, host, protocol))
        for future in as_completed(futures):
            if cancel.is_set():
                break
            result = future.result()
            completed_tasks += 1
            if progress_callback is not None:
                progress_callback(completed_tasks, total_tasks)
            if result is not None:
                results.append(result)
    except KeyboardInterrupt:
        cancel.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return sorted(results, key=lambda item: (ipaddress.ip_address(item.ip), item.type, item.port or 0))
