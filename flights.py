import datetime as dt
from typing import override

from attrs import field, frozen
from babel.numbers import format_currency


@frozen
class Availability:
    @frozen
    class Fees:
        amount: float
        currency: str

        @override
        def __str__(self) -> str:
            return format_currency(self.amount, self.currency)

    date: dt.date = field(repr=dt.date.isoformat)
    miles: int = field(repr=lambda miles: f"{miles:_}")
    fees: Fees = field(repr=Fees.__str__)
