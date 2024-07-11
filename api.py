import datetime as dt
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

from attrs import field, frozen
from attrs.validators import ge, le
from loguru import logger

from flights import Availability
from utils import httpx_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterable


@frozen
class Query(ABC):
    origin: str
    destination: str
    date: dt.date
    passengers: int = field(default=1, validator=[ge(1), le(9)])

    async def _send_query(self, endpoint: str) -> dict[str, Any]:
        json: dict[str, Any] = {
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
        r = await httpx_client().post(endpoint, json=json)
        if r.is_error:
            (logger.debug if r.is_server_error else logger.error)(
                "HTTP {}: response_json={}, request_json={}", r.status_code, r.json(), json
            )
            # Server errors are retried by `tenacity`
            r.raise_for_status()

        data: dict[str, Any] = r.json()
        if (error := data["error"]) and error != "309":
            raise ValueError(f"Unexpected error code: {error!r}, response_json={data}, request_json={json}")
        return data


@frozen
class AvailabilityQuery(Query, ABC):
    @abstractmethod
    def search(self) -> AsyncIterable[Availability]:
        raise NotImplementedError


@frozen
class CalendarQuery(AvailabilityQuery):
    @override
    async def search(self) -> AsyncIterable[Availability]:
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
                            solution["perPassengerAwardPoints"],
                            Availability.Fees(**solution["perPassengerSaleTotal"]),
                        )


@frozen
class WeeklyQuery(AvailabilityQuery):
    @override
    async def search(self) -> AsyncIterable[Availability]:
        data = await self._send_query("search/weekly")

        if data["error"] == 309 and any(day["solutionId"] for day in data["days"]):
            logger.warning("Error 309 response contains solutions, data={}", data)

        for day in data["days"]:
            if day["solutionId"]:
                yield Availability(
                    dt.date.fromisoformat(day["date"]),
                    int(day["perPassengerAwardPointsTotal"]),
                    Availability.Fees(**day["perPassengerDisplayTotal"]),
                )
