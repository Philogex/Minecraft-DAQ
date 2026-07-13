#!/usr/bin/env python3
"""Compare weighted kinematic feature distributions across trajectory datasets."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.aim_features import (
    COMPARISON_FEATURE_NAMES,
    AimPoint,
    TargetMetrics,
    compute_aim_path_features,
)
from analysis.dataset_groups import add_dataset_arguments, resolve_dataset_groups
from analysis.minescript_miner_backend import MinescriptMinerBackend
from analysis.mining_session import load_mining_session
from analysis.movement_segmentation import MovementSegmentationConfig
from analysis.path_density import AlignedPath, align_paths, weighted_quantile
from tools.plot_path_density import MOUSE_PATH_RECONSTRUCTION, _records_for_session


UNIT_INDEPENDENT_REFERENCE_FEATURES = frozenset(
    {
        "fitts_mt",
        "sub_peak_count",
        "sub_primary_amp_ratio",
        "sub_correction_onset",
        "sub_interpeak_cv",
        "sub_peak_speed_ratio",
        "smooth_norm_jerk",
        "smooth_ldlj",
        "geo_path_efficiency",
        "geo_angular_dev_at_peak",
        "geo_curvature_integral",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot weighted histograms and medians for the kinematic features "
            "formerly shown in the single-path summary table."
        )
    )
    add_dataset_arguments(parser)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("feature-distributions.png"),
    )
    parser.add_argument("--histogram-bins", type=int, default=40)
    parser.add_argument(
        "--value-quantile",
        type=float,
        default=0.99,
        help="Central weighted value mass used for x limits (default: 0.99).",
    )
    parser.add_argument("--fitts-a-ms", type=float)
    parser.add_argument("--fitts-b-ms", type=float)
    parser.add_argument("--eye-height", type=float, default=1.62)
    parser.add_argument("--config", type=Path, help="Minescript-Miner aim config.")
    parser.add_argument(
        "--reference-features",
        action="append",
        type=Path,
        help="External feature CSV; repeat for multiple references.",
    )
    parser.add_argument(
        "--reference-label",
        action="append",
        help="Label for --reference-features; repeat in the same order.",
    )
    parser.add_argument("--no-segmentation", action="store_true")
    parser.add_argument("--max-idle-gap-ms", type=float, default=150.0)
    parser.add_argument("--minimum-motion-ratio", type=float, default=0.1)
    parser.add_argument("--max-player-displacement", type=float, default=0.05)
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _features_for_path(
    path: AlignedPath,
    *,
    fitts_a_ms: float,
    fitts_b_ms: float,
) -> dict[str, float]:
    first_time = path.times_ms[0]
    points = tuple(
        AimPoint(
            yaw=x * path.distance,
            pitch=y * path.distance,
            t_ms=time - first_time,
        )
        for x, y, time in zip(path.x, path.y, path.times_ms)
    )
    target = TargetMetrics(
        yaw=path.distance,
        pitch=0.0,
        width_yaw=path.effective_width,
        width_pitch=path.effective_width,
    )
    features = compute_aim_path_features(
        points,
        target,
        fitts_a_ms=fitts_a_ms,
        fitts_b_ms=fitts_b_ms,
        fallback_width_deg=path.effective_width,
        wrap_yaw=False,
    )
    return {
        name: float(getattr(features, name))
        for name in COMPARISON_FEATURE_NAMES
    }


def _load_reference_features(path: Path) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            values: dict[str, float] = {}
            for name in COMPARISON_FEATURE_NAMES:
                if name not in UNIT_INDEPENDENT_REFERENCE_FEATURES:
                    values[name] = math.nan
                    continue
                try:
                    values[name] = float(row[name])
                except (KeyError, TypeError, ValueError):
                    values[name] = math.nan
            rows.append(values)
    return rows


def _feature_range(
    values_by_dataset: list[list[float]],
    weights_by_dataset: list[list[float]],
    quantile: float,
) -> tuple[float, float]:
    values = [value for dataset in values_by_dataset for value in dataset]
    weights = [weight for dataset in weights_by_dataset for weight in dataset]
    if not values:
        return 0.0, 1.0
    tail = (1.0 - quantile) / 2.0
    lower = weighted_quantile(values, weights, tail)
    upper = weighted_quantile(values, weights, 1.0 - tail)
    if math.isclose(lower, upper):
        epsilon = max(abs(lower) * 0.05, 0.5 if float(lower).is_integer() else 1e-9)
        return lower - epsilon, upper + epsilon
    return lower, upper


def _histogram_edges(
    feature_name: str,
    lower: float,
    upper: float,
    bin_count: int,
):
    import numpy as np

    if feature_name == "sub_peak_count":
        first = math.floor(lower)
        last = math.ceil(upper)
        return np.arange(first - 0.5, last + 1.5, 1.0)
    return np.linspace(lower, upper, bin_count + 1)


def _format_median(value: float) -> str:
    magnitude = abs(value)
    if magnitude != 0.0 and (magnitude >= 10_000.0 or magnitude < 0.001):
        return f"{value:.3e}"
    if magnitude >= 100.0:
        return f"{value:.1f}"
    return f"{value:.3f}"


def _plot(
    datasets: list[dict[str, object]],
    output: Path,
    *,
    histogram_bins: int,
    value_quantile: float,
    show: bool,
) -> list[dict[str, object]]:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    columns = 3
    rows = math.ceil(len(COMPARISON_FEATURE_NAMES) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(18, 4.0 * rows),
        squeeze=False,
        constrained_layout=True,
    )
    figure.suptitle(
        "Weighted kinematic feature distributions\n"
        "histograms share bins per feature; dashed lines mark weighted medians"
    )
    colors = [f"C{index}" for index in range(len(datasets))]
    reports: list[dict[str, object]] = []

    for feature_index, feature_name in enumerate(COMPARISON_FEATURE_NAMES):
        axis = axes.flat[feature_index]
        values_by_dataset: list[list[float]] = []
        weights_by_dataset: list[list[float]] = []
        missing_weights: list[float] = []
        for dataset in datasets:
            finite_values: list[float] = []
            finite_weights: list[float] = []
            missing_weight = 0.0
            for features, weight in dataset["features"]:
                value = features[feature_name]
                if math.isfinite(value):
                    finite_values.append(value)
                    finite_weights.append(weight)
                else:
                    missing_weight += weight
            values_by_dataset.append(finite_values)
            weights_by_dataset.append(finite_weights)
            missing_weights.append(missing_weight)

        lower, upper = _feature_range(
            values_by_dataset,
            weights_by_dataset,
            value_quantile,
        )
        edges = _histogram_edges(feature_name, lower, upper, histogram_bins)
        feature_report: dict[str, object] = {
            "feature": feature_name,
            "viewport": [float(edges[0]), float(edges[-1])],
            "datasets": [],
        }

        for dataset_index, dataset in enumerate(datasets):
            values = values_by_dataset[dataset_index]
            weights = weights_by_dataset[dataset_index]
            color = colors[dataset_index]
            label = str(dataset["label"])
            if not values:
                feature_report["datasets"].append(
                    {
                        "label": label,
                        "finite_weight": 0.0,
                        "missing_weight": missing_weights[dataset_index],
                        "median": None,
                        "weight_in_viewport": 0.0,
                    }
                )
                continue
            counts, _ = np.histogram(values, bins=edges, weights=weights)
            median = weighted_quantile(values, weights, 0.5)
            axis.stairs(
                counts,
                edges,
                fill=True,
                alpha=0.24,
                color=color,
                label=f"{label} (median={_format_median(median)})",
            )
            axis.stairs(counts, edges, color=color, linewidth=1.2)
            axis.axvline(
                median,
                color=color,
                linestyle="--",
                linewidth=1.8,
            )
            finite_weight = sum(weights)
            viewport_weight = sum(
                weight
                for value, weight in zip(values, weights)
                if edges[0] <= value <= edges[-1]
            )
            feature_report["datasets"].append(
                {
                    "label": label,
                    "finite_weight": finite_weight,
                    "missing_weight": missing_weights[dataset_index],
                    "median": median,
                    "weight_in_viewport": viewport_weight,
                }
            )

        axis.set_title(feature_name)
        axis.set_xlabel("feature value")
        axis.set_ylabel("weighted count")
        axis.grid(True, axis="y", alpha=0.2)
        axis.legend(fontsize="small")
        reports.append(feature_report)

    for unused_index in range(len(COMPARISON_FEATURE_NAMES), rows * columns):
        axes.flat[unused_index].axis("off")

    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160)
    print(f"Wrote {output.resolve()}")
    if show:
        plt.show()
    plt.close(figure)
    return reports


def main() -> None:
    args = parse_args()
    reference_paths = args.reference_features or []
    reference_labels = args.reference_label or []
    if len(reference_paths) != len(reference_labels):
        raise SystemExit(
            "repeat --reference-label exactly once per --reference-features"
        )
    if args.histogram_bins <= 1:
        raise SystemExit("--histogram-bins must be greater than one")
    if not 0.0 < args.value_quantile <= 1.0:
        raise SystemExit("--value-quantile must be in (0, 1]")

    groups = resolve_dataset_groups(args.sessions, args.labels, args.dataset)
    backend = MinescriptMinerBackend("sigmadrift", args.config)
    fitts_a_ms = (
        args.fitts_a_ms
        if args.fitts_a_ms is not None
        else backend.config.sigmadrift.fitts_a
    )
    fitts_b_ms = (
        args.fitts_b_ms
        if args.fitts_b_ms is not None
        else backend.config.sigmadrift.fitts_b
    )
    segmentation_config = None
    if not args.no_segmentation:
        segmentation_config = MovementSegmentationConfig(
            max_idle_gap_ms=args.max_idle_gap_ms,
            minimum_motion_ratio=args.minimum_motion_ratio,
            max_player_displacement=args.max_player_displacement,
        )

    datasets: list[dict[str, object]] = []
    dataset_reports: list[dict[str, object]] = []
    for group in groups:
        feature_rows: list[tuple[dict[str, float], float]] = []
        group_skipped: dict[str, int] = {}
        session_reports: list[dict[str, object]] = []
        input_events = 0
        valid_paths = 0
        valid_weight = 0.0
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
            feature_rows.extend(
                (
                    _features_for_path(
                        item,
                        fitts_a_ms=fitts_a_ms,
                        fitts_b_ms=fitts_b_ms,
                    ),
                    item.weight,
                )
                for item in aligned
            )
            session_weight = sum(item.weight for item in aligned)
            valid_paths += len(aligned)
            valid_weight += session_weight
            input_events += len(session.events)
            session_reports.append(
                {
                    "session": str(path.resolve()),
                    "input_events": len(session.events),
                    "valid_paths": len(aligned),
                    "valid_weight": session_weight,
                    "skipped_reasons": session_skipped,
                }
            )
        if not feature_rows:
            raise SystemExit(f"{group.label}: no valid paths")
        datasets.append({"label": group.label, "features": feature_rows})
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
                "valid_paths": valid_paths,
                "valid_weight": valid_weight,
                "skipped_reasons": group_skipped,
            }
        )
        print(
            f"{group.label}: {valid_paths} valid paths from "
            f"{len(group.sessions)} session(s), {sum(group_skipped.values())} skipped"
        )

    reference_weight = float(dataset_reports[0]["valid_weight"])
    omitted_reference_features = sorted(
        set(COMPARISON_FEATURE_NAMES) - UNIT_INDEPENDENT_REFERENCE_FEATURES
    )
    for label, path in zip(reference_labels, reference_paths):
        feature_values = _load_reference_features(path)
        if not feature_values:
            raise SystemExit(f"{path}: no reference feature rows")
        row_weight = reference_weight / len(feature_values)
        datasets.append(
            {
                "label": label,
                "features": [(values, row_weight) for values in feature_values],
            }
        )
        dataset_reports.append(
            {
                "label": label,
                "source": str(path.resolve()),
                "input_rows": len(feature_values),
                "valid_weight": reference_weight,
                "row_weight": row_weight,
                "omitted_incomparable_features": omitted_reference_features,
            }
        )
        print(
            f"{label}: {len(feature_values)} reference rows; omitted "
            + ", ".join(omitted_reference_features)
        )

    feature_reports = _plot(
        datasets,
        args.output,
        histogram_bins=args.histogram_bins,
        value_quantile=args.value_quantile,
        show=args.show,
    )
    report_path = args.output.with_suffix(".json")
    report = {
        "report_schema_version": 1,
        "plot": "kinematic_feature_distributions",
        "feature_names": COMPARISON_FEATURE_NAMES,
        "fitts_model": {"a_ms": fitts_a_ms, "b_ms": fitts_b_ms},
        "movement_segmentation": {
            "enabled": segmentation_config is not None,
            "config": asdict(segmentation_config) if segmentation_config else None,
            "generated_sessions_are_not_resegmented": True,
        },
        "value_quantile": args.value_quantile,
        "human_trajectory_reconstruction": MOUSE_PATH_RECONSTRUCTION,
        "external_reference_comparable_features": sorted(
            UNIT_INDEPENDENT_REFERENCE_FEATURES
        ),
        "datasets": dataset_reports,
        "features": feature_reports,
    }
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {report_path.resolve()}")


if __name__ == "__main__":
    main()
