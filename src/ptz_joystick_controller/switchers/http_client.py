from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import time


class HttpClientError(ConnectionError):
    """Raised when an HTTP request fails or times out."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: str
    headers: dict[str, str]


class HttpTransport(Protocol):
    def request(self, method: str, url: str, *, body: bytes | None = None, timeout: float) -> HttpResponse:
        ...


class UrllibHttpTransport:
    def request(self, method: str, url: str, *, body: bytes | None = None, timeout: float) -> HttpResponse:
        request = Request(url=url, data=body, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - controlled appliance LAN client
                raw = response.read()
                return HttpResponse(
                    status_code=response.status,
                    body=raw.decode("utf-8", errors="replace"),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            raw = exc.read()
            raise HttpClientError(f"HTTP {exc.code} for {url}: {raw.decode('utf-8', errors='replace')}") from exc
        except URLError as exc:
            raise HttpClientError(f"HTTP request failed for {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise HttpClientError(f"HTTP request timed out for {url}") from exc


@dataclass
class HttpClient:
    base_url: str
    timeout_seconds: float = 2.0
    retries: int = 1
    retry_delay_seconds: float = 0.05
    transport: HttpTransport | None = None
    debug: bool = False
    logger: logging.Logger | None = None
    response_log_limit: int = 500

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.transport is None:
            self.transport = UrllibHttpTransport()
        if self.logger is None:
            self.logger = logging.getLogger("ptz_joystick_controller.switchers.http")

    def build_url(self, path: str, query: dict[str, str | int | None] | None = None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{normalized_path}"
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"
        return url

    def get(self, path: str, query: dict[str, str | int | None] | None = None) -> HttpResponse:
        return self.request("GET", path, query=query)

    def post(
        self,
        path: str,
        *,
        query: dict[str, str | int | None] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        return self.request("POST", path, query=query, body=body)

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str | int | None] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        assert self.transport is not None
        assert self.logger is not None
        url = self.build_url(path, query)
        last_error: Exception | None = None
        attempts = max(1, self.retries + 1)
        for attempt in range(attempts):
            try:
                if self.debug:
                    self.logger.debug("HTTP request %s %s attempt=%s timeout=%s", method, url, attempt + 1, self.timeout_seconds)
                response = self.transport.request(method, url, body=body, timeout=self.timeout_seconds)
                if self.debug:
                    snippet = response.body[: self.response_log_limit]
                    self.logger.debug("HTTP response %s %s status=%s body=%r", method, url, response.status_code, snippet)
                if not 200 <= response.status_code < 300:
                    raise HttpClientError(f"HTTP {response.status_code} for {url}")
                return response
            except Exception as exc:  # narrow at public boundary to keep retry transport-agnostic
                last_error = exc
                if self.debug:
                    self.logger.debug("HTTP request failed %s %s attempt=%s error=%s", method, url, attempt + 1, exc)
                if attempt < attempts - 1:
                    time.sleep(self.retry_delay_seconds)
        raise HttpClientError(str(last_error)) from last_error
