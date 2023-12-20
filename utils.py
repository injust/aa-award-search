from __future__ import annotations

from time import sleep
from typing import Iterable, Literal, TypeAlias

from config import pretty_printer
from flights import Availability

DiffLine: TypeAlias = tuple[Literal[" ", "+", "-"], Availability]


def beep(n: int = 1) -> None:
    INTERVAL = 0.1

    for _ in range(n):
        print("\a", end="", flush=True)
        sleep(INTERVAL)


def compute_diff(a: Iterable[Availability], b: Iterable[Availability]) -> Iterable[DiffLine]:
    old = {avail.date: avail for avail in a}
    new = {avail.date: avail for avail in b}

    for date in sorted(old.keys() | new.keys()):
        if old.get(date, None) == new.get(date, None):
            yield (" ", new[date])
        else:
            if date in old:
                yield ("-", old[date])
            if date in new:
                yield ("+", new[date])


def format_diff(diff: Iterable[DiffLine]) -> str:
    LOGURU_COLOR_TAGS = {" ": "dim", "+": "green", "-": "red"}

    return "\n".join(
        f"<{LOGURU_COLOR_TAGS[change]}>{change}{pretty_printer().pformat(avail.asdict())}</>" for change, avail in diff
    )
