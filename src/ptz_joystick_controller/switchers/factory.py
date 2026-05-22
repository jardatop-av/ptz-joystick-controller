from __future__ import annotations

from ..models.switcher import SwitcherConfig, SwitcherType
from .atem import AtemCommandClient, AtemSwitcher
from .base import AbstractSwitcher
from .fake import FakeSwitcher
from .http_client import HttpClient, HttpTransport
from .osee_gostream_deck import OseeGoStreamDeckSwitcher
from .osee_gostream_duet import OseeGoStreamDuetSwitcher
from .vmix import VmixSwitcher


def create_offline_switcher(config: SwitcherConfig) -> AbstractSwitcher:
    return FakeSwitcher(switcher_type=SwitcherType(config.type))


def _base_url(config: SwitcherConfig, default_port: int) -> str:
    if not config.host:
        raise ValueError("Real switcher backend requires switcher.host")
    port = config.port or default_port
    return f"http://{config.host}:{port}"


def create_switcher(
    config: SwitcherConfig,
    *,
    offline: bool = True,
    http_transport: HttpTransport | None = None,
    atem_client: AtemCommandClient | None = None,
) -> AbstractSwitcher:
    if offline:
        return create_offline_switcher(config)

    switcher_type = SwitcherType(config.type)
    timeout = 2.0
    retries = 1

    if switcher_type == SwitcherType.VMIX:
        return VmixSwitcher(HttpClient(_base_url(config, 8088), timeout_seconds=timeout, retries=retries, transport=http_transport))
    if switcher_type == SwitcherType.OSEE_GOSTREAM_DECK:
        return OseeGoStreamDeckSwitcher(
            HttpClient(_base_url(config, 80), timeout_seconds=timeout, retries=retries, transport=http_transport)
        )
    if switcher_type == SwitcherType.OSEE_GOSTREAM_DUET:
        return OseeGoStreamDuetSwitcher(
            HttpClient(_base_url(config, 80), timeout_seconds=timeout, retries=retries, transport=http_transport)
        )
    if switcher_type in {SwitcherType.ATEM_MINI_PRO, SwitcherType.ATEM_TV_STUDIO_PRO_4K}:
        if atem_client is None:
            raise NotImplementedError("ATEM protocol client is not implemented yet; inject an AtemCommandClient")
        return AtemSwitcher(switcher_type=switcher_type, client=atem_client)

    raise ValueError(f"Unsupported switcher type: {config.type}")
