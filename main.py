from __future__ import annotations

import datetime as dt
import random
import sys
from asyncio import CancelledError
from collections.abc import Callable, Iterable, Sequence
from functools import cached_property
from itertools import product
from typing import Literal, Self

import anyio
import httpx
from anyio import create_task_group, run
from attrs import define, field, frozen
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
from utils import beep, httpx_client, pretty_printer

type DiffLine = tuple[Literal[" ", "+", "-"], Availability]


@frozen
class Diff:
    lines: Sequence[DiffLine]

    @classmethod
    def from_availability(cls, a: Iterable[Availability], b: Iterable[Availability]) -> Self:
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

    @cached_property
    def colorized(self) -> str:
        LOGURU_COLOR_TAGS = {" ": "dim", "+": "green", "-": "red"}

        return "\n".join(
            f"<{LOGURU_COLOR_TAGS[change]}>{change}{pretty_printer().pformat(avail.asdict())}</>"
            for change, avail in self.lines
        )


@frozen
class Job:
    @frozen
    class Query:
        @frozen
        class QueryRange(DayRange):
            SEARCH_LIMIT = dt.timedelta(days=331)
            SEARCH_RADIUS = dt.timedelta(days=6)
            SEARCH_WIDTH = SEARCH_RADIUS * 2 + dt.timedelta(days=1)

            def __attrs_post_init__(self) -> None:
                if self.step <= dt.timedelta():
                    raise ValueError("`step` must be positive")
                elif not bool(self):
                    raise ValueError("`QueryRange` must not be an empty range")

                if self.start < (min_start := dt.date.today()):
                    object.__setattr__(self, "start", min_start)
                if self.stop > (max_stop := dt.date.today() + self.SEARCH_LIMIT):
                    object.__setattr__(self, "stop", max_stop)

            def calendar_dates(self) -> Iterable[list[dt.date]]:
                for first_day in MonthRange(self.start, self.stop):
                    last_day = first_day + relativedelta(months=+1, days=-1)
                    yield list(Job.Query.QueryRange(first_day, last_day).weekly_dates())

            def weekly_dates(self) -> Iterable[dt.date]:
                yield (date := min(self.stop, self.start + self.SEARCH_RADIUS))
                while date + self.SEARCH_RADIUS < self.stop:
                    yield (date := min(self.stop, date + self.SEARCH_WIDTH))

        origins: Iterable[str] = field(validator=[min_len(1), not_(instance_of(str))])
        destinations: Iterable[str] = field(validator=[min_len(1), not_(instance_of(str))])
        dates: QueryRange
        passengers: int = 1

    query: Query
    frequency: dt.timedelta | None = None
    filters: Iterable[Callable[[Availability], bool]] = ()
    label: str | None = None

    @cached_property
    def name(self) -> str:
        return f"{self.label or ""} {"/".join(self.query.origins)}-{"/".join(self.query.destinations)} {self.query.dates}".lstrip()

    def calendar_tasks(self) -> Iterable[Task]:
        for origin, destination, dates in product(
            self.query.origins, self.query.destinations, self.query.dates.calendar_dates()
        ):

            def date_in_range(avail: Availability) -> bool:
                return avail.date in self.query.dates

            yield Task(
                f"{self.label or ""} {origin}-{destination} {dates[0].strftime("%Y-%m")}".lstrip(),
                [CalendarQuery(origin, destination, date, self.query.passengers) for date in dates],
                self.frequency,
                [*self.filters, date_in_range],
            )

    def weekly_tasks(self) -> Iterable[Task]:
        for origin, destination, date in product(
            self.query.origins, self.query.destinations, self.query.dates.weekly_dates()
        ):

            def date_in_range(avail: Availability) -> bool:
                return avail.date in self.query.dates

            yield Task(
                f"{self.label or ""} {origin}-{destination} {date}".lstrip(),
                [WeeklyQuery(origin, destination, date, self.query.passengers)],
                self.frequency,
                [*self.filters, date_in_range],
            )

    async def run(self) -> None:
        async with create_task_group() as tg:
            for task in self.calendar_tasks():
                tg.start_soon(task.run)


@define
class Task:
    name: str
    queries: Iterable[AvailabilityQuery]
    frequency: dt.timedelta | None = field(default=None, validator=optional(ge(dt.timedelta(minutes=1))))
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
                if availability := [
                    avail async for avail in query.search() if all(filter(avail) for filter in self.filters)
                ]:
                    return availability
            else:
                return []

        if self.frequency is None:
            availability = await run_once()

            logger.info("{}\n{}\n", self.name, pretty_printer().pformat(list(map(Availability.asdict, availability))))
            if availability:
                beep()

            return

        await anyio.sleep(random.uniform(0, self.frequency.total_seconds() / 2))

        while True:
            try:
                prev_availability, self.availability = self.availability, await run_once()
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
                        "{}\n{}\n",
                        self.name,
                        pretty_printer().pformat(list(map(Availability.asdict, self.availability))),
                    )
                    if self.availability:
                        beep()
                else:
                    diff = Diff.from_availability(prev_availability, self.availability)

                    if any(change > " " for change, _ in diff.lines):
                        logger.opt(colors=True).info(f"{{}}\n{diff.colorized}\n", self.name)
                        if any(change == "+" for change, _ in diff.lines):
                            beep(3)

                await anyio.sleep(self.frequency.total_seconds())


@logger.catch(onerror=lambda _: sys.exit(1))
async def main() -> None:
    jobs: list[Job] = []

    try:
        async with httpx_client(), create_task_group() as tg:
            for job in jobs:
                tg.start_soon(job.run)
    except* (CancelledError, KeyboardInterrupt):
        logger.debug("Shutting down")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, diagnose=True)

    run(main)
