#!/usr/bin/env python3
"""Plot camera displacement during block-break hold intervals."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.break_hold import BreakHoldPath, extract_break_hold_path, path_metrics
from analysis.dataset_groups import add_dataset_arguments, resolve_dataset_groups
from analysis.mining_context import (
    DEFAULT_BREAK_TICK_EDGES,
    DEFAULT_MAX_BREAK_DELAY_RATIO,
    DEFAULT_MIN_BREAK_DELAY_RATIO,
    break_timing_rejection_reason,
    parse_break_tick_edges,
)
from analysis.mining_session import MiningSession, load_mining_session
from analysis.path_density import (
    PathStratum,
    expected_break_duration_strata,
    weighted_quantile,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot radial camera-displacement density between block-break start "
            "and completion, stratified by expected break duration."
        )
    )
    add_dataset_arguments(parser)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("break-hold-stability.png"),
    )
    parser.add_argument(
        "--break-tick-edges",
        default=",".join(str(value) for value in DEFAULT_BREAK_TICK_EDGES),
    )
    parser.add_argument("--histogram-bins", type=int, default=80)
    parser.add_argument("--time-samples", type=int, default=121)
    parser.add_argument("--time-quantile", type=float, default=0.99)
    parser.add_argument("--displacement-quantile", type=float, default=0.99)
    parser.add_argument(
        "--position-tolerance",
        type=float,
        default=1.0e-6,
        help=(
            "Maximum start-to-end player-position difference in blocks "
            "(default: 1e-6)."
        ),
    )
    parser.add_argument(
        "--min-break-delay-ratio",
        type=float,
        default=DEFAULT_MIN_BREAK_DELAY_RATIO,
    )
    parser.add_argument(
        "--max-break-delay-ratio",
        type=float,
        default=DEFAULT_MAX_BREAK_DELAY_RATIO,
    )
    parser.add_argument("--no-break-delay-filter", action="store_true")
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _generated_weights(session: MiningSession) -> dict[int, float]:
    raw_events = session.metadata.get("events", [])
    if not isinstance(raw_events, list):
        return {}
    weights: dict[int, float] = {}
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        try:
            event_id = int(item["generated_event_id"])
            weight = float(item.get("analysis_weight", 1.0))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(weight) and weight > 0.0:
            weights[event_id] = weight
    return weights


def _paths_for_session(
    session: MiningSession,
    *,
    position_tolerance: float,
    filter_break_delay: bool,
    minimum_break_delay_ratio: float,
    maximum_break_delay_ratio: float,
) -> tuple[tuple[BreakHoldPath, ...], dict[str, int]]:
    weights = _generated_weights(session)
    paths: list[BreakHoldPath] = []
    skipped: dict[str, int] = {}
    for recorded in session.events:
        if filter_break_delay:
            reason = break_timing_rejection_reason(
                recorded.event,
                minimum_ratio=minimum_break_delay_ratio,
                maximum_ratio=maximum_break_delay_ratio,
            )
            if reason is not None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
        path, reason = extract_break_hold_path(
            recorded,
            position_tolerance=position_tolerance,
            weight=weights.get(recorded.event.event_id, 1.0),
        )
        if path is None:
            key = reason or "unknown_hold_extraction_failure"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        paths.append(path)
    return tuple(paths), skipped


def _paths_in_stratum(
    paths: tuple[BreakHoldPath, ...],
    stratum: PathStratum,
) -> tuple[BreakHoldPath, ...]:
    return tuple(
        path
        for path in paths
        if path.timing.expected_break_ms >= stratum.lower
        and (
            stratum.upper is None
            or path.timing.expected_break_ms < stratum.upper
        )
    )


def _finite_quantile(
    values: list[float],
    weights: list[float],
    quantile: float,
) -> float | None:
    finite = [
        (value, weight)
        for value, weight in zip(values, weights)
        if math.isfinite(value) and math.isfinite(weight) and weight > 0.0
    ]
    if not finite:
        return None
    return weighted_quantile(
        [value for value, _ in finite],
        [weight for _, weight in finite],
        quantile,
    )


def _metric_summary(paths: tuple[BreakHoldPath, ...]) -> dict[str, object]:
    rows = [(path_metrics(path), path.weight) for path in paths]
    names = tuple(rows[0][0]) if rows else ()
    result: dict[str, object] = {}
    for name in names:
        values = [metrics[name] for metrics, _ in rows]
        weights = [weight for _, weight in rows]
        result[name] = {
            "weighted_median": _finite_quantile(values, weights, 0.5),
            "weighted_p05": _finite_quantile(values, weights, 0.05),
            "weighted_p95": _finite_quantile(values, weights, 0.95),
        }
    return result


def _directional_moments(
    profiles: list[tuple[BreakHoldPath, object, object, object]],
) -> dict[str, float | None]:
    samples: list[tuple[float, float, float]] = []
    for path, yaw_values, pitch_values, _ in profiles:
        point_weight = path.weight / len(yaw_values)
        samples.extend(
            (float(yaw), float(pitch), point_weight)
            for yaw, pitch in zip(yaw_values, pitch_values)
        )
    total_weight = sum(weight for _, _, weight in samples)
    if total_weight <= 0.0:
        return {
            "mean_yaw_deg": None,
            "mean_pitch_deg": None,
            "yaw_variance_deg2": None,
            "pitch_variance_deg2": None,
            "yaw_pitch_covariance_deg2": None,
            "anisotropy_std_ratio": None,
        }
    mean_yaw = sum(yaw * weight for yaw, _, weight in samples) / total_weight
    mean_pitch = sum(pitch * weight for _, pitch, weight in samples) / total_weight
    yaw_variance = sum(
        (yaw - mean_yaw) ** 2 * weight for yaw, _, weight in samples
    ) / total_weight
    pitch_variance = sum(
        (pitch - mean_pitch) ** 2 * weight for _, pitch, weight in samples
    ) / total_weight
    covariance = sum(
        (yaw - mean_yaw) * (pitch - mean_pitch) * weight
        for yaw, pitch, weight in samples
    ) / total_weight
    trace = yaw_variance + pitch_variance
    discriminant = math.sqrt(
        max(0.0, (yaw_variance - pitch_variance) ** 2 + 4.0 * covariance**2)
    )
    major = max(0.0, 0.5 * (trace + discriminant))
    minor = max(0.0, 0.5 * (trace - discriminant))
    anisotropy = math.sqrt(major / minor) if minor > 1.0e-18 else None
    return {
        "mean_yaw_deg": mean_yaw,
        "mean_pitch_deg": mean_pitch,
        "yaw_variance_deg2": yaw_variance,
        "pitch_variance_deg2": pitch_variance,
        "yaw_pitch_covariance_deg2": covariance,
        "anisotropy_std_ratio": anisotropy,
    }


def _profiles(paths: tuple[BreakHoldPath, ...], time_grid):
    import numpy as np

    result = []
    for path in paths:
        active = time_grid <= path.timing.actual_break_ms
        active_times = time_grid[active]
        result.append(
            (
                path,
                np.interp(active_times, path.elapsed_ms, path.delta_yaw),
                np.interp(active_times, path.elapsed_ms, path.delta_pitch),
                np.interp(
                    active_times,
                    path.elapsed_ms,
                    path.radial_displacement,
                ),
            )
        )
    return result


def _plot(
    datasets: list[tuple[str, tuple[BreakHoldPath, ...]]],
    strata: tuple[PathStratum, ...],
    output: Path,
    *,
    histogram_bins: int,
    time_samples: int,
    time_quantile: float,
    displacement_quantile: float,
    show: bool,
) -> list[dict[str, object]]:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import PowerNorm

    figure, axes = plt.subplots(
        len(strata),
        len(datasets),
        figsize=(6.4 * len(datasets), 4.8 * len(strata)),
        squeeze=False,
        constrained_layout=True,
    )
    figure.suptitle(
        "Break-hold camera stability by expected break duration\n"
        "radial displacement from the orientation at break start"
    )
    panel_reports: list[dict[str, object]] = []

    for row, stratum in enumerate(strata):
        binned = [
            _paths_in_stratum(paths, stratum)
            for _, paths in datasets
        ]
        pooled = tuple(path for paths in binned for path in paths)
        time_max = _finite_quantile(
            [path.timing.actual_break_ms for path in pooled],
            [path.weight for path in pooled],
            time_quantile,
        )
        time_max = max(1.0, time_max or 1.0)
        time_grid = np.linspace(0.0, time_max, time_samples)
        profiles_by_dataset = [_profiles(paths, time_grid) for paths in binned]

        radial_values: list[float] = []
        radial_weights: list[float] = []
        for profiles in profiles_by_dataset:
            for path, _, _, radial in profiles:
                radial_values.extend(float(value) for value in radial)
                radial_weights.extend(path.weight / len(radial) for _ in radial)
        radial_max = _finite_quantile(
            radial_values,
            radial_weights,
            displacement_quantile,
        )
        radial_max = max(1.0e-4, radial_max or 1.0e-4)
        radial_edges = np.linspace(0.0, radial_max, histogram_bins + 1)

        histograms = []
        for profiles in profiles_by_dataset:
            histogram = np.zeros((histogram_bins, time_samples))
            for time_index in range(time_samples):
                values = [
                    float(radial[time_index])
                    for _, _, _, radial in profiles
                    if time_index < len(radial)
                ]
                weights = [
                    path.weight
                    for path, _, _, radial in profiles
                    if time_index < len(radial)
                ]
                if not values:
                    continue
                counts, _ = np.histogram(values, bins=radial_edges, weights=weights)
                total = counts.sum()
                if total > 0.0:
                    histogram[:, time_index] = counts / total
            histograms.append(histogram)
        vmax = max(
            (float(histogram.max()) for histogram in histograms),
            default=1.0,
        )

        for column, ((label, _), paths, profiles, histogram) in enumerate(
            zip(datasets, binned, profiles_by_dataset, histograms)
        ):
            axis = axes[row][column]
            active_counts: list[int] = []
            active_weights: list[float] = []
            medians: list[float | None] = []
            p95_values: list[float | None] = []
            for time_index in range(time_samples):
                values = [
                    float(radial[time_index])
                    for _, _, _, radial in profiles
                    if time_index < len(radial)
                ]
                weights = [
                    path.weight
                    for path, _, _, radial in profiles
                    if time_index < len(radial)
                ]
                active_counts.append(len(values))
                active_weights.append(sum(weights))
                medians.append(_finite_quantile(values, weights, 0.5))
                p95_values.append(_finite_quantile(values, weights, 0.95))

            if paths:
                axis.imshow(
                    histogram,
                    origin="lower",
                    extent=(0.0, time_max, 0.0, radial_max),
                    aspect="auto",
                    cmap="magma",
                    norm=PowerNorm(
                        gamma=0.4,
                        vmin=0.0,
                        vmax=max(vmax, 1.0e-12),
                    ),
                    interpolation="nearest",
                )
                finite_median = np.asarray(
                    [math.nan if value is None else value for value in medians]
                )
                finite_p95 = np.asarray(
                    [math.nan if value is None else value for value in p95_values]
                )
                axis.plot(
                    time_grid,
                    finite_median,
                    color="cyan",
                    linewidth=1.8,
                    label="weighted median",
                )
                axis.plot(
                    time_grid,
                    finite_p95,
                    color="white",
                    linewidth=1.4,
                    linestyle="--",
                    label="weighted p95",
                )
                axis.legend(loc="upper left", fontsize="small")
            else:
                axis.text(
                    0.5,
                    0.5,
                    "no valid break holds",
                    ha="center",
                    va="center",
                    transform=axis.transAxes,
                )

            weights = [path.weight for path in paths]
            median_expected = _finite_quantile(
                [path.timing.expected_break_ms for path in paths],
                weights,
                0.5,
            )
            median_actual = _finite_quantile(
                [path.timing.actual_break_ms for path in paths],
                weights,
                0.5,
            )
            axis.set_xlim(0.0, time_max)
            axis.set_ylim(0.0, radial_max)
            axis.set_xlabel("elapsed break time [ms]")
            axis.set_ylabel("radial angular displacement [deg]")
            if median_expected is None or median_actual is None:
                axis.set_title(
                    f"{label} | n={len(paths)}, weight={sum(weights):.1f}"
                )
            else:
                axis.set_title(
                    f"{label} | n={len(paths)}, weight={sum(weights):.1f}\n"
                    f"median expected={median_expected:.1f} ms, "
                    f"actual={median_actual:.1f} ms"
                )
            panel_reports.append(
                {
                    "label": label,
                    "stratum": asdict(stratum),
                    "path_count": len(paths),
                    "path_weight": sum(weights),
                    "time_viewport_ms": time_max,
                    "radial_viewport_deg": radial_max,
                    "time_grid_ms": [float(value) for value in time_grid],
                    "active_event_count": active_counts,
                    "active_event_weight": active_weights,
                    "weighted_median_radial_deg": medians,
                    "weighted_p95_radial_deg": p95_values,
                    "directional_moments": _directional_moments(profiles),
                    "event_metric_distributions": _metric_summary(paths),
                }
            )
        axes[row][0].annotate(
            stratum.label,
            xy=(-0.2, 0.5),
            xycoords="axes fraction",
            rotation=90,
            va="center",
            ha="center",
            fontsize=11,
            fontweight="bold",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160)
    print(f"Wrote {output.resolve()}")
    if show:
        plt.show()
    plt.close(figure)
    return panel_reports


def main() -> None:
    args = parse_args()
    if args.histogram_bins <= 1 or args.time_samples <= 1:
        raise SystemExit("--histogram-bins and --time-samples must exceed one")
    if not 0.0 < args.time_quantile <= 1.0:
        raise SystemExit("--time-quantile must be in (0, 1]")
    if not 0.0 < args.displacement_quantile <= 1.0:
        raise SystemExit("--displacement-quantile must be in (0, 1]")
    if not math.isfinite(args.position_tolerance) or args.position_tolerance < 0.0:
        raise SystemExit("--position-tolerance must be finite and non-negative")
    if not (
        0.0 <= args.min_break_delay_ratio <= args.max_break_delay_ratio
        and math.isfinite(args.max_break_delay_ratio)
    ):
        raise SystemExit("break delay ratio bounds must be finite and ordered")
    try:
        tick_edges = parse_break_tick_edges(args.break_tick_edges)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    strata = expected_break_duration_strata(tick_edges)

    groups = resolve_dataset_groups(args.sessions, args.labels, args.dataset)
    datasets: list[tuple[str, tuple[BreakHoldPath, ...]]] = []
    dataset_reports: list[dict[str, object]] = []
    for group in groups:
        group_paths: list[BreakHoldPath] = []
        group_skipped: dict[str, int] = {}
        session_reports: list[dict[str, object]] = []
        input_events = 0
        for session_path in group.sessions:
            session = load_mining_session(session_path)
            paths, skipped = _paths_for_session(
                session,
                position_tolerance=args.position_tolerance,
                filter_break_delay=not args.no_break_delay_filter,
                minimum_break_delay_ratio=args.min_break_delay_ratio,
                maximum_break_delay_ratio=args.max_break_delay_ratio,
            )
            group_paths.extend(paths)
            input_events += len(session.events)
            for reason, count in skipped.items():
                group_skipped[reason] = group_skipped.get(reason, 0) + count
            session_reports.append(
                {
                    "session": str(session_path.resolve()),
                    "input_events": len(session.events),
                    "valid_break_holds": len(paths),
                    "skipped_reasons": skipped,
                }
            )
        if not group_paths:
            reasons = ", ".join(
                f"{name}={count}" for name, count in sorted(group_skipped.items())
            )
            raise SystemExit(
                f"{group.label}: no valid break holds ({reasons or 'no events'})"
            )
        resolved_paths = tuple(group_paths)
        datasets.append((group.label, resolved_paths))
        dataset_reports.append(
            {
                "label": group.label,
                "sessions": session_reports,
                "input_events": input_events,
                "valid_break_holds": len(resolved_paths),
                "valid_weight": sum(path.weight for path in resolved_paths),
                "skipped_reasons": group_skipped,
            }
        )
        print(
            f"{group.label}: {len(resolved_paths)} valid break holds from "
            f"{len(group.sessions)} session(s), {sum(group_skipped.values())} skipped"
        )

    panels = _plot(
        datasets,
        strata,
        args.output,
        histogram_bins=args.histogram_bins,
        time_samples=args.time_samples,
        time_quantile=args.time_quantile,
        displacement_quantile=args.displacement_quantile,
        show=args.show,
    )
    report = {
        "report_schema_version": 1,
        "plot": "break_hold_camera_stability",
        "time_axis": "elapsed_break_time_ms",
        "displacement": {
            "yaw_and_pitch": "cumulative signed raw-mouse angular deltas",
            "plotted_radial": "hypot(delta_yaw, delta_pitch)",
            "not_angular_path_length": True,
        },
        "stratification": "expected_break_ms",
        "strata": [asdict(stratum) for stratum in strata],
        "break_timing_filter": {
            "enabled": not args.no_break_delay_filter,
            "minimum_ratio": args.min_break_delay_ratio,
            "maximum_ratio": args.max_break_delay_ratio,
            "observed_tick_estimate": "actual_break_ms / 50 + 1",
        },
        "player_position_filter": {
            "comparison": "last state at/before start vs last state at/before end",
            "maximum_endpoint_displacement_blocks": args.position_tolerance,
            "intermediate_displacement_is_not_considered": True,
        },
        "density": {
            "conditional_per_time_column": True,
            "time_quantile": args.time_quantile,
            "displacement_quantile": args.displacement_quantile,
        },
        "datasets": dataset_reports,
        "panels": panels,
    }
    report_path = args.output.with_suffix(".json")
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {report_path.resolve()}")


if __name__ == "__main__":
    main()
