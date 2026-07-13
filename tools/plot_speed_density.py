#!/usr/bin/env python3
"""Compare angular-speed densities over normalized movement time."""

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

from analysis.minescript_miner_backend import MinescriptMinerBackend
from analysis.dataset_groups import add_dataset_arguments, resolve_dataset_groups
from analysis.mining_session import load_mining_session
from analysis.movement_segmentation import MovementSegmentationConfig
from analysis.path_density import (
    AlignedPath,
    align_paths,
    paths_in_bin,
    quantile_edges,
    weighted_quantile,
)
from tools.plot_path_density import (
    MOUSE_PATH_RECONSTRUCTION,
    _parse_edges,
    _records_for_session,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot angular-speed densities over normalized movement time for "
            "DAQ and generated sessions, stratified by effective target width."
        )
    )
    add_dataset_arguments(parser)
    parser.add_argument("--output", type=Path, default=Path("speed-density.png"))
    parser.add_argument("--strata", type=int, default=3)
    parser.add_argument("--width-edges")
    parser.add_argument("--histogram-bins", type=int, default=100)
    parser.add_argument("--time-samples", type=int, default=101)
    parser.add_argument(
        "--speed-quantile",
        type=float,
        default=0.99,
        help="Weighted speed quantile used as the shared y limit (default: 0.99).",
    )
    parser.add_argument("--eye-height", type=float, default=1.62)
    parser.add_argument("--config", type=Path, help="Minescript-Miner aim config.")
    parser.add_argument("--no-segmentation", action="store_true")
    parser.add_argument("--max-idle-gap-ms", type=float, default=150.0)
    parser.add_argument("--minimum-motion-ratio", type=float, default=0.1)
    parser.add_argument("--max-player-displacement", type=float, default=0.05)
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _speed_profile(path: AlignedPath) -> tuple[tuple[float, ...], tuple[float, ...]]:
    progress: list[float] = []
    speeds: list[float] = []
    for index in range(1, len(path.times_ms)):
        dt_s = (path.times_ms[index] - path.times_ms[index - 1]) / 1000.0
        if dt_s <= 0.0:
            continue
        dx = (path.x[index] - path.x[index - 1]) * path.distance
        dy = (path.y[index] - path.y[index - 1]) * path.distance
        progress.append((path.progress[index] + path.progress[index - 1]) / 2.0)
        speeds.append(math.hypot(dx, dy) / dt_s)
    return tuple(progress), tuple(speeds)


def _resampled_speeds(
    paths: tuple[AlignedPath, ...],
    time_grid,
):
    import numpy as np

    profiles: list[tuple[AlignedPath, object]] = []
    for path in paths:
        progress, speeds = _speed_profile(path)
        if not speeds:
            continue
        profiles.append(
            (
                path,
                np.interp(
                    time_grid,
                    np.asarray(progress),
                    np.asarray(speeds),
                ),
            )
        )
    return profiles


