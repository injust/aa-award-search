from __future__ import annotations

import datetime as dt
from itertools import product
from typing import Callable, Iterable, Literal, Self, Sequence, TypeAlias

import trio
from attrs import define, field, frozen, validators

from classes.flights import Availability
from classes.queries import CalendarQuery, MultiQuery, WeeklyQuery
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
    multi_query: MultiQuery
    frequency: dt.timedelta | None = None
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    scope: trio.CancelScope | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return f"{'/'.join(self.multi_query.origins)}-{'/'.join(self.multi_query.destinations)} {self.multi_query.search_from.strftime('%Y/%m/%d')}-{self.multi_query.search_to.strftime('%Y/%m/%d')}"

    def to_tasks(self) -> Iterable[Task]:
        searches = [self.multi_query.search_from + dt.timedelta(days=6)]
        while searches[-1] + dt.timedelta(days=6) < self.multi_query.search_to:
            searches.append(searches[-1] + dt.timedelta(days=13))

        for origin, destination, date in product(self.multi_query.origins, self.multi_query.destinations, searches):
            query = WeeklyQuery(origin, destination, date, self.multi_query.passengers)
            yield Task(
                query, self.multi_query.search_from, self.multi_query.search_to, self.frequency, self.filters
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
