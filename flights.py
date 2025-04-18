from __future__ import annotations

import datetime as dt

from attrs import Attribute, asdict, frozen


@frozen
class Availability:
    @frozen
    class Fees:
        amount: float
        currency: str

    date: dt.date
    miles: int
    fees: Fees

    @staticmethod
    def _serialize(_inst: type, _attr: Attribute[object], value: object) -> object:
        match value:
            case dt.date():
                return value.isoformat()
            case _:
                return value

    def _asdict(self) -> dict[str, object]:
        return asdict(self, value_serializer=self._serialize)
