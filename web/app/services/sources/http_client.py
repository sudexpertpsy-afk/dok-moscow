"""Общий HTTP-клиент: троттлинг 1 rps и простой in-memory кэш."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_UA = "DokMoscowBot/1.0 (+https://dok.moscow; legal@dok.moscow)"


@dataclass
class _CacheEntry:
    expires_at: float
    status_code: int
    content: bytes
    headers: dict[str, str]


class ThrottledClient:
    """Синхронный httpx-клиент с паузой между запросами и кэшем GET."""

    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        cache_ttl: float = 300.0,
        timeout: float = 120.0,  # полные кодексы ИПС (fulltext) бывают >1–4 МБ
        user_agent: str = DEFAULT_UA,
        transport: httpx.BaseTransport | None = None,
        clock=None,
    ) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.cache_ttl = max(0.0, float(cache_ttl))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        # отрицательный старт — первый запрос без ожидания
        self._last_request_at = -self.min_interval
        self._cache: dict[str, _CacheEntry] = {}
        self._request_count = 0
        verify: bool | str = True
        try:
            import ssl
            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
            verify = ctx
        except ImportError:
            verify = True
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            follow_redirects=True,
            transport=transport,
            verify=verify,
        )

    @property
    def request_count(self) -> int:
        return self._request_count

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ThrottledClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def get(self, url: str, *, use_cache: bool = True) -> httpx.Response:
        now = self._clock()
        if use_cache and self.cache_ttl > 0:
            with self._lock:
                hit = self._cache.get(url)
                if hit is not None and hit.expires_at > now:
                    return httpx.Response(
                        hit.status_code,
                        content=hit.content,
                        headers=hit.headers,
                        request=httpx.Request("GET", url),
                    )

        with self._lock:
            wait = self.min_interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

        response = self._client.get(url)
        self._request_count += 1
        with self._lock:
            self._last_request_at = self._clock()
            if use_cache and self.cache_ttl > 0 and response.status_code == 200:
                self._cache[url] = _CacheEntry(
                    expires_at=self._clock() + self.cache_ttl,
                    status_code=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )
        return response
