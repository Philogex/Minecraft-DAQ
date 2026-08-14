"""Mining-duration context shared by trajectory analyses."""

from __future__ import annotations

import math
from dataclasses import dataclass

from analysis.mining_session import MiningEvent, RecordedMiningEvent, StateSample


MINECRAFT_TICK_MS = 50.0
DEFAULT_MIN_BREAK_DELAY_RATIO = 0.5
DEFAULT_MAX_BREAK_DELAY_RATIO = 2.0
DEFAULT_BREAK_TICK_EDGES = (1, 3, 6, 11, 21)


@dataclass(frozen=True)
class BreakTiming:
    actual_break_ms: float
    expected_break_ticks: int
    expected_break_ms: float
    break_delay_ratio: float


def break_timing(event: MiningEvent) -> BreakTiming | None:
    """Return comparable break timing when a DAQ v2 start was recorded."""

    if event.start_time_ns is None or event.expected_break_ticks is None:
        return None
    if event.expected_break_ticks <= 0 or event.event_time_ns < event.start_time_ns:
        return None
    actual_break_ms = (event.event_time_ns - event.start_time_ns) / 1_000_000.0
    expected_break_ms = event.expected_break_ticks * MINECRAFT_TICK_MS

    # Minecraft applies the first destroy-progress increment in the start call,
    # before one complete tick interval can appear in the wall-clock duration.
    observed_break_ticks = actual_break_ms / MINECRAFT_TICK_MS + 1.0
    delay_ratio = observed_break_ticks / event.expected_break_ticks
    if not all(
        math.isfinite(value)
        for value in (actual_break_ms, expected_break_ms, delay_ratio)
    ):
        return None
    return BreakTiming(
        actual_break_ms=actual_break_ms,
        expected_break_ticks=event.expected_break_ticks,
        expected_break_ms=expected_break_ms,
        break_delay_ratio=delay_ratio,
    )


def break_timing_rejection_reason(
    event: MiningEvent,
    *,
    minimum_ratio: float,
    maximum_ratio: float,
) -> str | None:
    """Reject only events that contain complete but implausible v2 timing."""

    if event.start_time_ns is None and event.expected_break_ticks is None:
        return None
    timing = break_timing(event)
    if timing is None:
        return "invalid_break_timing"
    if not minimum_ratio <= timing.break_delay_ratio <= maximum_ratio:
        return "break_delay_ratio_outlier"
    return None


def state_sample_at_break_start(
    recorded: RecordedMiningEvent,
) -> StateSample | None:
    """Return the last tick sample at or before the recorded attack start."""

    timing = break_timing(recorded.event)
    if timing is None:
        return None
    start_relative_ms = -timing.actual_break_ms
    return max(
        (
            sample
            for sample in recorded.state_samples
            if sample.relative_ms <= start_relative_ms
        ),
        key=lambda sample: sample.relative_ms,
        default=None,
    )


def parse_break_tick_edges(text: str) -> tuple[int, ...]:
    try:
        edges = tuple(int(value.strip()) for value in text.split(","))
    except ValueError as error:
        raise ValueError("break tick edges must be comma-separated integers") from error
    if len(edges) < 1 or edges[0] < 1:
        raise ValueError("break tick edges must start at one or greater")
    if any(current <= previous for previous, current in zip(edges, edges[1:])):
        raise ValueError("break tick edges must be strictly increasing")
    return edges
