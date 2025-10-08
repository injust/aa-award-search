from __future__ import annotations

import datetime as dt
import operator
from typing import TYPE_CHECKING, override

from attrs import field, frozen
from attrs.validators import instance_of, not_
from dateutil.relativedelta import relativedelta

if TYPE_CHECKING:
    from collections.abc import Iterator

    from attrs import Attribute


def _date_set_day_one(date: dt.date) -> dt.date:
    return date.replace(day=1)


def _relativedelta_normalize(delta: relativedelta) -> relativedelta:
    return delta.normalized()


@frozen
class DayRange:
    """Produces a sequence of `datetime.date` objects for every calendar day from `start` (inclusive) to `stop` (inclusive) by `step`."""

    start: dt.date = field(validator=not_(instance_of(dt.datetime)))
    stop: dt.date = field(validator=not_(instance_of(dt.datetime)))
    step: dt.timedelta = field(default=dt.timedelta(days=1))

    @step.validator  # pyright: ignore[reportAttributeAccessIssue, reportUntypedFunctionDecorator, reportUnknownMemberType]
    def _check_step(self, attr: Attribute[dt.timedelta], value: dt.timedelta) -> None:
        if not value:
            raise ValueError(f"`{attr.name}` must be non-zero")
        if value % dt.timedelta(days=1):
            raise ValueError(
                f"`{attr.name}` must be a `dt.timedelta` object with only integer `weeks` and `days` values"
            )

    def __bool__(self) -> bool:
        return any(iter(self))

    def __contains__(self, key: object) -> bool:
        return isinstance(key, dt.date) and (self.start <= key <= self.stop or self.start >= key >= self.stop)

    def __iter__(self) -> Iterator[dt.date]:
        increasing = self.step > dt.timedelta()
        comp = operator.le if increasing else operator.ge

        date = self.start
        while comp(date, self.stop):
            yield date
            date += self.step

    @override
    def __str__(self) -> str:
        return f"{self.start:%Y/%m/%d)}-{self.stop:%Y/%m/%d)}"


@frozen
class MonthRange:
    """Produces a sequence of `datetime.date` objects for every calendar month from `start` (inclusive) to `stop` (inclusive) by `step`.

    Each `datetime.date` object is set to the 1st day of the month.
    """

    start: dt.date = field(validator=not_(instance_of(dt.datetime)), converter=_date_set_day_one)
    stop: dt.date = field(validator=not_(instance_of(dt.datetime)), converter=_date_set_day_one)
    step: relativedelta = field(default=relativedelta(months=+1), converter=_relativedelta_normalize)

    @step.validator  # pyright: ignore[reportAttributeAccessIssue, reportUntypedFunctionDecorator, reportUnknownMemberType]
    def _check_step(self, attr: Attribute[relativedelta], value: relativedelta) -> None:
        if not value:
            raise ValueError(f"`{attr.name}` must be non-zero")
        if value != relativedelta(years=value.years, months=value.months):
            raise ValueError(f"`{attr.name}` must be a `relativedelta` object with only `years` and `months` values")

    def __bool__(self) -> bool:
        return any(iter(self))

    def __contains__(self, key: object) -> bool:
        return (
            isinstance(key, dt.date)
            and key.day == 1
            and (self.start <= key <= self.stop or self.start >= key >= self.stop)
        )

    def __iter__(self) -> Iterator[dt.date]:
        increasing = self.step.years * 12 + self.step.months > 0
        comp = operator.le if increasing else operator.ge

        date = self.start
        while comp(date, self.stop):
            assert date.day == 1, date.day
            yield date
            date += self.step

    @override
    def __str__(self) -> str:
        return f"{self.start:%Y/%m)}-{self.stop:%Y/%m)}"
