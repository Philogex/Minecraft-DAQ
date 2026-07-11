#!/usr/bin/env python3
"""Compare target-relative path densities in effective-width strata."""

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

from analysis.aim_features import AimPoint
from analysis.minescript_miner_backend import GenerationCaseError, MinescriptMinerBackend
from analysis.mining_session import MiningSession, RecordedMiningEvent, load_mining_session
from analysis.movement_segmentation import (
    MovementSegmentationConfig,
    segment_target_movement,
)
from analysis.path_density import (
    AlignedPath,
    AngularTarget,
    PathDensityRecord,
    align_paths,
    paths_in_bin,
    quantile_edges,
    weighted_quantile,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot target-relative path densities for DAQ and generated sessions, "
            "stratified by effective angular target width."
        )
    )
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument(
        "--label",
        action="append",
        dest="labels",
        help="Dataset label; repeat once per session.",
    )
    parser.add_argument("--output", type=Path, default=Path("path-density.png"))
    parser.add_argument("--strata", type=int, default=3)
    parser.add_argument(
        "--width-edges",
        help="Comma-separated W_eff edges in degrees; overrides --strata.",
    )
    parser.add_argument("--histogram-bins", type=int, default=90)
    parser.add_argument("--mean-samples", type=int, default=101)
    parser.add_argument(
        "--plot-quantile",
        type=float,
        default=0.95,
        help=(
            "Central weighted point mass used for shared plot limits "
            "(default: 0.95). This clips only the viewport, not paths or weights."
        ),
    )
    parser.add_argument("--eye-height", type=float, default=1.62)
    parser.add_argument("--config", type=Path, help="Minescript-Miner aim config.")
    parser.add_argument(
        "--no-segmentation",
        action="store_true",
        help="Plot complete recorded windows instead of detected movement episodes.",
    )
    parser.add_argument("--max-idle-gap-ms", type=float, default=150.0)
    parser.add_argument("--minimum-motion-ratio", type=float, default=0.1)
    parser.add_argument("--max-player-displacement", type=float, default=0.05)
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _generated_metadata_by_event(session: MiningSession) -> dict[int, dict[str, object]]:
    raw_events = session.metadata.get("events", [])
    if not isinstance(raw_events, list):
        return {}
    result: dict[int, dict[str, object]] = {}
    for item in raw_events:
        if isinstance(item, dict) and isinstance(item.get("generated_event_id"), int):
            result[int(item["generated_event_id"])] = item
    return result


def _record_from_generated(
    recorded: RecordedMiningEvent,
    metadata: dict[str, object],
) -> PathDensityRecord | None:
    target = metadata.get("target_condition")
    if not isinstance(target, dict):
        return None
    try:
        angular_target = AngularTarget(
            yaw=float(target["yaw"]),
            pitch=float(target["pitch"]),
            width_yaw=float(target["width_yaw"]),
            width_pitch=float(target["width_pitch"]),
        )
        weight = float(metadata.get("analysis_weight", 1.0))
    except (KeyError, TypeError, ValueError):
        return None
    return PathDensityRecord(
        event_id=recorded.event.event_id,
        points=tuple(
            AimPoint(sample.yaw, sample.pitch, sample.relative_ms)
            for sample in recorded.state_samples
        ),
        target=angular_target,
        weight=weight,
    )


