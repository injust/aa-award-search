from __future__ import annotations

import datetime as dt
from typing import Iterable

from attrs import field, frozen, validators


@frozen
class BatchQuery:
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
class ItineraryQuery(Query):
    pass


@frozen
class WeeklyQuery(Query):
    pass
