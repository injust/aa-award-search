from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from attrs import asdict, frozen

if TYPE_CHECKING:
    from attrs import Attribute

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


@frozen
class Availability:
    date: dt.date
    pricing: Pricing

    @staticmethod
    def _serialize(_inst: type, _attr: Attribute[object], value: object) -> object:
        match value:
            case dt.date():
                return value.isoformat()
            case _:
                return value

    def _asdict(self) -> dict[str, object]:
        return asdict(self, value_serializer=self._serialize)


@frozen
class Itinerary:
    @frozen
    class ProductDetail:
        booking_code: str
        cabin_type: str
        product_type: str
        alerts: Iterable[str]

    @frozen
    class Route:
        @frozen
        class Segment:
            @frozen
            class Flight:
                carrier: str
                number: int

            alerts: Iterable[str]
            origin: str
            destination: str
            departure: dt.datetime
            arrival: dt.datetime
            flight: Flight
            aircraft: str
            duration: dt.timedelta
            connection_time: dt.timedelta
            product_details: Iterable[Itinerary.ProductDetail]

        origin: str
        destination: str
        departure: dt.datetime
        arrival: dt.datetime
        stops: int
        connections: Sequence[str | tuple[str, str]]
        segments: Sequence[Segment]

    duration: dt.timedelta
    alerts: Iterable[str]
    pricing: Mapping[str, Pricing]
    route: Route
    product_details: Iterable[Itinerary.ProductDetail]


@frozen
class Pricing:
    @frozen
    class Fees:
        amount: float
        currency: str

    miles: int
    fees: Fees
