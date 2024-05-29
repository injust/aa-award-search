from __future__ import annotations

from functools import cache
from pprint import PrettyPrinter
from time import sleep

import httpx
from httpx._config import DEFAULT_LIMITS


@cache
def httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
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
        print("\a", end="", flush=True)
        sleep(interval)
