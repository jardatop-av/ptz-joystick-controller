from .atem import AtemCommandClient, AtemSwitcher
from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_source_ids, get_switcher_capabilities
from .factory import create_offline_switcher, create_switcher
from .fake import FakeSwitcher
from .health import HealthCheckResult, ReconnectPolicy, SwitcherHealthMonitor, SwitcherReconnectManager
from .http_client import HttpClient, HttpClientError, HttpResponse, HttpTransport
from .osee_gostream_deck import OseeGoStreamDeckSwitcher
from .osee_gostream_duet import OseeGoStreamDuetSwitcher
from .vmix import VmixApiClient, VmixApiError, VmixState, VmixSwitcher

__all__ = [
    "AbstractSwitcher",
    "AtemCommandClient",
    "AtemSwitcher",
    "FakeSwitcher",
    "HealthCheckResult",
    "HttpClient",
    "HttpClientError",
    "HttpResponse",
    "HttpTransport",
    "OseeGoStreamDeckSwitcher",
    "OseeGoStreamDuetSwitcher",
    "ReconnectPolicy",
    "SwitcherHealthMonitor",
    "SwitcherReconnectManager",
    "VmixApiClient",
    "VmixApiError",
    "VmixState",
    "VmixSwitcher",
    "create_offline_switcher",
    "create_switcher",
    "get_available_sources",
    "get_source_ids",
    "get_switcher_capabilities",
]

# Osee GoStream Series Protocol transport used by the runtime Duet backend.
from .osee_gsp import GspCommand, GspStreamParser, OseeGspTransport, crc16_modbus, format_gsp_command

__all__ += [
    "GspCommand",
    "GspStreamParser",
    "OseeGspTransport",
    "crc16_modbus",
    "format_gsp_command",
]

# Stage46: isolated GoStream Duet 8 ISO GSP mapping/control probe.
from .osee_duet_gsp import OseeDuetGspController, OseeDuetSourceMap, OseeDuetSourceRef, OseeDuetState

__all__ += [
    "OseeDuetGspController",
    "OseeDuetSourceMap",
    "OseeDuetSourceRef",
    "OseeDuetState",
]
