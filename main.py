from __future__ import annotations

import datetime as dt
import random
import sys
from contextlib import aclosing
from typing import Callable, cast

import httpx
import trio
from loguru import logger
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from trio_typing import TaskStatus

from api import search_calendar
from classes import Availability, Diff, Job, MultiQuery, Task
from config import httpx_client, pretty_printer
from utils import beep


async def run_job(job: Job, *, task_status: TaskStatus[trio.CancelScope] = trio.TASK_STATUS_IGNORED) -> None:
    with trio.CancelScope() as scope:  # pyright: ignore[reportGeneralTypeIssues]
        task_status.started(scope)

        async with trio.open_nursery() as n:
            for task in job.to_tasks():
                n.start_soon(run_task, task)


async def run_task(task: Task) -> None:
    @retry(
        sleep=trio.sleep,
        stop=stop_after_attempt(3),
        wait=wait_exponential(),
        retry=retry_if_exception_type(httpx.HTTPStatusError)
        & retry_if_exception(lambda e: cast(httpx.HTTPStatusError, e).response.status_code >= 500),
        before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
    )
    @retry(
        sleep=trio.sleep,
        stop=stop_after_attempt(10),
        wait=wait_exponential(max=32),
        retry=retry_if_exception_type(httpx.TransportError),
        before_sleep=before_sleep_log(logger, "DEBUG"),  # type: ignore[arg-type]
    )
    async def run_task_once() -> list[Availability]:
        try:
            return [
                avail async for avail in search_calendar(task.query) if all(filter(avail) for filter in task.filters)
            ]
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                logger.debug(f"{e!r}, response_json={e.response.json()}, query={task.query}")
            raise e

    if not task.frequency:
        availability = await run_task_once()

        logger.info(f"{task.name}\n{pretty_printer().pformat(list(map(Availability.asdict, availability)))}\n")
        if availability:
            beep()

        return

    await trio.sleep(random.uniform(0, task.frequency.total_seconds() / 2))

    while True:
        try:
            prev_availability = task.availability
            task.availability = await run_task_once()

            if prev_availability is None:
                logger.info(
                    f"{task.name}\n{pretty_printer().pformat(list(map(Availability.asdict, task.availability)))}\n"
                )
                if task.availability:
                    beep()
            else:
                diff = Diff.from_availability(prev_availability, task.availability)

                if any(change > " " for change, _ in diff.lines):
                    logger.opt(colors=True).info(f"{task.name}\n{diff.colorized()}\n")
                    if any(change == "+" for change, _ in diff.lines):
                        beep(3)

            await trio.sleep(task.frequency.total_seconds())
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError):
                assert e.response.status_code < 500
                logger.error(f"{e!r}, response_json={e.response.json()}, query={task.query}")
            elif isinstance(e, httpx.HTTPError):
                logger.exception(f"{e!r}")
            else:
                logger.exception(f"{e!r}, task={task}")

            beep()
            break


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
            MultiQuery(["HKG"], ["DFW", "NYC", "ORD"], [dt.date(2024, 1, 25)], 3),
            ONE_MINUTE,
            [date_ge(dt.date(2024, 1, 17)), miles_eq(70000)],
        ),  # pyright: ignore[reportGeneralTypeIssues]
        Job(
            MultiQuery(["HKG"], ["NYC"], [dt.date(2024, 1, 25), dt.date(2024, 2, 5)]),
            ONE_MINUTE,
            [date_ge(dt.date(2024, 1, 23)), miles_eq(70000)],
        ),  # pyright: ignore[reportGeneralTypeIssues]
    ]

    async with aclosing(httpx_client()), trio.open_nursery() as n:
        for job in jobs:
            job.scope = await n.start(run_job, job)


if __name__ == "__main__":
    logger.remove(0)
    logger.add(sys.stderr, diagnose=True)

    trio.run(main)
