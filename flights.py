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
    def _serialize(inst: type, attr: Attribute[object], value: object) -> object:
        if isinstance(value, dt.date):
            return value.isoformat()
        return value

    def asdict(self) -> dict[str, object]:
        return asdict(self, value_serializer=self._serialize)
