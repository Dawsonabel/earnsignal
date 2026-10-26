"""Polite HTTP client: per-host rate limits, retries with backoff, descriptive User-Agent."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import requests

from . import config

_RETRY_STATUS = {429, 500, 502, 503, 504}


class HostUnreachable(requests.ConnectionError):
    pass


class Client:
    def __init__(self, contact: str | None = None, rate_limits: dict | None = None, timeout: int = 30):
        s = config.settings()
        self.contact = contact if contact is not None else s.get("contact", "")
        self.rate_limits = rate_limits or s.get("rate_limits", {"default": 1})
        self.timeout = timeout
        self.session = requests.Session()
        ua = f"earnsignal-research/0.1 ({self.contact})" if self.contact else "earnsignal-research/0.1"
        self.session.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._conn_fail: dict[str, int] = {}  # consecutive connection failures per host

    def _wait(self, host: str) -> None:
        rps = float(self.rate_limits.get(host, self.rate_limits.get("default", 1)))
        gap = 1.0 / max(rps, 0.01)
        with self._lock:
            last = self._last.get(host, 0.0)
            delay = last + gap - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()

    def request(self, method: str, url: str, *, retries: int = 4, headers: dict | None = None, **kw) -> requests.Response:
        host = urlparse(url).netloc
        if self._conn_fail.get(host, 0) >= 3:  # circuit breaker: host looks unreachable
            raise HostUnreachable(f"{host} unreachable (3 consecutive connection failures); skipping")
        backoff = 2.0
        for attempt in range(retries + 1):
            self._wait(host)
            try:
                r = self.session.request(method, url, timeout=self.timeout, headers=headers, **kw)
                self._conn_fail[host] = 0
            except (requests.ConnectionError, requests.Timeout):
                self._conn_fail[host] = self._conn_fail.get(host, 0) + 1
                if attempt == retries or self._conn_fail[host] >= 3:
                    raise
                time.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code in _RETRY_STATUS and attempt < retries:
                wait = float(r.headers.get("Retry-After", backoff)) if r.headers.get("Retry-After", "").isdigit() else backoff
                time.sleep(min(wait, 120))
                backoff *= 2
                continue
            return r
        return r  # pragma: no cover

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, **kw) -> requests.Response:
        return self.request("GET", url, params=params, headers=headers, **kw)

    def get_json(self, url: str, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    def post_json(self, url: str, payload: dict, **kw):
        r = self.request("POST", url, json=payload, **kw)
        r.raise_for_status()
        return r.json()
