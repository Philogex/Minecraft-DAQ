"""Feature extraction for generated or recorded two-dimensional trajectories."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


COMPARISON_FEATURE_NAMES = (
    "fitts_mt",
    "fitts_id",
    "fitts_residual",
    "fitts_residual_ratio",
    "sub_peak_count",
    "sub_primary_amp_ratio",
    "sub_correction_onset",
    "sub_interpeak_cv",
    "sub_peak_speed_ratio",
    "smooth_jerk_rms",
    "smooth_norm_jerk",
    "smooth_ldlj",
    "smooth_curvature_change_rate",
    "geo_path_efficiency",
    "geo_max_deviation",
    "geo_angular_dev_at_peak",
    "geo_curvature_integral",
)

@dataclass(frozen=True)
class AimPoint:
    """One orientation or cursor sample in a target-relative trajectory."""

    yaw: float
    pitch: float
    t_ms: float


@dataclass(frozen=True)
class TargetMetrics:
    """Endpoint and effective target size in the trajectory coordinate system."""

    yaw: float
    pitch: float
    width_yaw: float
    width_pitch: float
    distance: float = 0.0


@dataclass(frozen=True)
class AimPathFeatures:
    fitts_mt: float
    fitts_id: float
    fitts_predicted_mt: float
    fitts_residual: float
    fitts_residual_ratio: float
    sub_peak_count: int
    sub_primary_amp_ratio: float
    sub_correction_onset: float
    sub_interpeak_cv: float
    sub_peak_speed_ratio: float
    smooth_jerk_rms: float
    smooth_norm_jerk: float
    smooth_ldlj: float
    smooth_curvature_change_rate: float
    geo_path_efficiency: float
    geo_max_deviation: float
    geo_angular_dev_at_peak: float
    geo_curvature_integral: float


@dataclass(frozen=True)
class AimPathSeries:
    times_ms: tuple[float, ...]
    yaws: tuple[float, ...]
    pitches: tuple[float, ...]
    speeds_deg_s: tuple[float, ...]
    speed_times_ms: tuple[float, ...]


def shortest_yaw_delta(source: float, target: float) -> float:
    return ((target - source + 180.0) % 360.0) - 180.0


def _axis_delta(source: float, target: float, *, wrap_yaw: bool) -> float:
    if wrap_yaw:
        return shortest_yaw_delta(source, target)
    return target - source


def unwrap_yaws(points: Sequence[AimPoint], *, wrap_yaw: bool = True) -> list[float]:
    if not points:
        return []

    unwrapped = [points[0].yaw]
    for point in points[1:]:
        unwrapped.append(
            unwrapped[-1] + _axis_delta(unwrapped[-1], point.yaw, wrap_yaw=wrap_yaw)
        )
    return unwrapped


def aim_path_series(
    points: Sequence[AimPoint],
    *,
    wrap_yaw: bool = True,
) -> AimPathSeries:
    yaws = tuple(unwrap_yaws(points, wrap_yaw=wrap_yaw))
    pitches = tuple(point.pitch for point in points)
    times = tuple(point.t_ms for point in points)
    speeds = []
    speed_times = []
    for index in range(1, len(points)):
        dt_s = (times[index] - times[index - 1]) / 1000.0
        if dt_s <= 0.0:
            speeds.append(0.0)
        else:
            dyaw = yaws[index] - yaws[index - 1]
            dpitch = pitches[index] - pitches[index - 1]
            speeds.append(math.hypot(dyaw, dpitch) / dt_s)
        speed_times.append((times[index] + times[index - 1]) / 2.0)
    return AimPathSeries(
        times_ms=times,
        yaws=yaws,
        pitches=pitches,
        speeds_deg_s=tuple(speeds),
        speed_times_ms=tuple(speed_times),
    )


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0.0 or not math.isfinite(denominator):
        return math.nan
    return numerator / denominator


def _straight_distance(
    start_yaw: float,
    start_pitch: float,
    target: TargetMetrics,
    *,
    wrap_yaw: bool,
) -> float:
    return math.hypot(
        _axis_delta(start_yaw, target.yaw, wrap_yaw=wrap_yaw),
        target.pitch - start_pitch,
    )


def _path_length(series: AimPathSeries) -> float:
    total = 0.0
    for index in range(1, len(series.times_ms)):
        total += math.hypot(
            series.yaws[index] - series.yaws[index - 1],
            series.pitches[index] - series.pitches[index - 1],
        )
    return total


def _speed_peaks(speeds: Sequence[float]) -> list[int]:
    if not speeds:
        return []
    max_speed = max(speeds)
    if max_speed <= 0.0:
        return []
    threshold = max_speed * 0.15
    peaks = []
    for index, speed in enumerate(speeds):
        previous_speed = speeds[index - 1] if index > 0 else -math.inf
        next_speed = speeds[index + 1] if index + 1 < len(speeds) else -math.inf
        if speed > threshold and speed >= previous_speed and speed > next_speed:
            peaks.append(index)
    return peaks


def _coefficient_of_variation(values: Sequence[float]) -> float:
    if len(values) < 2:
        return math.nan
    mean = sum(values) / len(values)
    if mean == 0.0:
        return math.nan
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def _correction_onset(
    peaks: Sequence[int],
    speeds: Sequence[float],
    speed_times: Sequence[float],
) -> float:
    if len(peaks) < 2:
        return math.nan
    primary = peaks[0]
    next_peak = peaks[1]
    if next_peak <= primary + 1:
        return speed_times[next_peak]
    valley = min(range(primary + 1, next_peak + 1), key=lambda index: speeds[index])
    return speed_times[valley]


def _max_perpendicular_deviation(
    series: AimPathSeries,
    start_yaw: float,
    start_pitch: float,
    target: TargetMetrics,
    *,
    wrap_yaw: bool,
) -> float:
    vx = _axis_delta(start_yaw, target.yaw, wrap_yaw=wrap_yaw)
    vy = target.pitch - start_pitch
    length = math.hypot(vx, vy)
    if length == 0.0:
        return 0.0
    max_deviation = 0.0
    for yaw, pitch in zip(series.yaws, series.pitches):
        px = yaw - series.yaws[0]
        py = pitch - start_pitch
        deviation = abs(vx * py - vy * px) / length
        max_deviation = max(max_deviation, deviation)
    return max_deviation


def _angular_deviation_at_peak(
    series: AimPathSeries,
    start_yaw: float,
    start_pitch: float,
    target: TargetMetrics,
    *,
    wrap_yaw: bool,
) -> float:
    if not series.speeds_deg_s:
        return math.nan
    peak_index = max(range(len(series.speeds_deg_s)), key=series.speeds_deg_s.__getitem__)
    dx = series.yaws[peak_index + 1] - series.yaws[peak_index]
    dy = series.pitches[peak_index + 1] - series.pitches[peak_index]
    tx = _axis_delta(start_yaw, target.yaw, wrap_yaw=wrap_yaw)
    ty = target.pitch - start_pitch
    move_length = math.hypot(dx, dy)
    target_length = math.hypot(tx, ty)
    if move_length == 0.0 or target_length == 0.0:
        return math.nan
    cosine = max(-1.0, min(1.0, (dx * tx + dy * ty) / (move_length * target_length)))
    return math.degrees(math.acos(cosine))


def _curvature_integral(series: AimPathSeries) -> float:
    headings = []
    for index in range(1, len(series.times_ms)):
        dx = series.yaws[index] - series.yaws[index - 1]
        dy = series.pitches[index] - series.pitches[index - 1]
        if dx != 0.0 or dy != 0.0:
            headings.append(math.atan2(dy, dx))
    if len(headings) < 2:
        return 0.0

    total = 0.0
    for previous, current in zip(headings, headings[1:]):
        delta = current - previous
        while delta <= -math.pi:
            delta += 2.0 * math.pi
        while delta > math.pi:
            delta -= 2.0 * math.pi
        total += abs(delta)
    return total


def _gradient(values: Sequence[float], times: Sequence[float]) -> list[float]:
    """Equivalent to NumPy's first-order gradient for non-uniform coordinates."""

    if len(values) != len(times) or len(values) < 2:
        return []
    result = [(values[1] - values[0]) / (times[1] - times[0])]
    for index in range(1, len(values) - 1):
        previous_step = times[index] - times[index - 1]
        next_step = times[index + 1] - times[index]
        previous_scale = -next_step / (
            previous_step * (previous_step + next_step)
        )
        center_scale = (next_step - previous_step) / (
            previous_step * next_step
        )
        next_scale = previous_step / (next_step * (previous_step + next_step))
        result.append(
            previous_scale * values[index - 1]
            + center_scale * values[index]
            + next_scale * values[index + 1]
        )
    result.append((values[-1] - values[-2]) / (times[-1] - times[-2]))
    return result


