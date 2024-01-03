from __future__ import annotations

import datetime as dt
from itertools import product
from typing import Callable, Iterable, Literal, Self, Sequence, TypeAlias

import attrs
import trio
from attrs import Attribute, define, field, frozen, validators

from config import pretty_printer


@frozen
class Availability:
    @frozen
    class Fees:
        amount: float
        currency: str

    date: dt.date
    miles: int
    fees: Fees

    def asdict(self) -> dict[str, object]:
        return attrs.asdict(self, value_serializer=self._serialize)

    @staticmethod
    def _serialize(inst: type, attr: Attribute[object], value: object) -> object:
        if isinstance(value, dt.date):
            return value.isoformat()
        return value


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


@frozen
class MultiQuery:
    origins: Iterable[str] = field(validator=validators.not_(validators.instance_of(str)))
    destinations: Iterable[str] = field(validator=validators.not_(validators.instance_of(str)))
    search_from: dt.date
    search_to: dt.date
    passengers: int = 1


@frozen
class Query:
    origin: str
    destination: str
    date: dt.date
    passengers: int = field(
        default=1, validator=[validators.ge(1), validators.le(9)]  # pyright: ignore[reportGeneralTypeIssues]
    )


@frozen
class CalendarQuery(Query):
    pass


@frozen
class WeeklyQuery(Query):
    pass


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
