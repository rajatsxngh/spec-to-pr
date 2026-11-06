"""Tiny stats helpers with one planted bug, used for the milestone 1 end-to-end check."""


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("mean() of empty list")
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median() of empty list")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid] + ordered[mid + 1]) / 2
