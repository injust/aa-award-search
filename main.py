from __future__ import annotations

import datetime as dt
import random
import sys
from collections.abc import Callable, Iterable, Sequence
from contextlib import aclosing
from itertools import product
from typing import Literal, Self

import httpx
import trio
from attrs import define, field, frozen, validators
from loguru import logger
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from trio_typing import TaskStatus

from api import AvailabilityQuery, CalendarQuery, WeeklyQuery
from config import httpx_client, pretty_printer
from flights import Availability
from utils import beep

type DiffLine = tuple[Literal[" ", "+", "-"], Availability]

SEARCH_RADIUS = dt.timedelta(days=6)
SEARCH_WIDTH = SEARCH_RADIUS * 2 + dt.timedelta(days=1)


@frozen
class Diff:
    lines: Sequence[DiffLine]

    @classmethod
    def from_availability(cls, a: Iterable[Availability], b: Iterable[Availability]) -> Self:
        old = {avail.date: avail for avail in a}
        new = {avail.date: avail for avail in b}

        lines: list[DiffLine] = []
        for date in sorted(old.keys() | new.keys()):
            if old.get(date, None) == new.get(date, None):
                lines.append((" ", new[date]))
            else:
                if date in old:
                    lines.append(("-", old[date]))
                if date in new:
                    lines.append(("+", new[date]))

        return cls(lines)

    def colorized(self) -> str:
        LOGURU_COLOR_TAGS = {" ": "dim", "+": "green", "-": "red"}

        return "\n".join(
            f"<{LOGURU_COLOR_TAGS[change]}>{change}{pretty_printer().pformat(avail.asdict())}</>"
            for change, avail in self.lines
        )


@define
class Job:
    @frozen
    class Query:
        origins: Iterable[str] = field(validator=[validators.min_len(1), validators.not_(validators.instance_of(str))])
        destinations: Iterable[str] = field(
            validator=[validators.min_len(1), validators.not_(validators.instance_of(str))]
        )
        date_range: tuple[dt.date, dt.date]
        passengers: int = 1

        def calendar(self) -> Iterable[CalendarQuery]:
            return NotImplemented  # TODO

        def weekly(self) -> Iterable[WeeklyQuery]:
            dates = [min(self.date_range[1], self.date_range[0] + SEARCH_RADIUS)]
            while dates[-1] + SEARCH_RADIUS < self.date_range[1]:
                dates.append(min(self.date_range[1], dates[-1] + SEARCH_WIDTH))

            for origin, destination, date in product(self.origins, self.destinations, dates):
                yield WeeklyQuery(origin, destination, date, self.passengers)

    query: Query
    frequency: dt.timedelta | None = None
    filters: Iterable[Callable[[Availability], bool]] = ()
    label: str | None = None
    scope: trio.CancelScope | None = None

    @property
    def name(self) -> str:
        return f"{self.label or ''} {'/'.join(self.query.origins)}-{'/'.join(self.query.destinations)} {'-'.join(map((lambda date: date.strftime('%Y/%m/%d')), self.query.date_range))}".lstrip()

    def tasks(self) -> Iterable[Task]:
        for query in self.query.weekly():
            yield Task(
                f"{self.label or ''} {query.origin}-{query.destination} {query.date}".lstrip(),
                query,
                self.query.date_range,
                self.frequency,
                self.filters,
            )


@define
class Task:
    name: str
    query: AvailabilityQuery
    date_range: tuple[dt.date, dt.date]
    frequency: dt.timedelta | None = field(
        default=None, validator=validators.optional(validators.ge(dt.timedelta(minutes=1)))
    )
    filters: Iterable[Callable[[Availability], bool]] = ()
    availability: Sequence[Availability] | None = None


async def run_job(job: Job, *, task_status: TaskStatus[trio.CancelScope] = trio.TASK_STATUS_IGNORED) -> None:
    with trio.CancelScope() as scope:  # pyright: ignore[reportGeneralTypeIssues]
        async with trio.open_nursery() as n:
            for task in job.tasks():
                n.start_soon(run_task, task)
            task_status.started(scope)


async def run_task(task: Task) -> None:
    @retry(
        sleep=trio.sleep,
        stop=stop_after_attempt(3),
        wait=wait_exponential(),
        retry=retry_if_exception(lambda e: isinstance(e, httpx.HTTPStatusError) and e.response.is_server_error),
        before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
    )
    @retry(
        sleep=trio.sleep,
        stop=stop_after_attempt(10),
        wait=wait_exponential(max=32),
        retry=retry_if_exception_type(httpx.TransportError),
        before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
    )
    async def run_task_once() -> list[Availability]:
        try:
            return [
                avail
                async for avail in task.query.search()
                if task.date_range[0] <= avail.date <= task.date_range[1]
                and all(filter(avail) for filter in task.filters)
            ]
        except httpx.HTTPStatusError as e:
            if e.response.is_server_error:
                logger.debug(f"{e!r}, response_json={e.response.json()}, request_content={e.request.content.decode()}")
            raise e

    if not task.frequency:
        availability = await run_task_once()

        logger.info(f"{task.name}\n{pretty_printer().pformat(list(map(Availability.asdict, availability)))}\n")
        if availability:
            beep()

        return

    await trio.sleep(random.uniform(0, task.frequency.total_seconds() / 2))

    while True:
        try:
            prev_availability, task.availability = task.availability, await run_task_once()

            if prev_availability is None:
                logger.info(
                    f"{task.name}\n{pretty_printer().pformat(list(map(Availability.asdict, task.availability)))}\n"
                )
                if task.availability:
                    beep()
            else:
                diff = Diff.from_availability(prev_availability, task.availability)

                if any(change > " " for change, _ in diff.lines):
                    logger.opt(colors=True).info(f"{task.name}\n{diff.colorized()}\n")
                    if any(change == "+" for change, _ in diff.lines):
                        beep(3)

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

    try:
        async with aclosing(httpx_client()), trio.open_nursery() as n:
            for job in jobs:
                job.scope = await n.start(run_job, job)
    except* KeyboardInterrupt:
        logger.debug("Shutting down")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, diagnose=True)

    trio.run(main, strict_exception_groups=True)
