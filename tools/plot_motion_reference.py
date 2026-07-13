#!/usr/bin/env python3
"""Compare unit-independent path and speed shapes across motion domains."""

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
from analysis.path_density import align_paths, weighted_quantile
from analysis.reference_motion import (
    NormalizedMotionPath,
    load_reference_paths,
    resample_aligned_motion,
)
from tools.plot_path_density import MOUSE_PATH_RECONSTRUCTION, _records_for_session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare target-aligned path geometry and speed-profile shape "
            "without mixing degrees and pixels."
        )
    )
    add_dataset_arguments(parser)
    parser.add_argument("--reference-paths", action="append", type=Path)
    parser.add_argument("--reference-label", action="append")
    parser.add_argument(
        "--output", type=Path, default=Path("motion-reference.png")
    )
    parser.add_argument("--histogram-bins", type=int, default=90)
    parser.add_argument("--plot-quantile", type=float, default=0.95)
    parser.add_argument("--speed-quantile", type=float, default=0.99)
    parser.add_argument("--eye-height", type=float, default=1.62)
    parser.add_argument("--config", type=Path, help="Minescript-Miner aim config.")
    parser.add_argument("--no-segmentation", action="store_true")
    parser.add_argument("--max-idle-gap-ms", type=float, default=150.0)
    parser.add_argument("--minimum-motion-ratio", type=float, default=0.1)
    parser.add_argument("--max-player-displacement", type=float, default=0.05)
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _normalized_dataset(paths) -> tuple[tuple[NormalizedMotionPath, float], ...]:
    result: list[tuple[NormalizedMotionPath, float]] = []
    for path in paths:
        normalized = resample_aligned_motion(path.x, path.y, path.times_ms)
        if normalized is not None:
            result.append((normalized, path.weight))
    return tuple(result)


def _normalized_dataset_weights(dataset) -> list[float]:
    total = sum(weight for _, weight in dataset)
    return [weight / total for _, weight in dataset] if total > 0.0 else []


def _shared_limits(datasets, plot_quantile: float, speed_quantile: float):
    x_values: list[float] = []
    y_values: list[float] = []
    point_weights: list[float] = []
    speed_values: list[float] = []
    speed_weights: list[float] = []
    for dataset in datasets:
        path_weights = _normalized_dataset_weights(dataset)
        for (path, _), path_weight in zip(dataset, path_weights):
            point_weight = path_weight / len(path.x)
            x_values.extend(path.x)
            y_values.extend(path.y)
            point_weights.extend([point_weight] * len(path.x))
            speed_weight = path_weight / len(path.speed)
            speed_values.extend(path.speed)
            speed_weights.extend([speed_weight] * len(path.speed))
    tail = (1.0 - plot_quantile) / 2.0
    x_min = min(-0.1, weighted_quantile(x_values, point_weights, tail))
    x_max = max(1.1, weighted_quantile(x_values, point_weights, 1.0 - tail))
    y_abs = max(
        0.1,
        weighted_quantile(
            [abs(value) for value in y_values], point_weights, plot_quantile
        ),
    )
    speed_max = max(
        1.0,
        weighted_quantile(speed_values, speed_weights, speed_quantile),
    )
    return x_min, x_max, y_abs, speed_max


