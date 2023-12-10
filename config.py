from functools import cache
from pprint import PrettyPrinter


@cache
def pretty_printer() -> PrettyPrinter:
    return PrettyPrinter(width=120, sort_dicts=False, underscore_numbers=True)