def _smoothness_features(series: AimPathSeries) -> tuple[float, float, float, float]:
    """Port the clean smoothness family from ck0i/sigmadrift-detector."""

    if len(series.times_ms) < 6:
        return 0.0, 0.0, 0.0, 0.0
    times: list[float] = []
    yaws: list[float] = []
    pitches: list[float] = []
    for time_ms, yaw, pitch in zip(
        series.times_ms,
        series.yaws,
        series.pitches,
    ):
        time_s = time_ms / 1000.0
        if times and time_s - times[-1] <= 1e-9:
            continue
        times.append(time_s)
        yaws.append(yaw)
        pitches.append(pitch)
    if len(times) < 6:
        return 0.0, 0.0, 0.0, 0.0

    velocity_yaw = _gradient(yaws, times)
    velocity_pitch = _gradient(pitches, times)
    acceleration_yaw = _gradient(velocity_yaw, times)
    acceleration_pitch = _gradient(velocity_pitch, times)
    jerk_yaw = _gradient(acceleration_yaw, times)
    jerk_pitch = _gradient(acceleration_pitch, times)
    jerk_magnitude = [
        math.hypot(yaw, pitch)
        for yaw, pitch in zip(jerk_yaw, jerk_pitch)
    ]
    if not all(math.isfinite(value) for value in jerk_magnitude):
        return 0.0, 0.0, 0.0, 0.0
    jerk_rms = math.sqrt(
        sum(value * value for value in jerk_magnitude) / len(jerk_magnitude)
    )

    movement_time = times[-1] - times[0]
    distance = math.hypot(yaws[-1] - yaws[0], pitches[-1] - pitches[0])
    jerk_integral = 0.0
    for index in range(len(times) - 1):
        jerk_pair = (
            jerk_magnitude[index] * jerk_magnitude[index]
            + jerk_magnitude[index + 1] * jerk_magnitude[index + 1]
        )
        jerk_integral += jerk_pair * (times[index + 1] - times[index]) / 4.0
    if distance > 1.0 and movement_time > 0.001:
        normalized_jerk_squared = (
            jerk_integral * movement_time**5 / distance**2
        )
        normalized_jerk = math.sqrt(max(0.5 * normalized_jerk_squared, 0.0))
        ldlj = -math.log(max(normalized_jerk_squared, 1e-30))
    else:
        normalized_jerk = 0.0
        ldlj = 0.0

    speed = [
        math.hypot(yaw, pitch)
        for yaw, pitch in zip(velocity_yaw, velocity_pitch)
    ]
    curvature = []
    for vx, vy, ax, ay, magnitude in zip(
        velocity_yaw,
        velocity_pitch,
        acceleration_yaw,
        acceleration_pitch,
        speed,
    ):
        safe_speed = magnitude if magnitude > 1e-6 else 1e-6
        curvature.append(abs(vx * ay - vy * ax) / safe_speed**3)
    curvature_derivative = _gradient(curvature, times)
    curvature_change_rate = math.sqrt(
        sum(value * value for value in curvature_derivative)
        / len(curvature_derivative)
    )
    result = (jerk_rms, normalized_jerk, ldlj, curvature_change_rate)
    if not all(math.isfinite(value) for value in result):
        return 0.0, 0.0, 0.0, 0.0
    return result


