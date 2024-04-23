import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable
from typing import Any

import httpx
from attrs import field, frozen, validators
from loguru import logger

from flights import Availability
from utils import httpx_client


@frozen
class Query(ABC):
    origin: str
    destination: str
    date: dt.date
    passengers: int = field(  # pyright: ignore[reportCallIssue]
        default=1,
        validator=[validators.ge(1), validators.le(9)],  # pyright: ignore[reportArgumentType]
    )

    async def _send_query(self, endpoint: str, httpx_client: httpx.AsyncClient = httpx_client()) -> dict[str, Any]:
        r = await httpx_client.post(
            endpoint,
            json={
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
                "tripOptions": {"searchType": "Award", "corporateBooking": False, "locale": "en_US"},
                "loyaltyInfo": None,
                "version": "",
                "queryParams": {"sliceIndex": 0, "sessionId": "", "solutionSet": "", "solutionId": ""},
            },
        )
        r.raise_for_status()

        data: dict[str, Any] = r.json()
        if (error := data["error"]) and error != "309":
            raise ValueError(
                f"Unexpected error code: {error!r}, response_json={data}, request_content={r.request.content.decode()}"
            )
        return data


@frozen
class AvailabilityQuery(Query):
    @abstractmethod
    def search(self) -> AsyncIterable[Availability]:
        raise NotImplementedError


@frozen
class CalendarQuery(AvailabilityQuery):
    async def search(self) -> AsyncIterable[Availability]:
        data = await self._send_query("search/calendar")

        if data["error"] == 309 and any(
            day["solution"] for month in data["calendarMonths"] for week in month["weeks"] for day in week["days"]
        ):
            logger.warning(f"Error 309 response contains solutions, data={data}")

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
    async def search(self) -> AsyncIterable[Availability]:
        data = await self._send_query("search/weekly")

        if data["error"] == 309 and any(day["solutionId"] for day in data["days"]):
            logger.warning(f"Error 309 response contains solutions, data={data}")

        for day in data["days"]:
            if day["solutionId"]:
                yield Availability(
                    dt.date.fromisoformat(day["date"]),
                    int(day["perPassengerAwardPointsTotal"]),
                    Availability.Fees(**day["perPassengerDisplayTotal"]),
                )
