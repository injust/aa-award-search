import time
from pprint import PrettyPrinter


def beep(times: int = 1, *, interval: float = 0.15) -> None:
    for _ in range(times):
        print(end="\a", flush=True)
        time.sleep(interval)


pretty_printer = PrettyPrinter(width=120, sort_dicts=False, underscore_numbers=True)
