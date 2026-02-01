from functools import wraps
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Never

import httpx
import orjson as jsonlib
from httpx._config import DEFAULT_LIMITS
from wrapt import function_wrapper

if TYPE_CHECKING:
    from collections.abc import Callable

HTTPX_LIMITS = httpx.Limits(
    max_connections=DEFAULT_LIMITS.max_connections,
    max_keepalive_connections=DEFAULT_LIMITS.max_keepalive_connections,
    keepalive_expiry=60,
)


async def httpx_make_status_code_HTTPStatus(r: httpx.Response) -> None:  # noqa: N802
    r.status_code = HTTPStatus(r.status_code)


@function_wrapper
def httpx_remove_HTTPStatusError_info_suffix(  # noqa: N802
    raise_for_status: Callable[[], httpx.Response], _instance: httpx.Response, args: tuple[()], kwargs: dict[str, Never]
) -> httpx.Response:
    try:
        return raise_for_status(*args, **kwargs)
    except httpx.HTTPStatusError as e:
        assert len(e.args) == 1 and isinstance(e.args[0], str), e.args
        message, removed = e.args[0].rsplit("\n", 1)
        assert removed.startswith("For more information check:"), removed
        e.args = (message,)
        raise


# TODO(https://github.com/encode/httpx/issues/717)
@wraps(httpx.Response.json)
def httpx_response_jsonlib(self: httpx.Response, **kwargs: Any) -> Any:
    return jsonlib.loads(self.content, **kwargs)


httpx.Response.json = httpx_response_jsonlib
httpx.Response.raise_for_status = httpx_remove_HTTPStatusError_info_suffix(httpx.Response.raise_for_status)
httpx_client = httpx.AsyncClient(
    headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    },
    http2=True,
    timeout=httpx.Timeout(5, read=10),
    limits=HTTPX_LIMITS,
    event_hooks={"response": [httpx_make_status_code_HTTPStatus]},
    base_url="https://www.aa.com/booking/api/",
)
