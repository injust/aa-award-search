from __future__ import annotations

import datetime as dt
from itertools import product
from typing import Callable, Iterable, Literal, Self, Sequence, TypeAlias

import trio
from attrs import define, field, frozen, validators

from classes.flights import Availability
from classes.queries import BatchQuery, CalendarQuery, WeeklyQuery
from config import pretty_printer

DiffLine: TypeAlias = tuple[Literal[" ", "+", "-"], Availability]


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
    batch_query: BatchQuery
    frequency: dt.timedelta | None = None
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    scope: trio.CancelScope | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return f"{'/'.join(self.batch_query.origins)}-{'/'.join(self.batch_query.destinations)} {self.batch_query.search_from.strftime('%Y/%m/%d')}-{self.batch_query.search_to.strftime('%Y/%m/%d')}"

    def to_tasks(self) -> Iterable[Task]:
        searches = [self.batch_query.search_from + dt.timedelta(days=6)]
        while searches[-1] + dt.timedelta(days=6) < self.batch_query.search_to:
            searches.append(searches[-1] + dt.timedelta(days=13))

        for origin, destination, date in product(self.batch_query.origins, self.batch_query.destinations, searches):
            query = WeeklyQuery(origin, destination, date, self.batch_query.passengers)
            yield Task(
                query, self.batch_query.search_from, self.batch_query.search_to, self.frequency, self.filters
            )  # pyright: ignore[reportGeneralTypeIssues]


@define
class Task:
    query: CalendarQuery | WeeklyQuery
    search_from: dt.date
    search_to: dt.date
    frequency: dt.timedelta | None = field(
        default=None, validator=validators.optional(validators.ge(dt.timedelta(minutes=1)))
    )
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    availability: Sequence[Availability] | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return f"{self.query.origin}-{self.query.destination} {self.search_from.strftime('%Y/%m/%d')}-{self.search_to.strftime('%Y/%m/%d')}"
