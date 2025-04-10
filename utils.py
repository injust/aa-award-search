import time
from functools import cache, wraps
from pprint import PrettyPrinter
from typing import TYPE_CHECKING, Any

import httpx
import orjson as jsonlib
from httpx._config import DEFAULT_LIMITS

if TYPE_CHECKING:
    from collections.abc import Callable


def httpx_remove_HTTPStatusError_info_suffix(  # noqa: N802
    raise_for_status: Callable[[httpx.Response], httpx.Response],
) -> Callable[[httpx.Response], httpx.Response]:
    @wraps(raise_for_status)
    def wrapper(self: httpx.Response) -> httpx.Response:
        try:
            return raise_for_status(self)
        except httpx.HTTPStatusError as e:
            assert len(e.args) == 1 and isinstance(e.args[0], str), e.args
            message, removed = e.args[0].rsplit("\n", 1)
            assert removed.startswith("For more information check:"), removed
            e.args = (message,)
            raise

    return wrapper


# TODO(https://github.com/encode/httpx/issues/717)
@wraps(httpx.Response.json)
def httpx_response_jsonlib(self: httpx.Response, **kwargs: Any) -> Any:
    return jsonlib.loads(self.content, **kwargs)


def beep(times: int = 1, *, interval: float = 0.15) -> None:
    for _ in range(times):
        print(end="\a", flush=True)
        time.sleep(interval)


@cache
def httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
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


httpx.Response.json = httpx_response_jsonlib  # type: ignore[method-assign]
httpx.Response.raise_for_status = httpx_remove_HTTPStatusError_info_suffix(httpx.Response.raise_for_status)  # type: ignore[assignment, method-assign]  # pyright: ignore[reportAttributeAccessIssue]
