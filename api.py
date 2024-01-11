import datetime as dt
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

import orjson as jsonlib
from attrs import field, frozen
from attrs.validators import ge, le
from loguru import logger

from flights import Availability, Itinerary, Pricing
from httpx_utils import httpx_client

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable

    type JSON = dict[str, Any]


@frozen
class Query(ABC):
    origin: str
    destination: str
    date: dt.date
    passengers: int = field(default=1, validator=[ge(1), le(9)])

    async def _send_query(self, endpoint: str) -> JSON:
        json: JSON = {
            "metadata": {"selectedProducts": [], "tripType": "OneWay", "udo": {}},
            "passengers": [{"type": "adult", "count": self.passengers}],
            "requestHeader": {"clientId": "AAcom"},
            "slices": [
                {
                    "allCarriers": True,
                    "cabin": "BUSINESS,FIRST",
                    "departureDate": self.date.isoformat(),
                    "destination": self.destination,
                    "destinationNearbyAirports": False,
                    "maxStops": None,
                    "origin": self.origin,
                    "originNearbyAirports": False,
                }
            ],
            "tripOptions": {
                "corporateBooking": False,
                "fareType": "Lowest",
                "locale": "en_US",
                "pointOfSale": None,
                "searchType": "Award",
            },
            "loyaltyInfo": None,
            "version": "cfr",
            "queryParams": {"sliceIndex": 0, "sessionId": "", "solutionSet": "", "solutionId": ""},
        }
        r = await httpx_client.post(endpoint, content=jsonlib.dumps(json))

        if r.is_error:
            (logger.debug if r.is_server_error else logger.error)(
                "{!r}: response_json={}, request_json={}", r.status_code, r.json(), json
            )
            # Server errors are retried by `tenacity`
            r.raise_for_status()

        data: JSON = r.json()
        if (error := data["error"]) and error != "309":
            msg = f"Unexpected error code: {error!r}, response_json={data}, request_json={json}"
            raise ValueError(msg)
        return data


@frozen
class AvailabilityQuery(Query, ABC):
    @abstractmethod
    def search(self) -> AsyncIterable[Availability]:
        raise NotImplementedError


@frozen
class CalendarQuery(AvailabilityQuery):
    @override
    async def search(self) -> AsyncGenerator[Availability]:
        data = await self._send_query("search/calendar")

        if data["error"] == 309 and any(
            day["solution"] for month in data["calendarMonths"] for week in month["weeks"] for day in week["days"]
        ):
            logger.warning("Error 309 response contains solutions, data={}", data)

        for month in data["calendarMonths"]:
            for week in month["weeks"]:
                for day in week["days"]:
                    if solution := day["solution"]:
                        yield Availability(
                            dt.date.fromisoformat(day["date"]),
                            Pricing(
                                solution["perPassengerAwardPoints"], Pricing.Fees(**solution["perPassengerSaleTotal"])
                            ),
                        )


@frozen
class WeeklyQuery(AvailabilityQuery):
    @override
    async def search(self) -> AsyncGenerator[Availability]:
        data = await self._send_query("search/weekly")

        if data["error"] == 309 and any(day["solutionId"] for day in data["days"]):
            logger.warning("Error 309 response contains solutions, data={}", data)

        for day in data["days"]:
            if day["solutionId"]:
                yield Availability(
                    dt.date.fromisoformat(day["date"]),
                    Pricing(int(day["perPassengerAwardPointsTotal"]), Pricing.Fees(**day["perPassengerDisplayTotal"])),
                )


@frozen
class ItineraryQuery(Query):
    async def search(self) -> AsyncIterable[Itinerary]:
        data = await self._send_query("/search/itinerary")

        if notifications := data["responseMetadata"]["notifications"]:
            logger.debug("notifications={}, query={}", notifications, self)

        for slice_ in data["slices"]:
            pricing = {
                pricing_detail["productType"]: Pricing(
                    pricing_detail["perPassengerAwardPoints"],
                    Pricing.Fees(**pricing_detail["perPassengerTaxesAndFees"]),
                )
                for pricing_detail in slice_["pricingDetail"]
                if pricing_detail["productAvailable"]
            }

            connections: list[str | tuple[str, str]] = []
            for connection in slice_["connectingCities"]:
                if len(connection) == 1:
                    connections.append(connection[0]["code"])
                elif len(connection) == 2:
                    connections.append((connection[0]["code"], connection[1]["code"]))
                else:
                    msg = f"Unexpected connectingCities value: {connection}, connectingCities={slice_['connectingCities']}"
                    raise ValueError(msg)

            segments: list[Itinerary.Route.Segment] = []
            for segment in slice_["segments"]:
                if len(segment["legs"]) > 1:
                    logger.warning("Segment contains multiple legs, segment={}", segment)
                leg = segment["legs"][0]
                if (
                    segment["origin"]["code"] != leg["origin"]["code"]
                    or segment["destination"]["code"] != leg["destination"]["code"]
                    or segment["departureDateTime"] != leg["departureDateTime"]
                    or segment["arrivalDateTime"] != leg["arrivalDateTime"]
                ):
                    logger.warning("Mismatching segment and leg, segment={}", segment)
                elif leg["aircraft"]["code"] != leg["aircraftCode"]:
                    logger.warning(
                        "Mismatching aircraft code: {!r} != {!r}, aircraft={}, aircraftCode={!r}",
                        leg["aircraft"]["code"],
                        leg["aircraftCode"],
                        leg["aircraft"],
                        leg["aircraftCode"],
                    )
                product_details = [
                    Itinerary.ProductDetail(
                        product_detail["bookingCode"],
                        product_detail["cabinType"],
                        product_detail["productType"],
                        product_detail["alerts"],
                    )
                    for product_detail in leg["productDetails"]
                ]
                segments.append(
                    Itinerary.Route.Segment(
                        segment["alerts"] + leg["alerts"],
                        segment["origin"]["code"],
                        segment["destination"]["code"],
                        dt.datetime.fromisoformat(segment["departureDateTime"]),
                        dt.datetime.fromisoformat(segment["arrivalDateTime"]),
                        Itinerary.Route.Segment.Flight(
                            segment["flight"]["carrierCode"], segment["flight"]["flightNumber"]
                        ),
                        leg["aircraftCode"],
                        dt.timedelta(minutes=leg["durationInMinutes"]),
                        dt.timedelta(minutes=leg["connectionTimeInMinutes"]),
                        product_details,
                    )
                )

            route = Itinerary.Route(
                slice_["origin"]["code"],
                slice_["destination"]["code"],
                dt.datetime.fromisoformat(slice_["departureDateTime"]),
                dt.datetime.fromisoformat(slice_["arrivalDateTime"]),
                slice_["stops"],
                connections,
                segments,
            )
            product_details = [
                Itinerary.ProductDetail(
                    product_detail["bookingCode"],
                    product_detail["cabinType"],
                    product_detail["productType"],
                    product_detail["alerts"],
                )
                for product_detail in slice_["productDetails"]
            ]
            yield Itinerary(
                dt.timedelta(minutes=slice_["durationInMinutes"]), slice_["alerts"], pricing, route, product_details
            )
