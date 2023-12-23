from __future__ import annotations

import datetime as dt
from typing import AsyncIterable

import httpx

from classes import Availability, Query


async def send_query(query: Query, httpx_client: httpx.AsyncClient) -> AsyncIterable[Availability]:
    r = await httpx_client.post(
        "/search/calendar",
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
                    "maxStops": query.max_stops,
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

    data = r.json()
    if (error := data["error"]) and error != "309":
        raise ValueError(f"Unexpected error code: {error!r}, response_json={data}, query={query}")

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
