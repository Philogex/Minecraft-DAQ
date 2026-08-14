"""Target-relative path records and effective-width stratification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, MutableMapping, Sequence

from analysis.aim_features import AimPoint, shortest_yaw_delta, unwrap_yaws
from analysis.mining_context import MINECRAFT_TICK_MS


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
    effective_width: float | None = None
    weight: float = 1.0
    start_inside_target_region: bool = False
    expected_break_ticks: int | None = None
    expected_break_ms: float | None = None
    actual_break_ms: float | None = None
    break_delay_ratio: float | None = None
    boundary_clearance_ratio: float = math.nan


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
    expected_break_ticks: int | None
    expected_break_ms: float | None
    actual_break_ms: float | None
    break_delay_ratio: float | None
    boundary_clearance_ratio: float


@dataclass(frozen=True)
class PathStratum:
    key: str
    label: str
    lower: float
    upper: float | None
    include_upper: bool = False


START_INSIDE_TARGET_REGION = "start_inside_target_region"


def direction_from_orientation(yaw: float, pitch: float) -> tuple[float, float, float]:
    """Convert Minecraft yaw and pitch to a unit look direction."""

    yaw_radians = math.radians(yaw)
    pitch_radians = math.radians(pitch)
    pitch_cosine = math.cos(pitch_radians)
    return (
        -math.sin(yaw_radians) * pitch_cosine,
        -math.sin(pitch_radians),
        math.cos(yaw_radians) * pitch_cosine,
    )


def point_in_visible_direction_components(
    direction: tuple[float, float, float],
    components: Sequence[Sequence[tuple[float, float, float]]],
) -> bool:
    """Test a direction against convex spherical components."""

    def dot(first, second) -> float:
        return sum(lhs * rhs for lhs, rhs in zip(first, second))

    def cross(first, second) -> tuple[float, float, float]:
        return (
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        )

    for vertices in components:
        if len(vertices) < 3:
            continue
        interior = tuple(
            sum(vertex[axis] for vertex in vertices) for axis in range(3)
        )
        edges = tuple(
            cross(vertex, vertices[(index + 1) % len(vertices)])
            for index, vertex in enumerate(vertices)
        )
        orientation_sum = sum(dot(edge, interior) for edge in edges)
        if orientation_sum == 0.0:
            continue
        orientation = 1.0 if orientation_sum > 0.0 else -1.0
        if all(
            orientation * dot(edge, direction)
            >= -1.0e-12 * max(1.0, math.sqrt(dot(edge, edge)))
            for edge in edges
        ):
            return True
    return False


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

    if (
        record.start_inside_target_region
        or len(record.points) < 2
        or not math.isfinite(record.weight)
        or record.weight <= 0.0
    ):
        return None
    start = record.points[0]
    movement_yaw = shortest_yaw_delta(start.yaw, record.target.yaw)
    movement_pitch = record.target.pitch - start.pitch
    distance = math.hypot(movement_yaw, movement_pitch)
    width = record.effective_width
    if width is None or not math.isfinite(width) or width <= 0.0:
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
        expected_break_ticks=record.expected_break_ticks,
        expected_break_ms=record.expected_break_ms,
        actual_break_ms=record.actual_break_ms,
        break_delay_ratio=record.break_delay_ratio,
        boundary_clearance_ratio=record.boundary_clearance_ratio,
    )


def align_paths(
    records: Iterable[PathDensityRecord],
    *,
    skipped_reasons: MutableMapping[str, int] | None = None,
) -> tuple[AlignedPath, ...]:
    paths: list[AlignedPath] = []
    for record in records:
        if record.start_inside_target_region:
            if skipped_reasons is not None:
                skipped_reasons[START_INSIDE_TARGET_REGION] = (
                    skipped_reasons.get(START_INSIDE_TARGET_REGION, 0) + 1
                )
            continue
        path = align_path(record)
        if path is None:
            if skipped_reasons is not None:
                skipped_reasons["alignment_failed"] = (
                    skipped_reasons.get("alignment_failed", 0) + 1
                )
            continue
        paths.append(path)
    return tuple(paths)


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


def effective_width_strata(edges: Sequence[float]) -> tuple[PathStratum, ...]:
    return tuple(
        PathStratum(
            key="effective_angular_target_width",
            label=(
                f"W_eff [{lower:.3f}, {upper:.3f}"
                f"{']' if index == len(edges) - 2 else ')'} deg"
            ),
            lower=lower,
            upper=upper,
            include_upper=index == len(edges) - 2,
        )
        for index, (lower, upper) in enumerate(zip(edges, edges[1:]))
    )


def expected_break_duration_strata(
    tick_edges: Sequence[int],
) -> tuple[PathStratum, ...]:
    strata = []
    for lower, upper in zip(tick_edges, tick_edges[1:]):
        strata.append(
            PathStratum(
                key="expected_break_ms",
                label=(
                    f"expected {lower}-{upper - 1} ticks "
                    f"({lower * MINECRAFT_TICK_MS:g}-"
                    f"{(upper - 1) * MINECRAFT_TICK_MS:g} ms)"
                ),
                lower=lower * MINECRAFT_TICK_MS,
                upper=upper * MINECRAFT_TICK_MS,
            )
        )
    lower = tick_edges[-1]
    strata.append(
        PathStratum(
            key="expected_break_ms",
            label=(
                f"expected >= {lower} ticks "
                f"(>= {lower * MINECRAFT_TICK_MS:g} ms)"
            ),
            lower=lower * MINECRAFT_TICK_MS,
            upper=None,
        )
    )
    return tuple(strata)


def paths_in_stratum(
    paths: Sequence[AlignedPath],
    stratum: PathStratum,
) -> tuple[AlignedPath, ...]:
    if stratum.key == "effective_angular_target_width":
        return paths_in_bin(
            paths,
            stratum.lower,
            stratum.upper if stratum.upper is not None else math.inf,
            include_upper=stratum.include_upper,
        )
    if stratum.key != "expected_break_ms":
        raise ValueError(f"unsupported path stratum: {stratum.key}")
    return tuple(
        path
        for path in paths
        if path.expected_break_ms is not None
        and path.expected_break_ms >= stratum.lower
        and (stratum.upper is None or path.expected_break_ms < stratum.upper)
    )
