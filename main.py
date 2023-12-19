from __future__ import annotations

import datetime as dt
import random
from typing import Callable

import httpx
import trio
from loguru import logger
from trio_typing import TaskStatus

from api import send_query
from classes import Availability, Job, Query
from config import pretty_printer
from utils import beep, compute_diff


async def run_job(
    job: Job, httpx_client: httpx.AsyncClient, *, task_status: TaskStatus[trio.CancelScope] = trio.TASK_STATUS_IGNORED
) -> None:
    with trio.CancelScope() as scope:  # pyright: ignore[reportGeneralTypeIssues]
        task_status.started(scope)

        await trio.sleep(random.uniform(0, job.frequency.total_seconds() / 2))

        while True:
            try:
                availability = [
                    avail
                    async for avail in send_query(job.query, httpx_client)
                    if all(filter(avail) for filter in job.filters)
                ]
            except httpx.TransportError as e:
                logger.warning(f"{e!r}")
                beep()
            except httpx.HTTPStatusError as e:
                log = logger.error if e.response.status_code < 500 else logger.warning
                log(f"{e!r}, query={job.query}")
                beep()
                if e.response.status_code < 500:
                    break
            except httpx.HTTPError as e:
                logger.exception(f"{e!r}")
                beep()
                break
            except Exception as e:
                logger.exception(f"{e!r}, query={job.query}")
                beep()
                break
            else:
                if (prev_availability := job.availability) is None:
                    print(job.name)
                    pretty_printer().pprint(list(map(Availability.asdict, availability)))
                    print()

                    if availability:
                        beep()
                elif diff := list(compute_diff(prev_availability, availability)):
                    print(job.name)
                    print(*diff, sep="\n")
                    print()

                    if any(line.startswith("+") for line in diff):
                        beep(3)

                job.availability = availability
            finally:
                await trio.sleep(job.frequency.total_seconds())


@logger.catch
async def main() -> None:
    ONE_MINUTE = dt.timedelta(minutes=1)

    def date_eq(date: dt.date) -> Callable[[Availability], bool]:
        return lambda avail: avail.date == date

    def date_ge(date: dt.date) -> Callable[[Availability], bool]:
        return lambda avail: avail.date >= date

    def date_le(date: dt.date) -> Callable[[Availability], bool]:
        return lambda avail: avail.date <= date

    def miles_eq(miles: int) -> Callable[[Availability], bool]:
        return lambda avail: avail.miles == miles

    jobs = [
        Job(
            Query("HKG", "ORD", dt.date(2024, 1, 25)), ONE_MINUTE, [date_ge(dt.date(2024, 1, 17)), miles_eq(70000)]
        ),  # pyright: ignore[reportGeneralTypeIssues]
        Job(
            Query("HKG", "DFW", dt.date(2024, 1, 25)), ONE_MINUTE, [date_ge(dt.date(2024, 1, 17)), miles_eq(70000)]
        ),  # pyright: ignore[reportGeneralTypeIssues]
        Job(
            Query("HKG", "NYC", dt.date(2024, 1, 25)), ONE_MINUTE, [date_ge(dt.date(2024, 1, 17)), miles_eq(70000)]
        ),  # pyright: ignore[reportGeneralTypeIssues]
        Job(
            Query("HKG", "NYC", dt.date(2024, 2, 5)), ONE_MINUTE, [miles_eq(70000)]
        ),  # pyright: ignore[reportGeneralTypeIssues]
    ]

    async with httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(5, read=10),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=60),
        base_url="https://www.aa.com/booking/api",
    ) as httpx_client, trio.open_nursery() as n:
        for job in jobs:
            job.scope = await n.start(run_job, job, httpx_client)


if __name__ == "__main__":
    trio.run(main)
