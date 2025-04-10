from __future__ import annotations

import datetime as dt
import random
import sys
from asyncio import CancelledError
from itertools import product
from typing import TYPE_CHECKING, ClassVar, Literal, Self

import anyio
import httpx
from anyio import create_task_group
from attrs import field, frozen
from attrs.validators import ge, instance_of, min_len, not_, optional
from dateutil.relativedelta import relativedelta
from loguru import logger
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from api import AvailabilityQuery, CalendarQuery, WeeklyQuery
from date_range import DayRange, MonthRange
from flights import Availability
from utils import beep, httpx_client, httpx_remove_HTTPStatusError_info_suffix, httpx_response_jsonlib, pretty_printer

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Generator, Iterable

    type DiffLine = tuple[Literal[" ", "+", "-"], Availability]

httpx.Response.json = httpx_response_jsonlib  # type: ignore[method-assign]
httpx.Response.raise_for_status = httpx_remove_HTTPStatusError_info_suffix(httpx.Response.raise_for_status)  # type: ignore[assignment, method-assign]  # pyright: ignore[reportAttributeAccessIssue]


@frozen
class Diff:
    lines: Collection[DiffLine]

    @classmethod
    def compare(cls, a: Iterable[Availability], b: Iterable[Availability]) -> Self:
        old = {avail.date: avail for avail in a}
        new = {avail.date: avail for avail in b}

        lines: list[DiffLine] = []
        for date in sorted(old.keys() | new.keys()):
            if old.get(date) == new.get(date):
                lines.append((" ", new[date]))
            else:
                if date in old:
                    lines.append(("-", old[date]))
                if date in new:
                    lines.append(("+", new[date]))

        return cls(lines)

    def colorize(self) -> str:
        LOGURU_COLOR_TAGS = {" ": "dim", "+": "green", "-": "red"}

        return "\n".join(
            f"<{LOGURU_COLOR_TAGS[change]}>{change}{pretty_printer().pformat(avail._asdict())}</>"
            for change, avail in self.lines
        )


@frozen
class Job:
    @frozen
    class Query:
        @frozen
        class QueryRange(DayRange):
            SEARCH_LIMIT: ClassVar[dt.timedelta] = dt.timedelta(days=331)
            SEARCH_RADIUS: ClassVar[dt.timedelta] = dt.timedelta(days=6)
            SEARCH_WIDTH: ClassVar[dt.timedelta] = SEARCH_RADIUS * 2 + dt.timedelta(days=1)

            def __attrs_post_init__(self) -> None:
                assert self.step
                if self.step < dt.timedelta():
                    raise ValueError("`step` must be positive")
                if self.start < dt.date.today():
                    raise ValueError("`start` cannot be in the past")
                if self.stop > dt.date.today() + self.SEARCH_LIMIT:
                    raise ValueError(f"`stop` cannot be more than {self.SEARCH_LIMIT.days} days in the future")
                if not bool(self):
                    raise ValueError("`QueryRange` must be a non-empty range")

            def calendar_dates(self) -> Generator[list[dt.date]]:
                for first_day in MonthRange(self.start, self.stop):
                    last_day = first_day + relativedelta(months=+1, days=-1)
                    yield list(Job.Query.QueryRange(first_day, last_day).weekly_dates())

            def weekly_dates(self) -> Generator[dt.date]:
                yield (date := min(self.stop, self.start + self.SEARCH_RADIUS))
                while date + self.SEARCH_RADIUS < self.stop:
                    yield (date := min(self.stop, date + self.SEARCH_WIDTH))

        origins: Collection[str] = field(validator=[min_len(1), not_(instance_of(str))])
        destinations: Collection[str] = field(validator=[min_len(1), not_(instance_of(str))])
        dates: QueryRange
        passengers: int = 1

    query: Query
    frequency: dt.timedelta | None = None
    filters: Collection[Callable[[Availability], bool]] = ()
    label: str = ""

    def calendar_tasks(self) -> Generator[Task]:
        for origin, destination, dates in product(
            self.query.origins, self.query.destinations, self.query.dates.calendar_dates()
        ):

            def is_date_in_range(avail: Availability) -> bool:
                return avail.date in self.query.dates

            yield Task(
                f"{self.label} {origin}-{destination} {dates[0]:%Y-%m}".lstrip(),
                [CalendarQuery(origin, destination, date, self.query.passengers) for date in dates],
                self.frequency,
                [*self.filters, is_date_in_range],
            )

    def weekly_tasks(self) -> Generator[Task]:
        for origin, destination, date in product(
            self.query.origins, self.query.destinations, self.query.dates.weekly_dates()
        ):

            def is_date_in_range(avail: Availability) -> bool:
                return avail.date in self.query.dates

            yield Task(
                f"{self.label} {origin}-{destination} {date}".lstrip(),
                [WeeklyQuery(origin, destination, date, self.query.passengers)],
                self.frequency,
                [*self.filters, is_date_in_range],
            )

    async def run(self) -> None:
        async with create_task_group() as task_group:
            for task in self.calendar_tasks():
                task_group.start_soon(task.run)


@frozen
class Task:
    name: str
    queries: Collection[AvailabilityQuery]
    frequency: dt.timedelta | None = field(default=None, validator=optional(ge(dt.timedelta(minutes=1))))
    filters: Collection[Callable[[Availability], bool]] = ()

    async def run(self) -> None:
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(),
            retry=retry_if_exception(lambda e: isinstance(e, httpx.HTTPStatusError) and e.response.is_server_error),
            before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        )
        @retry(
            stop=stop_after_attempt(10),
            wait=wait_exponential(max=32),
            retry=retry_if_exception_type(httpx.TransportError),
            before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        )
        async def run_once() -> list[Availability]:
            for query in self.queries:
                if availability := [
                    avail async for avail in query.search() if all(filter(avail) for filter in self.filters)
                ]:
                    return availability
            return []

        availability: list[Availability] | None = None

        if self.frequency is None:
            availability = await run_once()

            logger.info("{}\n{}\n", self.name, pretty_printer().pformat(list(map(Availability._asdict, availability))))
            if availability:
                beep()

            return

        await anyio.sleep(random.uniform(0, self.frequency.total_seconds() / 2))

        while True:
            try:
                prev_availability, availability = availability, await run_once()
            except Exception as e:
                match e:
                    case httpx.HTTPStatusError():
                        # Already logged in `Query._send_query()`
                        pass
                    case httpx.HTTPError():
                        logger.exception("{!r}", e)
                    case _:
                        logger.exception("{!r}, task={}", e, self)

                beep()
                break
            else:
                if prev_availability is None:
                    logger.info(
                        "{}\n{}\n", self.name, pretty_printer().pformat(list(map(Availability._asdict, availability)))
                    )
                    if availability:
                        beep()
                else:
                    diff = Diff.compare(prev_availability, availability)

                    if any(change > " " for change, _ in diff.lines):
                        logger.opt(colors=True).info(f"{self.name}\n{diff.colorize()}\n")
                        if any(change == "+" for change, _ in diff.lines):
                            beep(3)

                await anyio.sleep(self.frequency.total_seconds())


@logger.catch(onerror=lambda _: sys.exit(1))
async def main() -> None:
    jobs: list[Job] = []

    try:
        async with httpx_client(), create_task_group() as task_group:
            for job in jobs:
                task_group.start_soon(job.run)
    except* (CancelledError, KeyboardInterrupt):
        logger.debug("Shutting down")


if __name__ == "__main__":
    anyio.run(main)
