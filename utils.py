from __future__ import annotations

import time
from functools import cache
from pprint import PrettyPrinter

import httpx
from httpx._config import DEFAULT_LIMITS


@cache
def httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        },
        http2=True,
        timeout=httpx.Timeout(5, read=10),
        limits=httpx.Limits(
            max_connections=DEFAULT_LIMITS.max_connections,
            max_keepalive_connections=DEFAULT_LIMITS.max_keepalive_connections,
            keepalive_expiry=60,
        ),
        base_url="https://www.aa.com/booking/api/",
    )


@cache
def pretty_printer() -> PrettyPrinter:
    return PrettyPrinter(width=120, sort_dicts=False, underscore_numbers=True)


def beep(times: int = 1, interval: float = 0.1) -> None:
    for _ in range(times):
        print(end="\a", flush=True)
        time.sleep(interval)
