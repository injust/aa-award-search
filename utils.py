from __future__ import annotations

from time import sleep


def beep(n: int = 1) -> None:
    INTERVAL = 0.1

    for _ in range(n):
        print("\a", end="", flush=True)
        sleep(INTERVAL)
