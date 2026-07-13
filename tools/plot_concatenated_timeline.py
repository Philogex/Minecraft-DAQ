#!/usr/bin/env python3
"""Plot segmented trajectories concatenated on a continuous movement-time axis."""

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
from analysis.path_density import AlignedPath, align_paths, weighted_quantile
from tools.plot_path_density import MOUSE_PATH_RECONSTRUCTION, _records_for_session
from tools.plot_speed_density import _speed_profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Concatenate valid movement episodes and plot angular speed plus "
            "remaining target delta over cumulative active movement time."
        )
    )
    add_dataset_arguments(parser)
    parser.add_argument(
        "--output", type=Path, default=Path("concatenated-timeline.png")
    )
    parser.add_argument("--speed-quantile", type=float, default=0.995)
    parser.add_argument("--eye-height", type=float, default=1.62)
    parser.add_argument("--config", type=Path, help="Minescript-Miner aim config.")
    parser.add_argument("--no-segmentation", action="store_true")
    parser.add_argument("--max-idle-gap-ms", type=float, default=150.0)
    parser.add_argument("--minimum-motion-ratio", type=float, default=0.1)
    parser.add_argument("--max-player-displacement", type=float, default=0.05)
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _concatenate(paths: tuple[AlignedPath, ...]) -> dict[str, object]:
    speed_times: list[float] = []
    speeds: list[float] = []
    delta_times: list[float] = []
    deltas: list[float] = []
    boundaries: list[float] = []
    elapsed_ms = 0.0
    for path in paths:
        start_ms = path.times_ms[0]
        relative_times = [time - start_ms for time in path.times_ms]
        delta_times.extend(elapsed_ms + time for time in relative_times)
        deltas.extend(
            math.hypot(1.0 - x, y) for x, y in zip(path.x, path.y)
        )
        progress, path_speeds = _speed_profile(path)
        duration_ms = relative_times[-1]
        speed_times.extend(elapsed_ms + value * duration_ms for value in progress)
        speeds.extend(path_speeds)
        elapsed_ms += duration_ms
        boundaries.append(elapsed_ms)
        delta_times.append(math.nan)
        deltas.append(math.nan)
        speed_times.append(math.nan)
        speeds.append(math.nan)
    return {
        "speed_times_ms": speed_times,
        "speeds_deg_s": speeds,
        "delta_times_ms": delta_times,
        "remaining_delta_over_d": deltas,
        "boundaries_ms": boundaries,
        "duration_ms": elapsed_ms,
    }


def _finite(values: list[float]) -> list[float]:
    return [value for value in values if math.isfinite(value)]


