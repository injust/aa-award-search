import datetime as dt
from collections.abc import AsyncIterable

import httpx
from attrs import frozen
from loguru import logger
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from flights import Availability


@frozen
class Query:
    origin: str
    destination: str
    date: dt.date

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(),
        retry=retry_if_exception(lambda e: isinstance(e, httpx.HTTPStatusError) and e.response.is_server_error),
        before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
        reraise=True,
    )
    @retry(
        stop=stop_after_attempt(10),
        wait=wait_exponential(max=32),
        retry=retry_if_exception_type(httpx.TransportError),
        before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
        reraise=True,
    )
    async def search(self, httpx_client: httpx.AsyncClient) -> AsyncIterable[Availability]:
        r = await httpx_client.post(
            "/search/calendar",
            json={
                "metadata": {"selectedProducts": [], "tripType": "OneWay", "udo": {}},
                "passengers": [{"type": "adult", "count": 1}],
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

        data = r.json()
        if (error := data["error"]) and error != "309":
            raise ValueError(
                f"Unexpected error code: {error!r}, response_json={data}, request_content={r.request.content.decode()}"
            )

        if error == 309 and any(
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
