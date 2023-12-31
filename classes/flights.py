from __future__ import annotations

import datetime as dt
from typing import Mapping, Sequence

import attrs
from attrs import Attribute, frozen


@frozen
class Availability:
    date: dt.date
    pricing: Pricing

    def asdict(self) -> dict[str, object]:
        return attrs.asdict(self, value_serializer=self._serialize)

    @staticmethod
    def _serialize(inst: type, attr: Attribute[object], value: object) -> object:
        if isinstance(value, dt.date):
            return value.isoformat()
        return value


@frozen
class Itinerary:
    @frozen
    class Route:
        @frozen
        class Segment:
            @frozen
            class Flight:
                carrier: str
                number: int

            alerts: Sequence[str]
            origin: str
            destination: str
            departure: dt.datetime
            arrival: dt.datetime
            flight: Flight
            aircraft: str
            duration: dt.timedelta
            connection_time: dt.timedelta

        origin: str
        destination: str
        departure: dt.datetime
        arrival: dt.datetime
        stops: int
        connections: Sequence[str | tuple[str, str]]
        segments: Sequence[Segment]

    duration: dt.timedelta
    alerts: Sequence[str]
    pricing: Mapping[str, Pricing]
    route: Route


@frozen
class Pricing:
    @frozen
    class Fees:
        amount: float
        currency: str

    miles: int
    fees: Fees
