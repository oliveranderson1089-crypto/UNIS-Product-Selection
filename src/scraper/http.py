"""Polite HTTP client used by the crawler + PDF downloader."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_config

logger = logging.getLogger(__name__)


class PoliteClient:
    """
    Wrapper around httpx.Client that:
    - Inserts a delay between requests so we don't hammer the origin.
    - Retries with exponential backoff on transient errors.
    - Sends a descriptive User-Agent so the site operator can identify us.
    """

    def __init__(self) -> None:
        cfg = get_config()
        self._delay = cfg.crawler.delay_seconds
        self._timeout = cfg.crawler.request_timeout
        self._last_request = 0.0
        if not cfg.crawler.verify_ssl:
            logger.warning(
                "TLS verification DISABLED for crawler (config.crawler.verify_ssl=false "
                "or CRAWLER_VERIFY_SSL=false). Acceptable behind a trusted TLS-intercepting "
                "proxy; otherwise re-enable to prevent MITM."
            )
        self._client = httpx.Client(
            headers={"User-Agent": cfg.crawler.user_agent,
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"},
            timeout=self._timeout,
            follow_redirects=True,
            verify=cfg.crawler.verify_ssl,
        )

    def close(self) -> None:
        self._client.close()

    # ---- HTTP ---------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def get(self, url: str, *, stream: bool = False) -> httpx.Response:
        self._throttle()
        logger.debug("GET %s", url)
        resp = self._client.get(url) if not stream else self._client.get(url)
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    @contextmanager
    def stream(self, method: str, url: str) -> Iterator[httpx.Response]:
        self._throttle()
        logger.debug("%s (stream) %s", method, url)
        with self._client.stream(method, url) as resp:
            yield resp

    # ---- internals ----------------------------------------------------------
    def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        self._last_request = time.monotonic()


__all__ = ["PoliteClient"]