def _plot(
    datasets: list[tuple[str, tuple[AlignedPath, ...]]],
    output: Path,
    *,
    speed_quantile: float,
    show: bool,
) -> list[dict[str, object]]:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    concatenated = [_concatenate(paths) for _, paths in datasets]
    pooled_speeds: list[float] = []
    pooled_speed_weights: list[float] = []
    pooled_deltas: list[float] = []
    pooled_delta_weights: list[float] = []
    for _, paths in datasets:
        for path in paths:
            _, path_speeds = _speed_profile(path)
            if path_speeds:
                pooled_speeds.extend(path_speeds)
                pooled_speed_weights.extend(
                    [path.weight / len(path_speeds)] * len(path_speeds)
                )
            path_deltas = [
                math.hypot(1.0 - x, y) for x, y in zip(path.x, path.y)
            ]
            pooled_deltas.extend(path_deltas)
            pooled_delta_weights.extend(
                [path.weight / len(path_deltas)] * len(path_deltas)
            )
    speed_max = (
        weighted_quantile(
            pooled_speeds,
            pooled_speed_weights,
            speed_quantile,
        )
        if pooled_speeds
        else 1.0
    )
    delta_max = (
        max(
            1.0,
            weighted_quantile(
                pooled_deltas,
                pooled_delta_weights,
                0.995,
            ),
        )
        if pooled_deltas
        else 1.0
    )

    figure, axes = plt.subplots(
        len(datasets),
        2,
        figsize=(18.0, 4.2 * len(datasets)),
        squeeze=False,
        constrained_layout=True,
    )
    figure.suptitle(
        "Concatenated valid movement episodes\n"
        "real active time only; line breaks mark event boundaries"
    )
    reports: list[dict[str, object]] = []
    for row, ((label, paths), values) in enumerate(zip(datasets, concatenated)):
        speed_axis = axes[row][0]
        delta_axis = axes[row][1]
        speed_times = [value / 1000.0 for value in values["speed_times_ms"]]
        delta_times = [value / 1000.0 for value in values["delta_times_ms"]]
        boundaries = [value / 1000.0 for value in values["boundaries_ms"]]
        duration_s = values["duration_ms"] / 1000.0

        speed_axis.plot(
            speed_times,
            values["speeds_deg_s"],
            color="tab:orange",
            linewidth=0.55,
            rasterized=True,
        )
        delta_axis.plot(
            delta_times,
            values["remaining_delta_over_d"],
            color="tab:blue",
            linewidth=0.55,
            rasterized=True,
        )
        # A sparse subset stays legible for long recordings without becoming a grid.
        boundary_stride = max(1, math.ceil(len(boundaries) / 250))
        for boundary in boundaries[::boundary_stride]:
            speed_axis.axvline(
                boundary, color="black", alpha=0.055, linewidth=0.45
            )
            delta_axis.axvline(
                boundary, color="black", alpha=0.055, linewidth=0.45
            )

        speed_axis.set_xlim(0.0, max(duration_s, 1e-9))
        speed_axis.set_ylim(0.0, max(speed_max, 1.0))
        speed_axis.set_title(f"{label}: angular speed | {len(paths)} paths")
        speed_axis.set_xlabel("concatenated active movement time [s]")
        speed_axis.set_ylabel("angular speed [deg/s]")
        speed_axis.grid(True, axis="y", alpha=0.2)

        delta_axis.set_xlim(0.0, max(duration_s, 1e-9))
        delta_axis.set_ylim(0.0, delta_max)
        delta_axis.set_title(f"{label}: remaining target delta")
        delta_axis.set_xlabel("concatenated active movement time [s]")
        delta_axis.set_ylabel("remaining angular delta / D")
        delta_axis.grid(True, axis="y", alpha=0.2)

        finite_speeds = _finite(values["speeds_deg_s"])
        visible_speed_weight = 0.0
        total_speed_weight = 0.0
        for path in paths:
            _, path_speeds = _speed_profile(path)
            if not path_speeds:
                continue
            sample_weight = path.weight / len(path_speeds)
            total_speed_weight += path.weight
            visible_speed_weight += sample_weight * sum(
                speed <= speed_max for speed in path_speeds
            )
        reports.append(
            {
                "label": label,
                "path_count": len(paths),
                "path_weight": sum(path.weight for path in paths),
                "concatenated_duration_s": duration_s,
                "orientation_sample_count": sum(len(path.x) for path in paths),
                "speed_sample_count": len(finite_speeds),
                "median_path_duration_ms": weighted_quantile(
                    [path.times_ms[-1] - path.times_ms[0] for path in paths],
                    [path.weight for path in paths],
                    0.5,
                ),
                "speed_viewport_max_deg_s": speed_max,
                "speed_weight_fraction_in_viewport": (
                    visible_speed_weight / total_speed_weight
                    if total_speed_weight > 0.0
                    else None
                ),
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160)
    print(f"Wrote {output.resolve()}")
    if show:
        plt.show()
    plt.close(figure)
    return reports


def main() -> None:
    args = parse_args()
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

    panels = _plot(
        datasets,
        args.output,
        speed_quantile=args.speed_quantile,
        show=args.show,
    )
    report = {
        "report_schema_version": 1,
        "plot": "concatenated_movement_timeline",
        "time_axis": "cumulative_segmented_active_movement_time",
        "event_gaps_removed": True,
        "speed_quantile": args.speed_quantile,
        "movement_segmentation": {
            "enabled": segmentation_config is not None,
            "config": asdict(segmentation_config) if segmentation_config else None,
            "generated_sessions_are_not_resegmented": True,
        },
        "human_trajectory_reconstruction": MOUSE_PATH_RECONSTRUCTION,
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