def _records_for_session(
    session: MiningSession,
    backend: MinescriptMinerBackend,
    *,
    eye_height: float,
    segmentation_config: MovementSegmentationConfig | None = None,
) -> tuple[tuple[PathDensityRecord, ...], dict[str, int]]:
    generated_metadata = _generated_metadata_by_event(session)
    is_generated = bool(generated_metadata)
    records: list[PathDensityRecord] = []
    skipped: dict[str, int] = {}
    for recorded in session.events:
        if generated_metadata:
            record = _record_from_generated(
                recorded,
                generated_metadata.get(recorded.event.event_id, {}),
            )
            if record is None:
                reason = "invalid_generated_metadata"
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
        else:
            try:
                case = backend.prepare_case(
                    session.session_id,
                    recorded,
                    eye_height=eye_height,
                )
            except GenerationCaseError as error:
                skipped[error.reason] = skipped.get(error.reason, 0) + 1
                continue
            record = PathDensityRecord(
                event_id=recorded.event.event_id,
                points=tuple(
                    AimPoint(sample.yaw, sample.pitch, sample.relative_ms)
                    for sample in recorded.state_samples
                ),
                target=AngularTarget(
                    yaw=case.target.yaw,
                    pitch=case.target.pitch,
                    width_yaw=case.target.width_yaw,
                    width_pitch=case.target.width_pitch,
                ),
            )
        if segmentation_config is not None and not is_generated:
            result = segment_target_movement(
                record.points,
                record.target,
                angular_step_deg=backend.angular_step_deg(
                    recorded.state_samples[0].sensitivity
                ),
                player_positions=tuple(
                    (sample.player_x, sample.player_y, sample.player_z)
                    for sample in recorded.state_samples
                ),
                config=segmentation_config,
            )
            if result.segment is None:
                reason = result.reason or "unknown_segmentation_failure"
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            try:
                refined_case = backend.prepare_case(
                    session.session_id,
                    recorded,
                    eye_height=eye_height,
                    start_sample=recorded.state_samples[result.segment.start_index],
                    start_source="detected_movement_onset",
                )
            except GenerationCaseError as error:
                skipped[error.reason] = skipped.get(error.reason, 0) + 1
                continue
            record = PathDensityRecord(
                event_id=record.event_id,
                points=_reconstruct_mouse_path(
                    recorded,
                    result.segment.points,
                    backend.angular_step_deg(
                        recorded.state_samples[0].sensitivity
                    ),
                ),
                target=AngularTarget(
                    yaw=refined_case.target.yaw,
                    pitch=refined_case.target.pitch,
                    width_yaw=refined_case.target.width_yaw,
                    width_pitch=refined_case.target.width_pitch,
                ),
                weight=record.weight,
            )
        records.append(record)
    return tuple(records), skipped


def _reconstruct_mouse_path(
    recorded: RecordedMiningEvent,
    state_segment: tuple[AimPoint, ...],
    angular_step_deg: float,
) -> tuple[AimPoint, ...]:
    """Reconstruct a high-rate orientation path inside a tick-detected episode."""

    start = state_segment[0]
    end = state_segment[-1]
    samples = tuple(
        sample
        for sample in recorded.mouse_samples
        if start.t_ms < sample.relative_ms <= end.t_ms
    )
    if not samples:
        return state_segment
    points = [start]
    yaw = start.yaw
    pitch = start.pitch
    for sample in samples:
        yaw += sample.mouse_dx * angular_step_deg
        pitch += sample.mouse_dy * angular_step_deg
        points.append(AimPoint(yaw, pitch, sample.relative_ms))
    return tuple(points)


def _parse_edges(text: str) -> tuple[float, ...]:
    try:
        edges = tuple(float(value.strip()) for value in text.split(","))
    except ValueError as error:
        raise SystemExit("--width-edges must contain comma-separated numbers") from error
    if len(edges) < 2 or any(not math.isfinite(edge) for edge in edges):
        raise SystemExit("--width-edges requires at least two finite values")
    if any(current <= previous for previous, current in zip(edges, edges[1:])):
        raise SystemExit("--width-edges must be strictly increasing")
    return edges


def _weighted_percentile(values: list[float], weights: list[float], value: float) -> float:
    return weighted_quantile(values, weights, value)


