from __future__ import annotations

import datetime as dt
from functools import singledispatch
from typing import Any, AsyncIterable

from loguru import logger

from classes.flights import Availability, Itinerary, Pricing
from classes.queries import CalendarQuery, ItineraryQuery, Query, WeeklyQuery
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

        connections: list[str | tuple[str, str]] = []
        for connection in slice["connectingCities"]:
            if len(connection) == 1:
                connections.append(connection[0]["code"])
            elif len(connection) == 2:
                connections.append((connection[0]["code"], connection[1]["code"]))
            else:
                raise ValueError(f"Unexpected value: connectingCities={slice['connectingCities']}")

        segments: list[Itinerary.Route.Segment] = []
        for segment in slice["segments"]:
            if len(segment["legs"]) != 1:
                raise ValueError()
            leg = segment["legs"][0]
            if (
                segment["origin"]["code"] != leg["origin"]["code"]
                or segment["destination"]["code"] != leg["destination"]["code"]
                or segment["departureDateTime"] != leg["departureDateTime"]
                or segment["arrivalDateTime"] != leg["arrivalDateTime"]
            ):
                raise ValueError(f"Mismatching segment and leg: segment={segment}")
            elif leg["aircraft"]["code"] != leg["aircraftCode"]:
                raise ValueError(
                    f"Mismatching aircraft codes: aircraft={leg['aircraft']}, aircraftCode={leg['aircraftCode']!r}"
                )
            segments.append(
                Itinerary.Route.Segment(
                    segment["alerts"] + leg["alerts"],
                    segment["origin"]["code"],
                    segment["destination"]["code"],
                    dt.datetime.fromisoformat(segment["departureDateTime"]),
                    dt.datetime.fromisoformat(segment["arrivalDateTime"]),
                    Itinerary.Route.Segment.Flight(segment["flight"]["carrierCode"], segment["flight"]["flightNumber"]),
                    leg["aircraftCode"],
                    dt.timedelta(minutes=leg["durationInMinutes"]),
                    dt.timedelta(minutes=leg["connectionTimeInMinutes"]),
                )
            )

        route = Itinerary.Route(
            slice["origin"]["code"],
            slice["destination"]["code"],
            dt.datetime.fromisoformat(slice["departureDateTime"]),
            dt.datetime.fromisoformat(slice["arrivalDateTime"]),
            slice["stops"],
            connections,
            segments,
        )
        yield Itinerary(dt.timedelta(minutes=slice["durationInMinutes"]), slice["alerts"], pricing, route)
