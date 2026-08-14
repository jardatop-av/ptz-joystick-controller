from __future__ import annotations

import logging

from ..models.switcher import SwitcherConfig, SwitcherType
from .atem import AtemCommandClient, AtemSwitcher
from .atem_production import AtemTelevisionStudio4K8Client, ATEM_4K8_DEFAULT_PORT
from .base import AbstractSwitcher
from .fake import FakeSwitcher
from .http_client import HttpClient, HttpTransport
from .osee_gostream_deck import DeckTransportFactory, OseeGoStreamDeckSwitcher
from .osee_gostream_duet import OseeGoStreamDuetSwitcher, TransportFactory
from .vmix import VmixSwitcher

LOGGER = logging.getLogger(__name__)


def create_offline_switcher(config: SwitcherConfig) -> AbstractSwitcher:
    return FakeSwitcher(switcher_type=SwitcherType(config.type))


def _base_url(config: SwitcherConfig, default_port: int) -> str:
    if not config.host:
        raise ValueError("Real switcher backend requires switcher.host")
    port = config.port or default_port
    return f"http://{config.host}:{port}"


def switcher_backend_name(config: SwitcherConfig) -> str:
    switcher_type = SwitcherType(config.type)
    return {
        SwitcherType.VMIX: "vMix",
        SwitcherType.ATEM_MINI_PRO: "ATEM",
        SwitcherType.ATEM_TV_STUDIO_PRO_4K: "ATEM Television Studio Pro 4K",
        SwitcherType.ATEM_TELEVISION_STUDIO_4K8: "ATEM Television Studio 4K8",
        SwitcherType.OSEE_GOSTREAM_DECK: "Osee GoStream Deck",
        SwitcherType.OSEE_GOSTREAM_DUET: "Osee GoStream Duet 8 ISO",
    }[switcher_type]


def create_switcher(
    config: SwitcherConfig,
    *,
    offline: bool = True,
    http_transport: HttpTransport | None = None,
    atem_client: AtemCommandClient | None = None,
    osee_transport_factory: TransportFactory | DeckTransportFactory | None = None,
) -> AbstractSwitcher:
    if offline:
        return create_offline_switcher(config)

    switcher_type = SwitcherType(config.type)
    timeout = 2.0
    retries = 1

    if switcher_type == SwitcherType.VMIX:
        return VmixSwitcher(HttpClient(_base_url(config, 8088), timeout_seconds=timeout, retries=retries, transport=http_transport))
    if switcher_type == SwitcherType.OSEE_GOSTREAM_DECK:
        if not config.host:
            raise ValueError("Real Osee GoStream Deck backend requires switcher.host")
        kwargs = {"host": config.host, "port": config.port or 19010}
        if osee_transport_factory is not None:
            kwargs["transport_factory"] = osee_transport_factory
        return OseeGoStreamDeckSwitcher(**kwargs)
    if switcher_type == SwitcherType.OSEE_GOSTREAM_DUET:
        if not config.host:
            raise ValueError("Real Osee Duet backend requires switcher.host")
        kwargs = {"host": config.host, "port": config.port or 19010}
        if osee_transport_factory is not None:
            kwargs["transport_factory"] = osee_transport_factory
        return OseeGoStreamDuetSwitcher(**kwargs)
    if switcher_type == SwitcherType.ATEM_TELEVISION_STUDIO_4K8:
        if atem_client is None:
            if not config.host:
                raise ValueError("Real ATEM Television Studio 4K8 backend requires switcher.host")
            atem_client = AtemTelevisionStudio4K8Client(config.host, config.port or ATEM_4K8_DEFAULT_PORT, timeout=timeout)
        return AtemSwitcher(switcher_type=switcher_type, client=atem_client)
    if switcher_type in {SwitcherType.ATEM_MINI_PRO, SwitcherType.ATEM_TV_STUDIO_PRO_4K}:
        if atem_client is None:
            raise NotImplementedError("Legacy ATEM backend requires an injected AtemCommandClient")
        return AtemSwitcher(switcher_type=switcher_type, client=atem_client)

    raise ValueError(f"Unsupported switcher type: {config.type}")
