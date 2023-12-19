from __future__ import annotations

import datetime as dt
from typing import Callable, Iterable, Sequence

import attrs
import trio
from attrs import Attribute, define, field, validators


@define
class Availability:
    @define
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
    query: Query
    frequency: dt.timedelta = field(validator=validators.ge(dt.timedelta(minutes=1)))
    filters: Iterable[Callable[[Availability], bool]] = ()
    name: str = field()  # pyright: ignore[reportGeneralTypeIssues]
    availability: Sequence[Availability] | None = None
    scope: trio.CancelScope | None = None

    @name.default  # pyright: ignore[reportGeneralTypeIssues]
    def _default_name(self) -> str:
        return f"{self.query.origin}-{self.query.destination}"


@define
class Query:
    origin: str
    destination: str
    date: dt.date
