"""Sdílený HTTP klient — jednotná hlavička prohlížeče, timeout a opakování.

Vše jede přes obyčejné HTTP (žádná proxy, žádné obcházení antibotu).
Reálná User-Agent hlavička stačí, portály mají veřejná JSON/HTML rozhraní.
"""
from __future__ import annotations

import logging
import time

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

log = logging.getLogger("hlidac.http")

# Realistická hlavička běžného prohlížeče (macOS Safari — kamarádka má Mac).
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
    ),
    "Accept-Language": "cs,en;q=0.9",
}


class Http:
    """Tenká obálka nad httpx.Client s retry a slušným zdržením mezi požadavky."""

    def __init__(self, delay: float = 0.4, timeout: float = 25.0):
        self.delay = delay
        self._client = httpx.Client(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
        self._last = 0.0

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def get(self, url: str, **kw) -> httpx.Response:
        self._throttle()
        r = self._client.get(url, **kw)
        # 429/5xx -> vyvolá výjimku a tenacity to zkusí znovu
        if r.status_code in (429, 500, 502, 503, 504):
            r.raise_for_status()
        return r

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def post(self, url: str, **kw) -> httpx.Response:
        self._throttle()
        r = self._client.post(url, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            r.raise_for_status()
        return r

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Http":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