def _plot(
    datasets: list[tuple[str, tuple[AlignedPath, ...]]],
    edges: tuple[float, ...],
    output: Path,
    *,
    histogram_bins: int,
    time_samples: int,
    speed_quantile: float,
    show: bool,
) -> list[dict[str, object]]:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import PowerNorm

    row_count = len(edges) - 1
    column_count = len(datasets)
    figure, axes = plt.subplots(
        row_count,
        column_count,
        figsize=(6.2 * column_count, 4.8 * row_count),
        squeeze=False,
        constrained_layout=True,
    )
    figure.suptitle(
        "Angular-speed density by effective angular width\n"
        "time normalized per movement; speed retained in deg/s"
    )
    time_grid = np.linspace(0.0, 1.0, time_samples)
    panel_reports: list[dict[str, object]] = []

    for row in range(row_count):
        lower = edges[row]
        upper = edges[row + 1]
        binned = [
            paths_in_bin(paths, lower, upper, include_upper=row == row_count - 1)
            for _, paths in datasets
        ]
        profiles_by_dataset = [
            _resampled_speeds(paths, time_grid) for paths in binned
        ]
        pooled_values = [
            float(speed)
            for profiles in profiles_by_dataset
            for path, speeds in profiles
            for speed in speeds
        ]
        pooled_weights = [
            path.weight / time_samples
            for profiles in profiles_by_dataset
            for path, speeds in profiles
            for _ in speeds
        ]
        speed_max = (
            max(1.0, weighted_quantile(pooled_values, pooled_weights, speed_quantile))
            if pooled_values
            else 1.0
        )

        histograms = []
        for profiles in profiles_by_dataset:
            if not profiles:
                histograms.append(None)
                continue
            times = np.tile(time_grid, len(profiles))
            speeds = np.concatenate([values for _, values in profiles])
            weights = np.concatenate(
                [
                    np.full(time_samples, path.weight / time_samples)
                    for path, _ in profiles
                ]
            )
            histogram, _, _ = np.histogram2d(
                times,
                speeds,
                bins=(histogram_bins, histogram_bins),
                range=((0.0, 1.0), (0.0, speed_max)),
                weights=weights,
            )
            total = histogram.sum()
            histograms.append(histogram / total if total > 0.0 else histogram)
        vmax = max(
            (float(histogram.max()) for histogram in histograms if histogram is not None),
            default=1.0,
        )

        for column, ((label, _), paths, profiles, histogram) in enumerate(
            zip(datasets, binned, profiles_by_dataset, histograms)
        ):
            axis = axes[row][column]
            if histogram is None:
                axis.text(
                    0.5,
                    0.5,
                    "no paths",
                    ha="center",
                    va="center",
                    transform=axis.transAxes,
                )
                median_speeds = np.full(time_samples, np.nan)
                in_viewport = math.nan
            else:
                axis.imshow(
                    histogram.T,
                    origin="lower",
                    extent=(0.0, 1.0, 0.0, speed_max),
                    aspect="auto",
                    cmap="magma",
                    norm=PowerNorm(gamma=0.4, vmin=0.0, vmax=vmax),
                    interpolation="nearest",
                )
                median_speeds = np.asarray(
                    [
                        weighted_quantile(
                            [float(speeds[index]) for _, speeds in profiles],
                            [path.weight for path, _ in profiles],
                            0.5,
                        )
                        for index in range(time_samples)
                    ]
                )
                axis.plot(
                    time_grid,
                    median_speeds,
                    color="cyan",
                    linewidth=1.8,
                    label="weighted median speed",
                )
                visible_weight = sum(
                    path.weight
                    * sum(float(speed) <= speed_max for speed in speeds)
                    / len(speeds)
                    for path, speeds in profiles
                )
                total_weight = sum(path.weight for path, _ in profiles)
                in_viewport = visible_weight / total_weight

            path_weights = [path.weight for path in paths]
            median_width = (
                weighted_quantile(
                    [path.effective_width for path in paths], path_weights, 0.5
                )
                if paths
                else math.nan
            )
            axis.set_xlim(0.0, 1.0)
            axis.set_ylim(0.0, speed_max)
            axis.set_xlabel("normalized movement time")
            axis.set_ylabel("angular speed [deg/s]")
            axis.set_title(
                f"{label} | n={len(paths)}, weight={sum(path_weights):.1f}\n"
                f"median W_eff={median_width:.3f} deg, "
                f"in viewport={in_viewport:.1%}"
            )
            if histogram is not None:
                axis.legend(loc="upper right", fontsize="small")
            panel_reports.append(
                {
                    "label": label,
                    "width_lower_deg": lower,
                    "width_upper_deg": upper,
                    "path_count": len(paths),
                    "path_weight": sum(path_weights),
                    "speed_viewport_max_deg_s": speed_max,
                    "point_weight_in_viewport": in_viewport,
                    "median_speed_deg_s": [
                        None if not math.isfinite(float(value)) else float(value)
                        for value in median_speeds
                    ],
                }
            )
        axes[row][0].annotate(
            f"W_eff [{lower:.3f}, {upper:.3f}{']' if row == row_count - 1 else ')'} deg",
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
    if args.strata <= 0 or args.histogram_bins <= 1 or args.time_samples <= 1:
        raise SystemExit("--strata, --histogram-bins, and --time-samples must be positive")
    if not 0.0 < args.speed_quantile <= 1.0:
        raise SystemExit("--speed-quantile must be in (0, 1]")

    groups = resolve_dataset_groups(args.sessions, args.labels, args.dataset)
    backend = MinescriptMinerBackend("sigmadrift", args.config)
    segmentation_config = None
    if not args.no_segmentation:
        segmentation_config = MovementSegmentationConfig(
            max_idle_gap_ms=args.max_idle_gap_ms,
            minimum_motion_ratio=args.minimum_motion_ratio,
            max_player_displacement=args.max_player_displacement,
        )

    datasets: list[tuple[str, tuple[AlignedPath, ...]]] = []
    dataset_reports: list[dict[str, object]] = []
    for group in groups:
        group_aligned: list[AlignedPath] = []
        group_skipped: dict[str, int] = {}
        session_reports: list[dict[str, object]] = []
        input_events = 0
        for path in group.sessions:
            session = load_mining_session(path)
            records, skipped = _records_for_session(
                session,
                backend,
                eye_height=args.eye_height,
                segmentation_config=segmentation_config,
            )
            aligned = align_paths(records)
            alignment_failures = len(records) - len(aligned)
            session_skipped = dict(skipped)
            if alignment_failures:
                session_skipped["alignment_failed"] = alignment_failures
            for reason, count in session_skipped.items():
                group_skipped[reason] = group_skipped.get(reason, 0) + count
            group_aligned.extend(aligned)
            input_events += len(session.events)
            session_reports.append(
                {
                    "session": str(path.resolve()),
                    "input_events": len(session.events),
                    "valid_paths": len(aligned),
                    "valid_weight": sum(item.weight for item in aligned),
                    "skipped_reasons": session_skipped,
                }
            )
        if not group_aligned:
            raise SystemExit(f"{group.label}: no valid paths")
        aligned_group = tuple(group_aligned)
        datasets.append((group.label, aligned_group))
        dataset_reports.append(
            {
                "label": group.label,
                "session": (
                    str(group.sessions[0].resolve())
                    if len(group.sessions) == 1
                    else None
                ),
                "sessions": session_reports,
                "input_events": input_events,
                "valid_paths": len(aligned_group),
                "valid_weight": sum(item.weight for item in aligned_group),
                "skipped_reasons": group_skipped,
            }
        )
        print(
            f"{group.label}: {len(aligned_group)} valid paths from "
            f"{len(group.sessions)} session(s), {sum(group_skipped.values())} skipped"
        )
        if group_skipped:
            print(
                "  skip reasons: "
                + ", ".join(
                    f"{key}={value}" for key, value in sorted(group_skipped.items())
                )
            )

    edges = (
        _parse_edges(args.width_edges)
        if args.width_edges
        else quantile_edges(datasets[0][1], args.strata)
    )
    print("W_eff edges [deg]: " + ", ".join(f"{edge:.6f}" for edge in edges))
    panels = _plot(
        datasets,
        edges,
        args.output,
        histogram_bins=args.histogram_bins,
        time_samples=args.time_samples,
        speed_quantile=args.speed_quantile,
        show=args.show,
    )

    report_path = args.output.with_suffix(".json")
    report = {
        "report_schema_version": 1,
        "plot": "angular_speed_density",
        "time_axis": "normalized_movement_time",
        "speed_unit": "deg/s",
        "effective_width_edges_deg": edges,
        "movement_segmentation": {
            "enabled": segmentation_config is not None,
            "config": asdict(segmentation_config) if segmentation_config else None,
            "generated_sessions_are_not_resegmented": True,
        },
        "speed_quantile": args.speed_quantile,
        "time_samples": args.time_samples,
        "human_trajectory_reconstruction": MOUSE_PATH_RECONSTRUCTION,
        "datasets": dataset_reports,
        "panels": panels,
    }
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {report_path.resolve()}")


if __name__ == "__main__":
    main()
