from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import ControllerConfig, load_config
from ..event_bus import EventBus
from ..joystick.discovery import AutoJoystickDiscovery
from ..joystick.linux_evdev import LinuxEvdevJoystickProvider
from ..joystick.runtime import JoystickRuntimeMonitor
from ..joystick.windows_pygame import WindowsPygameJoystickProvider
from ..models.joystick_runtime import JoystickDeviceInfo
from ..models.ptz import PtzCamera
from ..ptz.transport import PtzTransport, build_real_udp_transport
from ..switchers.atem import AtemCommandClient
from ..switchers.factory import create_switcher, switcher_backend_name
from ..switchers.http_client import HttpTransport
from ..switchers.osee_gostream_duet import TransportFactory
from ..webui import RuntimeStatusProvider, create_web_app
from .joystick_switcher_bridge import JoystickToSwitcherBridge

LOGGER = logging.getLogger(__name__)


def default_joystick_provider_factory(device: JoystickDeviceInfo):
    if device.backend == "evdev":
        return LinuxEvdevJoystickProvider(device.path)
    if device.backend == "pygame":
        return WindowsPygameJoystickProvider(int(device.path))
    raise RuntimeError(f"Unsupported joystick backend: {device.backend}")


@dataclass
class RuntimeApplication:
    """Single switcher-independent application runtime.

    The selected backend is created exactly once from ``switcher.type``.  The
    joystick bridge, PTZ router and dashboard only depend on AbstractSwitcher.
    """

    config: ControllerConfig
    dry_run: bool = False
    event_bus: EventBus = field(default_factory=EventBus)
    http_transport: HttpTransport | None = None
    atem_client: AtemCommandClient | None = None
    osee_transport_factory: TransportFactory | None = None
    ptz_transport_factory: Callable[[PtzCamera], PtzTransport] | None = None
    bridge: JoystickToSwitcherBridge = field(init=False)
    status_provider: RuntimeStatusProvider = field(init=False)
    _web_thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        switcher = create_switcher(
            self.config.switcher,
            offline=self.dry_run,
            http_transport=self.http_transport,
            atem_client=self.atem_client,
            osee_transport_factory=self.osee_transport_factory,
        )
        LOGGER.info("Selected switcher backend: %s", switcher_backend_name(self.config.switcher))
        joystick_monitor = JoystickRuntimeMonitor(
            config=self.config,
            event_bus=self.event_bus,
            discovery=AutoJoystickDiscovery(),
            provider_factory=default_joystick_provider_factory,
        )

        effective_ptz_transport_factory = self.ptz_transport_factory
        if effective_ptz_transport_factory is None and not self.dry_run:
            effective_ptz_transport_factory = build_real_udp_transport
            LOGGER.info("Selected PTZ transport: real VISCA UDP")
        elif effective_ptz_transport_factory is None:
            LOGGER.info("Selected PTZ transport: fake/dry-run")
        else:
            LOGGER.info("Selected PTZ transport: injected custom factory")

        # Preserve the effective factory for runtime config apply/rebuilds and
        # for introspection in tests. An explicitly injected factory always wins.
        self.ptz_transport_factory = effective_ptz_transport_factory

        self.bridge = JoystickToSwitcherBridge(
            config=self.config,
            joystick_monitor=joystick_monitor,
            switcher=switcher,
            event_bus=self.event_bus,
            dry_run=self.dry_run,
            ptz_transport_factory=effective_ptz_transport_factory,
        )
        self.status_provider = RuntimeStatusProvider.from_bridge(self.bridge)

    def start(self, *, start_web: bool = True) -> None:
        self.bridge.start()
        if start_web and self.config.webui.enabled:
            self._start_web_server()

    def poll_once(self):
        return self.bridge.poll_once()

    def run_forever(self, *, interval: float = 0.05, start_web: bool = True) -> None:
        self.start(start_web=start_web)
        try:
            while True:
                self.poll_once()
                time.sleep(interval)
        finally:
            self.stop()

    def stop(self) -> None:
        self.bridge.ptz_router.stop_all_active_motion(reason="runtime_exit")
        try:
            self.bridge.switcher.disconnect()
        except Exception:
            LOGGER.debug("Switcher disconnect failed during shutdown", exc_info=True)

    def _start_web_server(self) -> None:
        def run() -> None:
            try:
                import uvicorn

                app = create_web_app(self.status_provider)
                uvicorn.run(
                    app,
                    host=self.config.webui.listen_host,
                    port=self.config.webui.listen_port,
                    log_level="warning",
                )
            except Exception as exc:
                LOGGER.warning("Web UI failed safely: %s", exc)

        self._web_thread = threading.Thread(target=run, name="ptz-webui", daemon=True)
        self._web_thread.start()


def create_runtime_application(
    config_path: str | Path,
    *,
    dry_run: bool = False,
    http_transport: HttpTransport | None = None,
    atem_client: AtemCommandClient | None = None,
    osee_transport_factory: TransportFactory | None = None,
    ptz_transport_factory: Callable[[PtzCamera], PtzTransport] | None = None,
) -> RuntimeApplication:
    return RuntimeApplication(
        load_config(config_path),
        dry_run=dry_run,
        http_transport=http_transport,
        atem_client=atem_client,
        osee_transport_factory=osee_transport_factory,
        ptz_transport_factory=ptz_transport_factory,
    )
