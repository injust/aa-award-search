from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from time import sleep

from config import pretty_printer
from flights import Availability


def beep(n: int = 1) -> None:
    INTERVAL = 0.1

    for _ in range(n):
        print("\a", end="", flush=True)
        sleep(INTERVAL)


def compute_diff(a: Sequence[Availability], b: Sequence[Availability]) -> Iterable[str]:
    for line in difflib.unified_diff(
        tuple(map(pretty_printer().pformat, map(Availability.asdict, a))),
        tuple(map(pretty_printer().pformat, map(Availability.asdict, b))),
        n=max(len(a), len(b)),
        lineterm="",
    ):
        if not (line.startswith("@@") or line in {"+++ ", "--- "}):
            yield line
