"""Shared HTTP client: timeout, retry with backoff, and an on-disk cache.

Historic data (finished seasons) never changes, so it is cached forever;
live data (current season, Polymarket prices) uses a short TTL.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (Foot8 research)"


class FetchError(RuntimeError):
    """A request failed after retries."""


def _cache_path(url: str, params: dict[str, Any] | None) -> Path:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    return CACHE_DIR / hashlib.sha256(key.encode()).hexdigest()[:24]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
def _get(url: str, params: dict[str, Any] | None, headers: dict[str, str]) -> bytes:
    with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, params=params, headers={"User-Agent": USER_AGENT, **headers})
        if resp.status_code >= 500 or resp.status_code == 429:
            resp.raise_for_status()
        if resp.status_code >= 400:
            raise FetchError(f"{resp.status_code} for {url}")
        return resp.content


def is_cached(url: str, params: dict[str, Any] | None, ttl_seconds: float | None) -> bool:
    """True when fetch_* would be served from the disk cache."""
    path = _cache_path(url, params)
    if ttl_seconds == 0 or not path.exists():
        return False
    return ttl_seconds is None or time.time() - path.stat().st_mtime < ttl_seconds


def fetch_bytes(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl_seconds: float | None = 0,
) -> bytes:
    """GET ``url``.  ttl_seconds: None = cache forever, 0 = never cache."""
    path = _cache_path(url, params)
    if ttl_seconds != 0 and path.exists():
        age = time.time() - path.stat().st_mtime
        if ttl_seconds is None or age < ttl_seconds:
            return path.read_bytes()

    try:
        content = _get(url, params, headers or {})
    except (httpx.HTTPError, FetchError) as e:
        if path.exists():
            logger.warning(f"Fetch failed for {url} ({e}); using stale cache")
            return path.read_bytes()
        raise FetchError(f"GET {url} failed: {e}") from e

    if ttl_seconds != 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return content


def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl_seconds: float | None = 0,
) -> Any:
    return json.loads(fetch_bytes(url, params, headers, ttl_seconds))
