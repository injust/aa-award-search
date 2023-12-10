from __future__ import annotations

import datetime as dt

import attrs
from attrs import Attribute, frozen


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
