from __future__ import annotations

from dataclasses import asdict, dataclass, field
import ipaddress
import threading
import time
import uuid
from typing import Callable, Sequence

from ..discovery.network_probe import (
    DiscoveryResult,
    auto_detect_network,
    parse_protocols,
    scan_network,
    validate_scan_network,
)


ScanFunction = Callable[..., list[DiscoveryResult]]


@dataclass(slots=True)
class DiscoveryJob:
    job_id: str
    network: str
    protocols: tuple[str, ...]
    timeout: float
    concurrency: int
    status: str = "running"
    completed: int = 0
    total: int = 0
    results: list[DiscoveryResult] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def payload(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "network": self.network,
            "protocols": list(self.protocols),
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "results": [asdict(item) for item in self.results],
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class DiscoveryJobManager:
    """Short-lived in-memory read-only discovery jobs for the Config page."""

    def __init__(self, *, scanner: ScanFunction = scan_network) -> None:
        self._scanner = scanner
        self._jobs: dict[str, DiscoveryJob] = {}
        self._lock = threading.RLock()

    def defaults(self) -> dict[str, object]:
        network, local_ip = auto_detect_network()
        return {
            "cidr": str(network),
            "local_ip": local_ip,
            "timeout": 0.5,
            "concurrency": 32,
            "protocols": ["osee", "vmix", "visca", "atem"],
        }

    def start(
        self,
        *,
        cidr: str | None,
        timeout: float,
        concurrency: int,
        protocols: Sequence[str],
    ) -> DiscoveryJob:
        if cidr and cidr.strip():
            network = validate_scan_network(cidr.strip())
            local_ip: str | None = None
        else:
            network, local_ip = auto_detect_network()
        selected = parse_protocols(protocols)
        if not selected:
            raise ValueError("Select at least one discovery protocol")
        if timeout <= 0 or timeout > 30:
            raise ValueError("timeout must be in range (0, 30]")
        if not 1 <= concurrency <= 256:
            raise ValueError("concurrency must be in range 1..256")

        host_count = sum(1 for ip in network.hosts() if str(ip) != local_ip and not ip.is_loopback)
        job = DiscoveryJob(
            job_id=uuid.uuid4().hex,
            network=str(network),
            protocols=selected,
            timeout=float(timeout),
            concurrency=int(concurrency),
            total=host_count * len(selected),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        thread = threading.Thread(
            target=self._run,
            args=(job, network, local_ip),
            name=f"discovery-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

    def _run(self, job: DiscoveryJob, network: ipaddress.IPv4Network, local_ip: str | None) -> None:
        def progress(completed: int, total: int) -> None:
            with self._lock:
                job.completed = completed
                job.total = total

        try:
            results = self._scanner(
                network,
                local_ip=local_ip,
                protocols=job.protocols,
                timeout=job.timeout,
                concurrency=job.concurrency,
                cancel_event=job.cancel_event,
                progress_callback=progress,
            )
            with self._lock:
                job.results = results
                job.status = "cancelled" if job.cancel_event.is_set() else "complete"
                if job.status == "complete":
                    job.completed = job.total
        except Exception as exc:  # job boundary: surface a readable error to the UI
            with self._lock:
                job.status = "error"
                job.error = str(exc)
        finally:
            with self._lock:
                job.finished_at = time.time()

    def get(self, job_id: str) -> DiscoveryJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> DiscoveryJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == "running":
                job.cancel_event.set()
                job.status = "cancelling"
            return job