def compute_aim_path_features(
    points: Sequence[AimPoint],
    target: TargetMetrics,
    *,
    fitts_a_ms: float,
    fitts_b_ms: float,
    fallback_width_deg: float,
    wrap_yaw: bool = True,
) -> AimPathFeatures:
    if len(points) < 2:
        nan = math.nan
        return AimPathFeatures(
            fitts_mt=0.0,
            fitts_id=nan,
            fitts_predicted_mt=nan,
            fitts_residual=nan,
            fitts_residual_ratio=nan,
            sub_peak_count=0,
            sub_primary_amp_ratio=nan,
            sub_correction_onset=nan,
            sub_interpeak_cv=nan,
            sub_peak_speed_ratio=nan,
            smooth_jerk_rms=nan,
            smooth_norm_jerk=nan,
            smooth_ldlj=nan,
            smooth_curvature_change_rate=nan,
            geo_path_efficiency=nan,
            geo_max_deviation=nan,
            geo_angular_dev_at_peak=nan,
            geo_curvature_integral=nan,
        )

    series = aim_path_series(points, wrap_yaw=wrap_yaw)
    start_yaw = points[0].yaw
    start_pitch = points[0].pitch
    movement_time = points[-1].t_ms - points[0].t_ms
    straight_distance = _straight_distance(
        start_yaw,
        start_pitch,
        target,
        wrap_yaw=wrap_yaw,
    )
    target_width = max(
        fallback_width_deg,
        min(
            width
            for width in (target.width_yaw, target.width_pitch)
            if width > 0.0
        )
        if target.width_yaw > 0.0 or target.width_pitch > 0.0
        else fallback_width_deg,
    )
    fitts_id = math.log2(straight_distance / target_width + 1.0)
    predicted_mt = fitts_a_ms + fitts_b_ms * fitts_id
    residual = movement_time - predicted_mt

    peaks = _speed_peaks(series.speeds_deg_s)
    peak_times = [series.speed_times_ms[index] for index in peaks]
    peak_speeds = [series.speeds_deg_s[index] for index in peaks]
    primary_peak = peaks[0] if peaks else None
    primary_amp_ratio = math.nan
    if primary_peak is not None and straight_distance > 0.0:
        primary_yaw = series.yaws[primary_peak + 1]
        primary_pitch = series.pitches[primary_peak + 1]
        primary_amplitude = math.hypot(
            primary_yaw - series.yaws[0],
            primary_pitch - start_pitch,
        )
        primary_amp_ratio = primary_amplitude / straight_distance

    interpeak_intervals = [
        current - previous
        for previous, current in zip(peak_times, peak_times[1:])
    ]
    primary_speed = peak_speeds[0] if peak_speeds else math.nan
    secondary_speed = max(peak_speeds[1:]) if len(peak_speeds) > 1 else math.nan
    path_length = _path_length(series)
    (
        smooth_jerk_rms,
        smooth_norm_jerk,
        smooth_ldlj,
        smooth_curvature_change_rate,
    ) = _smoothness_features(series)

    return AimPathFeatures(
        fitts_mt=movement_time,
        fitts_id=fitts_id,
        fitts_predicted_mt=predicted_mt,
        fitts_residual=residual,
        fitts_residual_ratio=_safe_ratio(residual, predicted_mt),
        sub_peak_count=len(peaks),
        sub_primary_amp_ratio=primary_amp_ratio,
        sub_correction_onset=_correction_onset(peaks, series.speeds_deg_s, series.speed_times_ms),
        sub_interpeak_cv=_coefficient_of_variation(interpeak_intervals),
        sub_peak_speed_ratio=_safe_ratio(secondary_speed, primary_speed),
        smooth_jerk_rms=smooth_jerk_rms,
        smooth_norm_jerk=smooth_norm_jerk,
        smooth_ldlj=smooth_ldlj,
        smooth_curvature_change_rate=smooth_curvature_change_rate,
        geo_path_efficiency=_safe_ratio(straight_distance, path_length),
        geo_max_deviation=_max_perpendicular_deviation(
            series,
            start_yaw,
            start_pitch,
            target,
            wrap_yaw=wrap_yaw,
        ),
        geo_angular_dev_at_peak=_angular_deviation_at_peak(
            series,
            start_yaw,
            start_pitch,
            target,
            wrap_yaw=wrap_yaw,
        ),
        geo_curvature_integral=_curvature_integral(series),
    )