def _plot(datasets, output: Path, *, histogram_bins: int, plot_quantile: float,
          speed_quantile: float, show: bool) -> list[dict[str, object]]:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import PowerNorm

    x_min, x_max, y_abs, speed_max = _shared_limits(
        [dataset for _, dataset, _ in datasets], plot_quantile, speed_quantile
    )
    path_histograms = []
    speed_histograms = []
    for _, dataset, _ in datasets:
        path_weights = _normalized_dataset_weights(dataset)
        x = np.concatenate([np.asarray(path.x) for path, _ in dataset])
        y = np.concatenate([np.asarray(path.y) for path, _ in dataset])
        point_weights = np.concatenate(
            [
                np.full(len(path.x), weight / len(path.x))
                for (path, _), weight in zip(dataset, path_weights)
            ]
        )
        path_histogram, _, _ = np.histogram2d(
            x, y, bins=histogram_bins,
            range=((x_min, x_max), (-y_abs, y_abs)), weights=point_weights,
        )
        path_histograms.append(path_histogram)

        time_grid = np.linspace(0.0, 1.0, len(dataset[0][0].speed))
        times = np.tile(time_grid, len(dataset))
        speeds = np.concatenate([np.asarray(path.speed) for path, _ in dataset])
        sample_weights = np.concatenate(
            [
                np.full(len(path.speed), weight / len(path.speed))
                for (path, _), weight in zip(dataset, path_weights)
            ]
        )
        speed_histogram, _, _ = np.histogram2d(
            times, speeds, bins=histogram_bins,
            range=((0.0, 1.0), (0.0, speed_max)), weights=sample_weights,
        )
        speed_histograms.append(speed_histogram)

    path_vmax = max(float(histogram.max()) for histogram in path_histograms)
    speed_vmax = max(float(histogram.max()) for histogram in speed_histograms)
    figure, axes = plt.subplots(
        2, len(datasets), figsize=(6.1 * len(datasets), 9.2),
        squeeze=False, constrained_layout=True,
    )
    figure.suptitle(
        "Unit-independent motion reference\n"
        "path: start=(0,0), endpoint=(1,0); speed: divided by D / movement time"
    )
    reports: list[dict[str, object]] = []
    for column, ((label, dataset, metadata), path_histogram, speed_histogram) in enumerate(
        zip(datasets, path_histograms, speed_histograms)
    ):
        path_axis = axes[0][column]
        speed_axis = axes[1][column]
        path_axis.imshow(
            path_histogram.T, origin="lower",
            extent=(x_min, x_max, -y_abs, y_abs), aspect="auto", cmap="magma",
            norm=PowerNorm(gamma=0.4, vmin=0.0, vmax=path_vmax),
            interpolation="nearest",
        )
        weights = [weight for _, weight in dataset]
        total_weight = sum(weights)
        mean_x = np.average(
            np.asarray([path.x for path, _ in dataset]), axis=0, weights=weights
        )
        mean_y = np.average(
            np.asarray([path.y for path, _ in dataset]), axis=0, weights=weights
        )
        path_axis.plot(mean_x, mean_y, color="cyan", linewidth=1.8,
                       label="weighted mean path")
        path_axis.scatter([0.0, 1.0], [0.0, 0.0], c=["white", "lime"], s=22)
        path_axis.set_xlim(x_min, x_max)
        path_axis.set_ylim(-y_abs, y_abs)
        path_axis.set_title(f"{label} | n={len(dataset)}, weight={total_weight:.1f}")
        path_axis.set_xlabel("along movement / D")
        path_axis.set_ylabel("perpendicular / D")
        path_axis.legend(loc="upper left", fontsize="small")

        speed_axis.imshow(
            speed_histogram.T, origin="lower", extent=(0.0, 1.0, 0.0, speed_max),
            aspect="auto", cmap="magma",
            norm=PowerNorm(gamma=0.4, vmin=0.0, vmax=speed_vmax),
            interpolation="nearest",
        )
        speed_matrix = np.asarray([path.speed for path, _ in dataset])
        median_speed = [
            weighted_quantile(speed_matrix[:, index].tolist(), weights, 0.5)
            for index in range(speed_matrix.shape[1])
        ]
        speed_axis.plot(
            np.linspace(0.0, 1.0, len(median_speed)), median_speed,
            color="cyan", linewidth=1.8, label="weighted median speed",
        )
        speed_axis.set_xlim(0.0, 1.0)
        speed_axis.set_ylim(0.0, speed_max)
        speed_axis.set_xlabel("normalized movement time")
        speed_axis.set_ylabel("speed / (D / movement time)")
        speed_axis.legend(loc="upper right", fontsize="small")
        reports.append(
            {
                "label": label,
                "path_count": len(dataset),
                "path_weight": total_weight,
                "metadata": metadata,
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
    groups = resolve_dataset_groups(args.sessions, args.labels, args.dataset)
    reference_paths = args.reference_paths or []
    reference_labels = args.reference_label or []
    if len(reference_paths) != len(reference_labels):
        raise SystemExit("repeat --reference-label exactly once per reference")
    if args.histogram_bins <= 1:
        raise SystemExit("--histogram-bins must be greater than one")
    if not 0.0 < args.plot_quantile <= 1.0:
        raise SystemExit("--plot-quantile must be in (0, 1]")
    if not 0.0 < args.speed_quantile <= 1.0:
        raise SystemExit("--speed-quantile must be in (0, 1]")

    backend = MinescriptMinerBackend("sigmadrift", args.config)
    segmentation_config = None
    if not args.no_segmentation:
        segmentation_config = MovementSegmentationConfig(
            max_idle_gap_ms=args.max_idle_gap_ms,
            minimum_motion_ratio=args.minimum_motion_ratio,
            max_player_displacement=args.max_player_displacement,
        )
    datasets = []
    dataset_reports = []
    for group in groups:
        group_normalized: list[tuple[NormalizedMotionPath, float]] = []
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
            normalized = _normalized_dataset(aligned)
            session_skipped = dict(skipped)
            alignment_failures = len(records) - len(aligned)
            normalization_failures = len(aligned) - len(normalized)
            if alignment_failures:
                session_skipped["alignment_failed"] = alignment_failures
            if normalization_failures:
                session_skipped["normalization_failed"] = normalization_failures
            for reason, count in session_skipped.items():
                group_skipped[reason] = group_skipped.get(reason, 0) + count
            group_normalized.extend(normalized)
            input_events += len(session.events)
            session_reports.append(
                {
                    "session": str(path.resolve()),
                    "input_events": len(session.events),
                    "path_count": len(normalized),
                    "path_weight": sum(weight for _, weight in normalized),
                    "skipped_reasons": session_skipped,
                }
            )
        if not group_normalized:
            raise SystemExit(f"{group.label}: no valid normalized paths")
        normalized_group = tuple(group_normalized)
        metadata = {"sessions": session_reports, "skipped_reasons": group_skipped}
        datasets.append((group.label, normalized_group, metadata))
        dataset_reports.append(
            {
                "label": group.label,
                "sessions": session_reports,
                "input_events": input_events,
                "path_count": len(normalized_group),
                "path_weight": sum(weight for _, weight in normalized_group),
                "skipped_reasons": group_skipped,
            }
        )
        print(
            f"{group.label}: {len(normalized_group)} normalized paths from "
            f"{len(group.sessions)} session(s)"
        )
    reference_weight = sum(weight for _, weight in datasets[0][1])
    for label, path in zip(reference_labels, reference_paths):
        paths, metadata = load_reference_paths(path)
        if not paths:
            raise SystemExit(f"{path}: no reference paths")
        path_weight = reference_weight / len(paths)
        datasets.append(
            (label, tuple((item, path_weight) for item in paths), metadata)
        )
        dataset_reports.append(
            {"label": label, "source": str(path.resolve()), "path_count": len(paths)}
        )
        print(f"{label}: {len(paths)} normalized reference paths")

    panels = _plot(
        datasets, args.output, histogram_bins=args.histogram_bins,
        plot_quantile=args.plot_quantile, speed_quantile=args.speed_quantile,
        show=args.show,
    )
    report = {
        "report_schema_version": 1,
        "plot": "unit_independent_motion_reference",
        "target_width_stratification": None,
        "path_normalization": "start=(0,0), endpoint_or_target=(1,0)",
        "speed_normalization": "speed / (D / movement_time)",
        "plot_quantile": args.plot_quantile,
        "speed_quantile": args.speed_quantile,
        "movement_segmentation": {
            "enabled": segmentation_config is not None,
            "config": asdict(segmentation_config) if segmentation_config else None,
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
