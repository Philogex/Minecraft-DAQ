"""Target-relative path records and effective-width stratification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from analysis.aim_features import AimPoint, shortest_yaw_delta, unwrap_yaws


@dataclass(frozen=True)
class AngularTarget:
    yaw: float
    pitch: float
    width_yaw: float
    width_pitch: float


@dataclass(frozen=True)
class PathDensityRecord:
    event_id: int
    points: tuple[AimPoint, ...]
    target: AngularTarget
    weight: float = 1.0


@dataclass(frozen=True)
class AlignedPath:
    event_id: int
    x: tuple[float, ...]
    y: tuple[float, ...]
    times_ms: tuple[float, ...]
    progress: tuple[float, ...]
    distance: float
    effective_width: float
    width_yaw: float
    width_pitch: float
    fitts_id: float
    weight: float


def effective_target_width(
    movement_yaw: float,
    movement_pitch: float,
    width_yaw: float,
    width_pitch: float,
) -> float:
    """Project an axis-aligned angular target rectangle onto the movement axis."""

    distance = math.hypot(movement_yaw, movement_pitch)
    if distance <= 0.0:
        return math.nan
    if width_yaw <= 0.0 or width_pitch <= 0.0:
        return math.nan
    unit_yaw = movement_yaw / distance
    unit_pitch = movement_pitch / distance
    return math.hypot(width_yaw * unit_yaw, width_pitch * unit_pitch)


def align_path(record: PathDensityRecord) -> AlignedPath | None:
    """Align start-to-target with +x and normalize angular distance to one."""

    if len(record.points) < 2 or not math.isfinite(record.weight) or record.weight <= 0.0:
        return None
    start = record.points[0]
    movement_yaw = shortest_yaw_delta(start.yaw, record.target.yaw)
    movement_pitch = record.target.pitch - start.pitch
    distance = math.hypot(movement_yaw, movement_pitch)
    width = effective_target_width(
        movement_yaw,
        movement_pitch,
        record.target.width_yaw,
        record.target.width_pitch,
    )
    if distance <= 0.0 or not math.isfinite(width):
        return None

    unwrapped_yaws = unwrap_yaws(record.points)
    denominator = distance * distance
    x: list[float] = []
    y: list[float] = []
    for yaw, point in zip(unwrapped_yaws, record.points):
        point_yaw = yaw - unwrapped_yaws[0]
        point_pitch = point.pitch - start.pitch
        x.append(
            (point_yaw * movement_yaw + point_pitch * movement_pitch) / denominator
        )
        y.append(
            (-point_yaw * movement_pitch + point_pitch * movement_yaw) / denominator
        )

    first_time = record.points[0].t_ms
    duration = record.points[-1].t_ms - first_time
    if duration <= 0.0:
        return None
    progress = tuple((point.t_ms - first_time) / duration for point in record.points)
    fitts_id = math.log2(distance / width + 1.0)
    return AlignedPath(
        event_id=record.event_id,
        x=tuple(x),
        y=tuple(y),
        times_ms=tuple(point.t_ms for point in record.points),
        progress=progress,
        distance=distance,
        effective_width=width,
        width_yaw=record.target.width_yaw,
        width_pitch=record.target.width_pitch,
        fitts_id=fitts_id,
        weight=record.weight,
    )


def align_paths(records: Iterable[PathDensityRecord]) -> tuple[AlignedPath, ...]:
    return tuple(path for record in records if (path := align_path(record)) is not None)


def weighted_quantile(
    values: Sequence[float],
    weights: Sequence[float],
    quantile: float,
) -> float:
    if len(values) != len(weights) or not values:
        raise ValueError("values and weights must have the same non-zero length")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    total_weight = sum(weight for _, weight in ordered)
    if total_weight <= 0.0:
        raise ValueError("weights must have a positive sum")
    threshold = quantile * total_weight
    tolerance = max(total_weight * 1e-12, 1e-15)
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative + tolerance >= threshold:
            return value
    return ordered[-1][0]


def quantile_edges(paths: Sequence[AlignedPath], bin_count: int) -> tuple[float, ...]:
    if bin_count <= 0:
        raise ValueError("bin_count must be positive")
    if not paths:
        raise ValueError("cannot derive bins from an empty path collection")
    values = [path.effective_width for path in paths]
    weights = [path.weight for path in paths]
    raw_edges = [
        weighted_quantile(values, weights, index / bin_count)
        for index in range(bin_count + 1)
    ]
    edges = [raw_edges[0]]
    for edge in raw_edges[1:]:
        if edge > edges[-1] and not math.isclose(edge, edges[-1]):
            edges.append(edge)
    if len(edges) < 2:
        value = edges[0]
        epsilon = max(abs(value) * 1e-9, 1e-12)
        return (value - epsilon, value + epsilon)
    return tuple(edges)


def paths_in_bin(
    paths: Sequence[AlignedPath],
    lower: float,
    upper: float,
    *,
    include_upper: bool,
) -> tuple[AlignedPath, ...]:
    if include_upper:
        return tuple(path for path in paths if lower <= path.effective_width <= upper)
    return tuple(path for path in paths if lower <= path.effective_width < upper)
