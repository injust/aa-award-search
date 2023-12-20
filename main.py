from __future__ import annotations

import datetime as dt
import random
import sys
from collections.abc import Callable, Iterable, Sequence
from itertools import product

import httpx
import trio
from attrs import define, field, frozen, validators
from loguru import logger
from trio_typing import TaskStatus

import api
from config import pretty_printer
from flights import Availability
from utils import beep, compute_diff, format_diff


@define
class Job:
    @frozen
    class Query:
        origins: Iterable[str] = field(validator=[validators.min_len(1), validators.not_(validators.instance_of(str))])
        destinations: Iterable[str] = field(
            validator=[validators.min_len(1), validators.not_(validators.instance_of(str))]
        )
        dates: Iterable[dt.date]
        passengers: int = 1

        def queries(self) -> Iterable[api.Query]:
            for origin, destination, date in product(self.origins, self.destinations, self.dates):
                yield api.Query(origin, destination, date, self.passengers)

    query: Query
    frequency: dt.timedelta | None = None
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    scope: trio.CancelScope | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return (
            f"{'/'.join(self.query.origins)}-{'/'.join(self.query.destinations)} {'/'.join(map(str, self.query.dates))}"
        )

    def tasks(self) -> Iterable[Task]:
        for query in self.query.queries():
            yield Task(query, self.frequency, self.filters)  # pyright: ignore[reportGeneralTypeIssues]


@define
class Task:
    query: api.Query
    frequency: dt.timedelta | None = field(
        default=None, validator=validators.optional(validators.ge(dt.timedelta(minutes=1)))
    )
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    availability: Sequence[Availability] | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return f"{self.query.origin}-{self.query.destination}"


async def run_job(
    job: Job, httpx_client: httpx.AsyncClient, *, task_status: TaskStatus[trio.CancelScope] = trio.TASK_STATUS_IGNORED
) -> None:
    with trio.CancelScope() as scope:  # pyright: ignore[reportGeneralTypeIssues]
        async with trio.open_nursery() as n:
            for task in job.tasks():
                n.start_soon(run_task, task, httpx_client)
            task_status.started(scope)


async def run_task(task: Task, httpx_client: httpx.AsyncClient) -> None:
    async def run_task_once() -> None:
        try:
            availability = [
                avail
                async for avail in task.query.search(httpx_client)
                if all(filter(avail) for filter in task.filters)
            ]
        except httpx.TransportError as e:
            logger.warning(f"{e!r}")
            beep()
        except httpx.HTTPStatusError as e:
            if not e.response.is_server_error:
                raise e
            logger.warning(f"{e!r}, request_content={e.request.content.decode()}")
            beep()
        else:
            if (prev_availability := task.availability) is None:
                logger.info(f"{task.name}\n{pretty_printer().pformat(list(map(Availability.asdict, availability)))}\n")
                if availability:
                    beep()
            else:
                diff = list(compute_diff(prev_availability, availability))

                if any(change > " " for change, _ in diff):
                    logger.opt(colors=True).info(f"{task.name}\n{format_diff(diff)}\n")
                    if any(change == "+" for change, _ in diff):
                        beep(3)

            task.availability = availability

    if not task.frequency:
        await run_task_once()
        return

    await trio.sleep(random.uniform(0, task.frequency.total_seconds() / 2))

    while True:
        try:
            await run_task_once()
            await trio.sleep(task.frequency.total_seconds())
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError):
                assert not e.response.is_server_error
                logger.error(f"{e!r}, response_json={e.response.json()}, request_content={e.request.content.decode()}")
            elif isinstance(e, httpx.HTTPError):
                logger.exception(f"{e!r}")
            else:
                logger.exception(f"{e!r}, task={task}")

            beep()
            break


@logger.catch(onerror=lambda _: sys.exit(1))
async def main() -> None:
    jobs: list[Job] = []

    async with httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(5, read=10),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=60),
        base_url="https://www.aa.com/booking/api",
    ) as httpx_client, trio.open_nursery() as n:
        for job in jobs:
            job.scope = await n.start(run_job, job, httpx_client)


if __name__ == "__main__":
    trio.run(main, strict_exception_groups=True)
