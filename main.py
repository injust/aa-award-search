import datetime as dt
import sys
from collections.abc import Callable, Collection, Iterable, Sequence
from contextlib import suppress
from itertools import product
from random import randrange
from typing import ClassVar, Literal, Self

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

from api import AvailabilityQuery, WeeklyQuery
from config import httpx_client, pretty_printer
from date_range import DayRange
from flights import Availability
from utils import beep

type DiffLine = tuple[Literal[" ", "+", "-"], Availability]


@frozen
class Diff:
    lines: Collection[DiffLine]

    @classmethod
    def compare(cls, a: Iterable[Availability], b: Iterable[Availability]) -> Self:
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
        @frozen
        class QueryRange(DayRange):
            SEARCH_LIMIT: ClassVar[dt.timedelta] = dt.timedelta(days=331)
            SEARCH_RADIUS: ClassVar[dt.timedelta] = dt.timedelta(days=6)
            SEARCH_WIDTH: ClassVar[dt.timedelta] = SEARCH_RADIUS * 2 + dt.timedelta(days=1)

            def __attrs_post_init__(self) -> None:
                if self.step <= dt.timedelta():
                    raise ValueError("`step` must be positive")
                elif not self:
                    raise ValueError("`QueryRange` must be a non-empty range")

                if self.start < (min_start := dt.date.today()):
                    object.__setattr__(self, "start", min_start)
                if self.stop > (max_stop := dt.date.today() + self.SEARCH_LIMIT):
                    object.__setattr__(self, "stop", max_stop)

            def calendar_dates(self) -> Iterable[list[dt.date]]:
                return NotImplemented

            def weekly_dates(self) -> Iterable[dt.date]:
                yield (date := min(self.stop, self.start + self.SEARCH_RADIUS))
                while date + self.SEARCH_RADIUS < self.stop:
                    yield (date := min(self.stop, date + self.SEARCH_WIDTH))

        origins: Iterable[str] = field(validator=[validators.min_len(1), validators.not_(validators.instance_of(str))])
        destinations: Iterable[str] = field(
            validator=[validators.min_len(1), validators.not_(validators.instance_of(str))]
        )
        dates: QueryRange
        passengers: int = 1

    query: Query
    frequency: dt.timedelta | None = None
    filters: Iterable[Callable[[Availability], bool]] = ()
    label: str = ""

    @property
    def name(self) -> str:
        return f"{self.label}{self.label and ' '}{'/'.join(self.query.origins)}-{'/'.join(self.query.destinations)} {self.query.dates}"

    def calendar_tasks(self) -> Iterable[Task]:
        return NotImplemented

    def weekly_tasks(self) -> Iterable[Task]:
        for origin, destination, date in product(
            self.query.origins, self.query.destinations, self.query.dates.weekly_dates()
        ):

            def is_date_in_range(avail: Availability) -> bool:
                return avail.date in self.query.dates

            yield Task(
                f"{self.label}{self.label and ' '}{origin}-{destination} {date}",
                [WeeklyQuery(origin, destination, date, self.query.passengers)],
                self.frequency,
                [*self.filters, is_date_in_range],
            )

    async def run(self, *, task_status: TaskStatus[trio.CancelScope] = trio.TASK_STATUS_IGNORED) -> None:
        with trio.CancelScope() as scope:  # pyright: ignore[reportGeneralTypeIssues]
            async with trio.open_nursery() as nursery:
                for task in self.weekly_tasks():
                    nursery.start_soon(task.run)
                task_status.started(scope)


@define
class Task:
    name: str
    queries: Iterable[AvailabilityQuery]
    frequency: dt.timedelta | None = field(
        default=None, validator=validators.optional(validators.ge(dt.timedelta(minutes=1)))
    )
    filters: Iterable[Callable[[Availability], bool]] = ()
    availability: Sequence[Availability] | None = None

    async def run(self) -> None:
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(),
            retry=retry_if_exception(lambda e: isinstance(e, httpx.HTTPStatusError) and e.response.is_server_error),
            before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
        )
        @retry(
            stop=stop_after_attempt(10),
            wait=wait_exponential(max=32),
            retry=retry_if_exception_type(httpx.TransportError),
            before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
        )
        async def run_once() -> list[Availability]:
            for query in self.queries:
                try:
                    if availability := [
                        avail async for avail in query.search() if all(filter(avail) for filter in self.filters)
                    ]:
                        return availability
                except httpx.HTTPStatusError as e:
                    if e.response.is_server_error:
                        logger.debug(
                            f"{e!r}, response_json={e.response.json()}, request_content={e.request.content.decode()}"
                        )
                    raise e
            return []

        if not self.frequency:
            availability = await run_once()

            logger.info(f"{self.name}\n{pretty_printer().pformat(list(map(Availability.asdict, availability)))}\n")
            if availability:
                beep()

            return

        jitter = randrange(int(self.frequency.total_seconds() // 2))
        await trio.sleep(jitter)

        while True:
            try:
                prev_availability, self.availability = self.availability, await run_once()

                if prev_availability is None:
                    logger.info(
                        f"{self.name}\n{pretty_printer().pformat(list(map(Availability.asdict, self.availability)))}\n"
                    )
                    if self.availability:
                        beep()
                else:
                    diff = Diff.compare(prev_availability, self.availability)

                    if any(change > " " for change, _ in diff.lines):
                        logger.opt(colors=True).info(f"{self.name}\n{diff.colorized()}\n")
                        if any(change == "+" for change, _ in diff.lines):
                            beep(3)

                await trio.sleep(self.frequency.total_seconds())
            except Exception as e:
                if isinstance(e, httpx.HTTPStatusError):
                    assert not e.response.is_server_error
                    logger.error(
                        f"{e!r}, response_json={e.response.json()}, request_content={e.request.content.decode()}"
                    )
                elif isinstance(e, httpx.HTTPError):
                    logger.exception(f"{e!r}")
                else:
                    logger.exception(f"{e!r}, task={self}")

                beep()
                break


@logger.catch(onerror=lambda _: sys.exit(1))
async def main() -> None:
    jobs: list[Job] = []

    with suppress(KeyboardInterrupt):
        async with httpx_client(), trio.open_nursery() as nursery:
            for job in jobs:
                nursery.start_soon(job.run)  # pyright: ignore[reportGeneralTypeIssues]


if __name__ == "__main__":
    trio.run(main)
