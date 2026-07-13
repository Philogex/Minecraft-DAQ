"""Unit-independent motion paths for cross-domain trajectory comparisons."""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from analysis.aim_features import AimPoint


@dataclass(frozen=True)
class NormalizedMotionPath:
    x: tuple[float, ...]
    y: tuple[float, ...]
    speed: tuple[float, ...]


def _interpolate(
    positions: Sequence[float],
    values: Sequence[float],
    query: float,
) -> float:
    if query <= positions[0]:
        return values[0]
    if query >= positions[-1]:
        return values[-1]
    lower = 0
    upper = len(positions) - 1
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if positions[middle] <= query:
            lower = middle
        else:
            upper = middle
    span = positions[upper] - positions[lower]
    fraction = (query - positions[lower]) / span if span > 0.0 else 0.0
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def resample_aligned_motion(
    x: Sequence[float],
    y: Sequence[float],
    times_ms: Sequence[float],
    *,
    sample_count: int = 101,
) -> NormalizedMotionPath | None:
    """Resample a start=(0,0), target=(1,0) path in unitless coordinates."""

    if len(x) != len(y) or len(x) != len(times_ms) or len(x) < 2:
        return None
    if sample_count < 2:
        raise ValueError("sample_count must be at least two")
    duration_ms = times_ms[-1] - times_ms[0]
    if duration_ms <= 0.0:
        return None
    progress = tuple((time - times_ms[0]) / duration_ms for time in times_ms)
    if any(current <= previous for previous, current in zip(progress, progress[1:])):
        return None
    grid = tuple(index / (sample_count - 1) for index in range(sample_count))
    resampled_x = tuple(_interpolate(progress, x, value) for value in grid)
    resampled_y = tuple(_interpolate(progress, y, value) for value in grid)

    speed_positions: list[float] = []
    normalized_speeds: list[float] = []
    duration_s = duration_ms / 1000.0
    for index in range(1, len(times_ms)):
        dt_s = (times_ms[index] - times_ms[index - 1]) / 1000.0
        if dt_s <= 0.0:
            continue
        speed_positions.append((progress[index] + progress[index - 1]) / 2.0)
        speed_in_d_per_s = math.hypot(
            x[index] - x[index - 1],
            y[index] - y[index - 1],
        ) / dt_s
        normalized_speeds.append(speed_in_d_per_s * duration_s)
    if not normalized_speeds:
        return None
    resampled_speed = tuple(
        _interpolate(speed_positions, normalized_speeds, value) for value in grid
    )
    return NormalizedMotionPath(resampled_x, resampled_y, resampled_speed)


def normalize_endpoint_motion(
    points: Sequence[AimPoint],
    *,
    sample_count: int = 101,
) -> NormalizedMotionPath | None:
    """Align a two-dimensional path from its first point to its final point."""

    if len(points) < 2:
        return None
    start = points[0]
    dx = points[-1].yaw - start.yaw
    dy = points[-1].pitch - start.pitch
    distance_squared = dx * dx + dy * dy
    if distance_squared <= 0.0:
        return None
    x = tuple(
        ((point.yaw - start.yaw) * dx + (point.pitch - start.pitch) * dy)
        / distance_squared
        for point in points
    )
    y = tuple(
        (-(point.yaw - start.yaw) * dy + (point.pitch - start.pitch) * dx)
        / distance_squared
        for point in points
    )
    return resample_aligned_motion(
        x,
        y,
        tuple(point.t_ms for point in points),
        sample_count=sample_count,
    )


def write_reference_paths(
    path: Path,
    paths: Sequence[NormalizedMotionPath],
    metadata: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "metadata": metadata,
        "paths": [asdict(item) for item in paths],
    }
    with gzip.open(path, "wt", encoding="utf-8") as file:
        json.dump(payload, file, separators=(",", ":"), allow_nan=False)


def load_reference_paths(
    path: Path,
) -> tuple[tuple[NormalizedMotionPath, ...], dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        payload = json.load(file)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("paths"), list):
        raise ValueError(f"unsupported normalized path cache: {path}")
    paths = tuple(
        NormalizedMotionPath(
            x=tuple(float(value) for value in item["x"]),
            y=tuple(float(value) for value in item["y"]),
            speed=tuple(float(value) for value in item["speed"]),
        )
        for item in payload["paths"]
    )
    metadata = payload.get("metadata", {})
    return paths, metadata if isinstance(metadata, dict) else {}
