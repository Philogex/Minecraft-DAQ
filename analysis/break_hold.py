"""Camera stability measurements during a recorded block-break interval."""

from __future__ import annotations

import math
from dataclasses import dataclass

from analysis.mining_context import BreakTiming, break_timing
from analysis.mining_session import RecordedMiningEvent, StateSample


@dataclass(frozen=True)
class BreakHoldPath:
    event_id: int
    elapsed_ms: tuple[float, ...]
    delta_yaw: tuple[float, ...]
    delta_pitch: tuple[float, ...]
    radial_displacement: tuple[float, ...]
    timing: BreakTiming
    player_displacement: float
    weight: float = 1.0


def sensitivity_to_angular_step_deg(sensitivity: float) -> float:
    """Return Minecraft's degrees per raw mouse-delta unit."""

    if not math.isfinite(sensitivity) or not 0.0 <= sensitivity <= 1.0:
        return math.nan
    scaled = sensitivity * 0.6 + 0.2
    return scaled * scaled * scaled * 1.2


def _last_state_at_or_before(
    samples: tuple[StateSample, ...],
    relative_ms: float,
) -> StateSample | None:
    return max(
        (sample for sample in samples if sample.relative_ms <= relative_ms),
        key=lambda sample: sample.relative_ms,
        default=None,
    )


def extract_break_hold_path(
    recorded: RecordedMiningEvent,
    *,
    position_tolerance: float,
    weight: float = 1.0,
) -> tuple[BreakHoldPath | None, str | None]:
    """Reconstruct cumulative camera displacement from break start to end."""

    timing = break_timing(recorded.event)
    if timing is None:
        return None, "missing_break_timing"
    if not recorded.state_samples:
        return None, "missing_state_samples"
    if not math.isfinite(position_tolerance) or position_tolerance < 0.0:
        raise ValueError("position_tolerance must be finite and non-negative")
    if not math.isfinite(weight) or weight <= 0.0:
        raise ValueError("weight must be finite and positive")

    start_relative_ms = -timing.actual_break_ms
    start_state = _last_state_at_or_before(
        recorded.state_samples,
        start_relative_ms,
    )
    end_state = _last_state_at_or_before(recorded.state_samples, 0.0)
    if start_state is None:
        return None, "break_start_outside_state_window"
    if end_state is None:
        return None, "missing_break_end_state"

    start_position = (
        start_state.player_x,
        start_state.player_y,
        start_state.player_z,
    )
    end_position = (
        end_state.player_x,
        end_state.player_y,
        end_state.player_z,
    )
    if not all(math.isfinite(value) for value in start_position + end_position):
        return None, "invalid_player_position"
    player_displacement = math.dist(start_position, end_position)
    if player_displacement > position_tolerance:
        return None, "player_position_changed_during_break"

    angular_step = sensitivity_to_angular_step_deg(start_state.sensitivity)
    if not math.isfinite(angular_step) or angular_step <= 0.0:
        return None, "invalid_sensitivity"

    elapsed_ms = [0.0]
    delta_yaw = [0.0]
    delta_pitch = [0.0]
    yaw = 0.0
    pitch = 0.0
    for sample in recorded.mouse_samples:
        if not start_relative_ms < sample.relative_ms <= 0.0:
            continue
        if not all(
            math.isfinite(value)
            for value in (sample.relative_ms, sample.mouse_dx, sample.mouse_dy)
        ):
            return None, "invalid_mouse_sample"
        yaw += sample.mouse_dx * angular_step
        pitch += sample.mouse_dy * angular_step
        sample_elapsed_ms = sample.relative_ms - start_relative_ms
        if sample_elapsed_ms <= elapsed_ms[-1]:
            elapsed_ms[-1] = max(elapsed_ms[-1], sample_elapsed_ms)
            delta_yaw[-1] = yaw
            delta_pitch[-1] = pitch
        else:
            elapsed_ms.append(sample_elapsed_ms)
            delta_yaw.append(yaw)
            delta_pitch.append(pitch)

    if timing.actual_break_ms > elapsed_ms[-1]:
        elapsed_ms.append(timing.actual_break_ms)
        delta_yaw.append(yaw)
        delta_pitch.append(pitch)
    radial = tuple(
        math.hypot(yaw_value, pitch_value)
        for yaw_value, pitch_value in zip(delta_yaw, delta_pitch)
    )
    return (
        BreakHoldPath(
            event_id=recorded.event.event_id,
            elapsed_ms=tuple(elapsed_ms),
            delta_yaw=tuple(delta_yaw),
            delta_pitch=tuple(delta_pitch),
            radial_displacement=radial,
            timing=timing,
            player_displacement=player_displacement,
            weight=weight,
        ),
        None,
    )


def time_rms(values: tuple[float, ...], elapsed_ms: tuple[float, ...]) -> float:
    if len(values) != len(elapsed_ms) or len(values) < 2:
        return math.nan
    duration = elapsed_ms[-1] - elapsed_ms[0]
    if duration <= 0.0:
        return math.nan
    integral = sum(
        0.5 * (first * first + second * second) * (second_t - first_t)
        for first, second, first_t, second_t in zip(
            values,
            values[1:],
            elapsed_ms,
            elapsed_ms[1:],
        )
    )
    return math.sqrt(max(0.0, integral / duration))


def path_metrics(path: BreakHoldPath) -> dict[str, float]:
    path_length = sum(
        math.hypot(second_yaw - first_yaw, second_pitch - first_pitch)
        for first_yaw, second_yaw, first_pitch, second_pitch in zip(
            path.delta_yaw,
            path.delta_yaw[1:],
            path.delta_pitch,
            path.delta_pitch[1:],
        )
    )
    return {
        "yaw_rms_deg": time_rms(path.delta_yaw, path.elapsed_ms),
        "pitch_rms_deg": time_rms(path.delta_pitch, path.elapsed_ms),
        "radial_rms_deg": time_rms(path.radial_displacement, path.elapsed_ms),
        "yaw_max_abs_deg": max(abs(value) for value in path.delta_yaw),
        "pitch_max_abs_deg": max(abs(value) for value in path.delta_pitch),
        "radial_max_deg": max(path.radial_displacement),
        "yaw_final_deg": path.delta_yaw[-1],
        "pitch_final_deg": path.delta_pitch[-1],
        "radial_final_deg": path.radial_displacement[-1],
        "angular_path_length_deg": path_length,
    }
