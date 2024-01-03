from __future__ import annotations

import datetime as dt
from functools import singledispatch
from typing import Any, AsyncIterable

from classes import Availability, CalendarQuery, Query, WeeklyQuery
from config import httpx_client


async def _send_search_query(endpoint: str, query: Query) -> dict[str, Any]:
    r = await httpx_client().post(
        endpoint,
        json={
            "metadata": {"selectedProducts": [], "tripType": "OneWay", "udo": {}},
            "passengers": [{"type": "adult", "count": query.passengers}],
            "requestHeader": {"clientId": "AAcom"},
            "slices": [
                {
                    "allCarriers": True,
                    "cabin": "BUSINESS,FIRST",
                    "departureDate": query.date.isoformat(),
                    "destination": query.destination,
                    "destinationNearbyAirports": False,
                    "maxStops": None,
                    "origin": query.origin,
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
        raise ValueError(f"Unexpected error code: {error!r}, response_json={data}, query={query}")

    return data


@singledispatch
def search_availability(query: Query) -> AsyncIterable[Availability]:
    return NotImplemented


@search_availability.register
async def search_calendar(query: CalendarQuery) -> AsyncIterable[Availability]:
    data = await _send_search_query("/search/calendar", query)

    for month in data["calendarMonths"]:
        for week in month["weeks"]:
            for day in week["days"]:
                if solution := day["solution"]:
                    if (currency := solution["perPassengerSaleTotal"]["currency"]) != "USD":
                        raise ValueError(f"Unexpected currency: {currency!r}, response_json={data}, query={query}")

                    yield Availability(
                        dt.date.fromisoformat(day["date"]),
                        solution["perPassengerAwardPoints"],
                        Availability.Fees(**solution["perPassengerSaleTotal"]),
                    )


@search_availability.register
async def search_weekly(query: WeeklyQuery) -> AsyncIterable[Availability]:
    data = await _send_search_query("/search/weekly", query)

    for day in data["days"]:
        if day["solutionId"]:
            if (currency := day["perPassengerDisplayTotal"]["currency"]) != "USD":
                raise ValueError(f"Unexpected currency: {currency!r}, response_json={data}, query={query}")

            yield Availability(
                dt.date.fromisoformat(day["date"]),
                int(day["perPassengerAwardPointsTotal"]),
                Availability.Fees(**day["perPassengerDisplayTotal"]),
            )
