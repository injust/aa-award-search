from functools import cache
from pprint import PrettyPrinter

import httpx


@cache
def httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(5, read=10),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=60),
        base_url="https://www.aa.com/booking/api",
    )


@cache
def pretty_printer() -> PrettyPrinter:
    return PrettyPrinter(width=120, sort_dicts=False, underscore_numbers=True)
