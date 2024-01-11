import datetime as dt
from typing import TYPE_CHECKING, override

from attrs import field, frozen
from babel.numbers import format_currency

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


@frozen
class Availability:
    date: dt.date = field(repr=dt.date.isoformat)
    pricing: Pricing


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

        @override
        def __str__(self) -> str:
            return format_currency(self.amount, self.currency)

    miles: int = field(repr=lambda miles: f"{miles:_}")
    fees: Fees = field(repr=Fees.__str__)
