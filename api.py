from __future__ import annotations

import datetime as dt
from functools import singledispatch
from typing import Any, AsyncIterable

from loguru import logger

from classes import Availability, CalendarQuery, Itinerary, ItineraryQuery, Pricing, Query, WeeklyQuery
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
                        Pricing(solution["perPassengerAwardPoints"], Pricing.Fees(**solution["perPassengerSaleTotal"])),
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
                Pricing(int(day["perPassengerAwardPointsTotal"]), Pricing.Fees(**day["perPassengerDisplayTotal"])),
            )


async def search_itinerary(query: ItineraryQuery) -> AsyncIterable[Itinerary]:
    data = await _send_search_query("/search/itinerary", query)

    if notifications := data["responseMetadata"]["notifications"]:
        logger.debug(f"notifications={notifications}, query={query}")

    for slice in data["slices"]:
        pricing = {
            pricing_detail["productType"]: Pricing(
                pricing_detail["perPassengerAwardPoints"], Pricing.Fees(**pricing_detail["perPassengerTaxesAndFees"])
            )
            for pricing_detail in slice["pricingDetail"]
            if pricing_detail["productAvailable"]
        }
        yield Itinerary(dt.timedelta(minutes=slice["durationInMinutes"]), slice["alerts"], pricing)

        slice = {
            "origin": {"code": "LHR"},
            "arrivesNextDay": 2,
            "destination": {"code": "HKG"},
            "stops": 1,
            "departureDateTime": "2024-06-26T14:15:00.000+01:00",
            "productDetails": [
                {
                    "bookingCode": "P",
                    "cabinType": "PREMIUM_ECONOMY",
                    "productType": "PREMIUM_ECONOMY",
                    "alerts": [],
                }
            ],
            "arrivalDateTime": "2024-06-28T10:10:00.000+08:00",
            "connectingCities": [[{"code": "BLR"}]],
            "segments": [
                {
                    "alerts": [],
                    "flight": {"carrierCode": "BA", "flightNumber": "119"},
                    "legs": [
                        {
                            "aircraft": {"code": "351", "name": "Airbus A350-1000", "shortName": "Airbus A350"},
                            "arrivalDateTime": "2024-06-27T04:45:00.000+05:30",
                            "connectionTimeInMinutes": 1255,
                            "departureDateTime": "2024-06-26T14:15:00.000+01:00",
                            "destination": {"code": "BLR"},
                            "durationInMinutes": 600,
                            "origin": {"code": "LHR"},
                            "productDetails": [
                                {
                                    "bookingCode": "P",
                                    "cabinType": "PREMIUM_ECONOMY",
                                    "productType": "PREMIUM_ECONOMY",
                                    "alerts": [],
                                }
                            ],
                            "alerts": [],
                            "arrivesNextDay": 1,
                            "aircraftCode": "351",
                        }
                    ],
                    "origin": {"code": "LHR"},
                    "destination": {"code": "BLR"},
                    "departureDateTime": "2024-06-26T14:15:00.000+01:00",
                    "arrivalDateTime": "2024-06-27T04:45:00.000+05:30",
                },
                {
                    "alerts": [],
                    "flight": {"carrierCode": "CX", "flightNumber": "624"},
                    "legs": [
                        {
                            "aircraft": {"code": "333", "name": "Airbus A330-300", "shortName": "Airbus A330"},
                            "arrivalDateTime": "2024-06-28T10:10:00.000+08:00",
                            "connectionTimeInMinutes": 0,
                            "departureDateTime": "2024-06-28T01:40:00.000+05:30",
                            "destination": {"code": "HKG"},
                            "durationInMinutes": 360,
                            "origin": {"code": "BLR"},
                            "productDetails": [
                                {
                                    "bookingCode": "X",
                                    "cabinType": "COACH",
                                    "productType": "PREMIUM_ECONOMY",
                                    "alerts": ["ALERTS.CLASS-OF-SERVICE-NOT-AVAILABLE"],
                                }
                            ],
                            "alerts": ["ALERTS.OVERNIGHT"],
                            "arrivesNextDay": 0,
                            "aircraftCode": "333",
                        }
                    ],
                    "origin": {"code": "BLR"},
                    "destination": {"code": "HKG"},
                    "departureDateTime": "2024-06-28T01:40:00.000+05:30",
                    "arrivalDateTime": "2024-06-28T10:10:00.000+08:00",
                },
            ],
        }
