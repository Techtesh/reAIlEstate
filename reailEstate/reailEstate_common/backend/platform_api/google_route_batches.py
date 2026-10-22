"""Bounded route-batch orchestration independent of CLI, database and providers."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteBatchResult:
    report: dict
    attempted_rows: list[dict]


def has_route_duration(row):
    return any(route.get("status") == "available" and route.get("minutes") is not None
        for group in row.get("routes", {}).get("groups", [])
        for station in group.get("stations", [])
        for route in (station.get("walking", {}), station.get("driving", {})))


def drain_route_batches(run_batch: Callable[[bool], RouteBatchResult], *, max_batches=100):
    """The callback must checkpoint each batch; True requests continuation/resume."""
    if not 1 <= max_batches <= 100:
        raise ValueError("--max-batches must be between 1 and 100.")
    for index in range(max_batches):
        result = run_batch(index > 0)
        if index == 0 and result.attempted_rows and not any(
                has_route_duration(row) for row in result.attempted_rows):
            raise ValueError("No route durations were returned in the first batch. Stopped before further API work.")
        if result.report["state"] == "completed":
            return result.report
    raise ValueError(f"Stopped after {max_batches} batches without reaching completed state. Resume from the checkpoint.")