def _plot(
    datasets: list[tuple[str, tuple[AlignedPath, ...]]],
    edges: tuple[float, ...],
    output: Path,
    *,
    histogram_bins: int,
    mean_samples: int,
    plot_quantile: float,
    show: bool,
) -> None:
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
        "Target-relative path density by effective angular width\n"
        "start=(0, 0), target=(1, 0), coordinates normalized by angular distance"
    )
    progress_grid = np.linspace(0.0, 1.0, mean_samples)

    for row in range(row_count):
        lower = edges[row]
        upper = edges[row + 1]
        binned = [
            paths_in_bin(paths, lower, upper, include_upper=row == row_count - 1)
            for _, paths in datasets
        ]
        all_row_paths = tuple(path for paths in binned for path in paths)
        if all_row_paths:
            all_x = [value for path in all_row_paths for value in path.x]
            all_y = [value for path in all_row_paths for value in path.y]
            x_weights = [path.weight / len(path.x) for path in all_row_paths for _ in path.x]
            y_weights = [path.weight / len(path.y) for path in all_row_paths for _ in path.y]
            tail = (1.0 - plot_quantile) / 2.0
            x_min = min(-0.1, _weighted_percentile(all_x, x_weights, tail))
            x_max = max(1.1, _weighted_percentile(all_x, x_weights, 1.0 - tail))
            y_abs = max(
                0.1,
                _weighted_percentile(
                    [abs(value) for value in all_y], y_weights, plot_quantile
                ),
            )
        else:
            x_min, x_max, y_abs = -0.1, 1.1, 0.5
        extent = (x_min, x_max, -y_abs, y_abs)

        histograms = []
        for paths in binned:
            if not paths:
                histograms.append(None)
                continue
            x = np.concatenate([np.asarray(path.x) for path in paths])
            y = np.concatenate([np.asarray(path.y) for path in paths])
            weights = np.concatenate(
                [np.full(len(path.x), path.weight / len(path.x)) for path in paths]
            )
            histogram, _, _ = np.histogram2d(
                x,
                y,
                bins=histogram_bins,
                range=((x_min, x_max), (-y_abs, y_abs)),
                weights=weights,
            )
            total = histogram.sum()
            histograms.append(histogram / total if total > 0.0 else histogram)
        vmax = max(
            (float(histogram.max()) for histogram in histograms if histogram is not None),
            default=1.0,
        )

        for column, ((label, _), paths, histogram) in enumerate(
            zip(datasets, binned, histograms)
        ):
            axis = axes[row][column]
            if histogram is None:
                axis.text(0.5, 0.5, "no paths", ha="center", va="center", transform=axis.transAxes)
            else:
                axis.imshow(
                    histogram.T,
                    origin="lower",
                    extent=extent,
                    aspect="auto",
                    cmap="magma",
                    norm=PowerNorm(gamma=0.4, vmin=0.0, vmax=vmax),
                    interpolation="nearest",
                )
                mean_x = np.zeros_like(progress_grid)
                mean_y = np.zeros_like(progress_grid)
                total_weight = 0.0
                for path in paths:
                    mean_x += path.weight * np.interp(progress_grid, path.progress, path.x)
                    mean_y += path.weight * np.interp(progress_grid, path.progress, path.y)
                    total_weight += path.weight
                if total_weight > 0.0:
                    axis.plot(
                        mean_x / total_weight,
                        mean_y / total_weight,
                        color="cyan",
                        linewidth=1.8,
                        label="weighted mean path",
                    )
            visible_weight = sum(
                path.weight
                * sum(
                    x_min <= x <= x_max and -y_abs <= y <= y_abs
                    for x, y in zip(path.x, path.y)
                )
                / len(path.x)
                for path in paths
            )
            total_point_weight = sum(path.weight for path in paths)
            visible_ratio = (
                visible_weight / total_point_weight if total_point_weight > 0.0 else math.nan
            )
            axis.scatter([0.0, 1.0], [0.0, 0.0], c=["white", "lime"], s=22, zorder=4)
            axis.axhline(0.0, color="white", alpha=0.2, linewidth=0.7)
            axis.set_xlim(x_min, x_max)
            axis.set_ylim(-y_abs, y_abs)
            axis.set_xlabel("along movement / D")
            axis.set_ylabel("perpendicular / D")
            width_values = [path.effective_width for path in paths]
            path_weights = [path.weight for path in paths]
            median_width = (
                weighted_quantile(width_values, path_weights, 0.5) if paths else math.nan
            )
            median_id = (
                weighted_quantile(
                    [path.fitts_id for path in paths], path_weights, 0.5
                )
                if paths
                else math.nan
            )
            median_width_yaw = (
                weighted_quantile(
                    [path.width_yaw for path in paths], path_weights, 0.5
                )
                if paths
                else math.nan
            )
            median_width_pitch = (
                weighted_quantile(
                    [path.width_pitch for path in paths], path_weights, 0.5
                )
                if paths
                else math.nan
            )
            axis.set_title(
                f"{label} | n={len(paths)}, weight={sum(path_weights):.1f}\n"
                f"median W_eff={median_width:.3f} deg, median ID={median_id:.3f}, "
                f"in viewport={visible_ratio:.1%}\n"
                f"median widths=(yaw {median_width_yaw:.3f}, "
                f"pitch {median_width_pitch:.3f}) deg"
            )
            if histogram is not None:
                axis.legend(loc="upper left", fontsize="small")
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


