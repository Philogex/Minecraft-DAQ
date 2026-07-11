"""Detect the final target-directed movement episode in a DAQ sample window."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from analysis.aim_features import AimPoint, shortest_yaw_delta
from analysis.path_density import AngularTarget, effective_target_width


@dataclass(frozen=True)
class MovementSegmentationConfig:
    max_idle_gap_ms: float = 150.0
    active_step_fraction: float = 0.5
    minimum_motion_ratio: float = 0.1
    minimum_progress_fraction: float = 0.25
    target_tolerance_factor: float = 1.0
    max_player_displacement: float = 0.05


@dataclass(frozen=True)
class MovementSegment:
    points: tuple[AimPoint, ...]
    start_index: int
    end_index: int
    start_error_deg: float
    end_error_deg: float
    path_length_deg: float
    effective_width_deg: float


@dataclass(frozen=True)
class MovementSegmentationResult:
    segment: MovementSegment | None
    reason: str | None
    candidate_count: int


def _angular_delta(first: AimPoint, second: AimPoint) -> float:
    return math.hypot(
        shortest_yaw_delta(first.yaw, second.yaw),
        second.pitch - first.pitch,
    )


def _target_error(point: AimPoint, target: AngularTarget) -> float:
    return math.hypot(
        shortest_yaw_delta(point.yaw, target.yaw),
        target.pitch - point.pitch,
    )


def _active_clusters(
    points: Sequence[AimPoint],
    active_threshold_deg: float,
    max_idle_gap_ms: float,
) -> tuple[tuple[int, int], ...]:
    active_intervals = [
        index
        for index, (first, second) in enumerate(zip(points, points[1:]))
        if _angular_delta(first, second) >= active_threshold_deg
    ]
    if not active_intervals:
        return ()

    clusters: list[tuple[int, int]] = []
    start = active_intervals[0]
    previous = start
    for current in active_intervals[1:]:
        idle_gap_ms = points[current].t_ms - points[previous + 1].t_ms
        if idle_gap_ms > max_idle_gap_ms:
            clusters.append((start, previous + 1))
            start = current
        previous = current
    clusters.append((start, previous + 1))
    return tuple(clusters)


def segment_target_movement(
    points: Sequence[AimPoint],
    target: AngularTarget,
    *,
    angular_step_deg: float,
    player_positions: Sequence[tuple[float, float, float]] | None = None,
    config: MovementSegmentationConfig = MovementSegmentationConfig(),
) -> MovementSegmentationResult:
    """Return the latest credible movement episode ending at the target."""

    if len(points) < 2:
        return MovementSegmentationResult(None, "insufficient_samples", 0)
    if angular_step_deg <= 0.0 or not math.isfinite(angular_step_deg):
        return MovementSegmentationResult(None, "invalid_angular_step", 0)
    if any(
        not math.isfinite(value)
        for point in points
        for value in (point.yaw, point.pitch, point.t_ms)
    ):
        return MovementSegmentationResult(None, "non_finite_sample", 0)
    if any(second.t_ms <= first.t_ms for first, second in zip(points, points[1:])):
        return MovementSegmentationResult(None, "non_increasing_timestamps", 0)
    if player_positions is not None:
        if len(player_positions) != len(points):
            return MovementSegmentationResult(None, "position_sample_count_mismatch", 0)
        if any(
            not all(math.isfinite(value) for value in position)
            for position in player_positions
        ):
            return MovementSegmentationResult(None, "non_finite_player_position", 0)

    active_threshold = angular_step_deg * config.active_step_fraction
    clusters = _active_clusters(
        points,
        active_threshold,
        config.max_idle_gap_ms,
    )
    if not clusters:
        return MovementSegmentationResult(None, "no_active_intervals", 0)

    rejected_for_target = False
    rejected_for_progress = False
    rejected_for_amplitude = False
    for start_index, end_index in reversed(clusters):
        episode = tuple(points[start_index : end_index + 1])
        start_error = _target_error(episode[0], target)
        end_error = _target_error(episode[-1], target)
        movement_yaw = shortest_yaw_delta(episode[0].yaw, target.yaw)
        movement_pitch = target.pitch - episode[0].pitch
        width = effective_target_width(
            movement_yaw,
            movement_pitch,
            target.width_yaw,
            target.width_pitch,
        )
        if not math.isfinite(width):
            continue
        path_length = sum(
            _angular_delta(first, second)
            for first, second in zip(episode, episode[1:])
        )
        minimum_motion = max(
            angular_step_deg,
            min(start_error, width) * config.minimum_motion_ratio,
        )
        if path_length < minimum_motion:
            rejected_for_amplitude = True
            continue
        if player_positions is not None:
            start_position = player_positions[start_index]
            displacement = max(
                math.dist(start_position, player_positions[index])
                for index in range(start_index, end_index + 1)
            )
            if displacement > config.max_player_displacement:
                return MovementSegmentationResult(
                    None,
                    "player_position_changed",
                    len(clusters),
                )
        if start_error - end_error < minimum_motion * config.minimum_progress_fraction:
            rejected_for_progress = True
            continue
        target_tolerance = max(
            angular_step_deg * 2.0,
            width * 0.5 * config.target_tolerance_factor,
        )
        if end_error > target_tolerance:
            rejected_for_target = True
            continue
        return MovementSegmentationResult(
            MovementSegment(
                points=episode,
                start_index=start_index,
                end_index=end_index,
                start_error_deg=start_error,
                end_error_deg=end_error,
                path_length_deg=path_length,
                effective_width_deg=width,
            ),
            None,
            len(clusters),
        )

    if rejected_for_target:
        reason = "movement_does_not_end_at_target"
    elif rejected_for_progress:
        reason = "movement_does_not_approach_target"
    elif rejected_for_amplitude:
        reason = "movement_below_minimum_amplitude"
    else:
        reason = "invalid_target_width"
    return MovementSegmentationResult(None, reason, len(clusters))
