import datetime as dt
import sys
from collections.abc import Callable, Collection, Iterable, Sequence
from contextlib import suppress
from itertools import product
from random import randrange
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

SEARCH_LIMIT = dt.timedelta(days=331)
SEARCH_RADIUS = dt.timedelta(days=6)
SEARCH_WIDTH = SEARCH_RADIUS * 2 + dt.timedelta(days=1)


def _constrain_query_date_range(date_range: tuple[dt.date, dt.date]) -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    start = max(today, date_range[0])
    stop = min(today + SEARCH_LIMIT, date_range[1])
    return start, stop


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
        origins: Iterable[str] = field(validator=[validators.min_len(1), validators.not_(validators.instance_of(str))])
        destinations: Iterable[str] = field(
            validator=[validators.min_len(1), validators.not_(validators.instance_of(str))]
        )
        date_range: tuple[dt.date, dt.date] = field(converter=_constrain_query_date_range)
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
    label: str = ""

    @property
    def name(self) -> str:
        return f"{self.label}{self.label and ' '}{'/'.join(self.query.origins)}-{'/'.join(self.query.destinations)} {'-'.join(map((lambda date: date.strftime('%Y/%m/%d')), self.query.date_range))}"

    def tasks(self) -> Iterable[Task]:
        for query in self.query.weekly():

            def is_date_in_range(avail: Availability) -> bool:
                return self.query.date_range[0] <= avail.date <= self.query.date_range[1]

            yield Task(
                f"{self.label}{self.label and ' '}{query.origin}-{query.destination} {query.date}",
                query,
                self.frequency,
                [*self.filters, is_date_in_range],
            )

    async def run(self, *, task_status: TaskStatus[trio.CancelScope] = trio.TASK_STATUS_IGNORED) -> None:
        with trio.CancelScope() as scope:  # pyright: ignore[reportGeneralTypeIssues]
            async with trio.open_nursery() as nursery:
                for task in self.tasks():
                    nursery.start_soon(task.run)
                task_status.started(scope)


@define
class Task:
    name: str
    query: AvailabilityQuery
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
            try:
                return [avail async for avail in self.query.search() if all(filter(avail) for filter in self.filters)]
            except httpx.HTTPStatusError as e:
                if e.response.is_server_error:
                    logger.debug(
                        f"{e!r}, response_json={e.response.json()}, request_content={e.request.content.decode()}"
                    )
                raise e

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
