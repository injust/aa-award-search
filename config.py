from functools import cache
from pprint import PrettyPrinter

import httpx

_httpx_client: httpx.AsyncClient | None = None


def httpx_client() -> httpx.AsyncClient:
    global _httpx_client

    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(5, read=10),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=60),
            base_url="https://www.aa.com/booking/api",
        )
    else:
        assert not _httpx_client.is_closed

    return _httpx_client


@cache
def pretty_printer() -> PrettyPrinter:
    return PrettyPrinter(width=120, sort_dicts=False, underscore_numbers=True)