def main() -> None:
    args = parse_args()
    if args.labels is not None and len(args.labels) != len(args.sessions):
        raise SystemExit("repeat --label exactly once per session")
    if args.strata <= 0 or args.histogram_bins <= 1 or args.mean_samples <= 1:
        raise SystemExit("--strata, --histogram-bins, and --mean-samples must be positive")
    if not 0.0 < args.plot_quantile <= 1.0:
        raise SystemExit("--plot-quantile must be in (0, 1]")

    labels = args.labels or [path.name for path in args.sessions]
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
    for label, path in zip(labels, args.sessions):
        session = load_mining_session(path)
        records, skipped = _records_for_session(
            session,
            backend,
            eye_height=args.eye_height,
            segmentation_config=segmentation_config,
        )
        aligned = align_paths(records)
        if not aligned:
            raise SystemExit(f"{path}: no valid paths")
        datasets.append((label, aligned))
        skipped_count = sum(skipped.values()) + len(records) - len(aligned)
        print(
            f"{label}: {len(aligned)} valid paths, {skipped_count} skipped; "
            f"W_eff={min(item.effective_width for item in aligned):.6f}.."
            f"{max(item.effective_width for item in aligned):.6f} deg"
        )
        if skipped:
            print("  skip reasons: " + ", ".join(f"{key}={value}" for key, value in sorted(skipped.items())))
        dataset_reports.append(
            {
                "label": label,
                "session": str(path.resolve()),
                "input_events": len(session.events),
                "valid_paths": len(aligned),
                "valid_weight": sum(item.weight for item in aligned),
                "skipped_reasons": skipped,
            }
        )

    edges = (
        _parse_edges(args.width_edges)
        if args.width_edges
        else quantile_edges(datasets[0][1], args.strata)
    )
    print("W_eff edges [deg]: " + ", ".join(f"{edge:.6f}" for edge in edges))
    _plot(
        datasets,
        edges,
        args.output,
        histogram_bins=args.histogram_bins,
        mean_samples=args.mean_samples,
        plot_quantile=args.plot_quantile,
        show=args.show,
    )
    report_path = args.output.with_suffix(".json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "report_schema_version": 1,
        "stratification": "effective_angular_target_width",
        "effective_width_edges_deg": edges,
        "coordinate_system": {
            "start": [0.0, 0.0],
            "target": [1.0, 0.0],
            "normalized_by": "start_to_target_angular_distance",
        },
        "movement_segmentation": {
            "enabled": segmentation_config is not None,
            "config": asdict(segmentation_config) if segmentation_config else None,
            "generated_sessions_are_not_resegmented": True,
        },
        "plot_quantile": args.plot_quantile,
        "datasets": dataset_reports,
    }
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {report_path.resolve()}")


if __name__ == "__main__":
    main()
