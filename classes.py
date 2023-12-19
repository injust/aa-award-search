from __future__ import annotations

import datetime as dt
from itertools import product
from typing import Callable, Iterable, Sequence

import attrs
import trio
from attrs import Attribute, define, field, frozen, validators


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


@define
class Job:
    multi_query: MultiQuery
    frequency: dt.timedelta
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    scope: trio.CancelScope | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return f"{'/'.join(self.multi_query.origins)}-{'/'.join(self.multi_query.destinations)} {'/'.join(map(str, self.multi_query.dates))}"

    def to_tasks(self) -> Iterable[Task]:
        for query in self.multi_query.to_queries():
            yield Task(query, self.frequency, self.filters)  # pyright: ignore[reportGeneralTypeIssues]


@frozen
class MultiQuery:
    origins: Iterable[str] = field(validator=validators.not_(validators.instance_of(str)))
    destinations: Iterable[str] = field(validator=validators.not_(validators.instance_of(str)))
    dates: Iterable[dt.date]
    passengers: int = 1

    def to_queries(self) -> Iterable[Query]:
        for origin, destination, date in product(self.origins, self.destinations, self.dates):
            yield Query(origin, destination, date, self.passengers)


@frozen
class Query:
    origin: str
    destination: str
    date: dt.date
    passengers: int = field(
        default=1, validator=[validators.ge(1), validators.le(9)]  # pyright: ignore[reportGeneralTypeIssues]
    )


@define
class Task:
    query: Query
    frequency: dt.timedelta = field(validator=validators.ge(dt.timedelta(minutes=1)))
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    availability: Sequence[Availability] | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return f"{self.query.origin}-{self.query.destination}"
