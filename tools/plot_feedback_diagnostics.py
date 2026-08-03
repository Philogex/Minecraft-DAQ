#!/usr/bin/env python3
"""Plot controller-specific geometry-feedback diagnostics."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.dataset_groups import add_dataset_arguments, resolve_dataset_groups


@dataclass(frozen=True)
class DiagnosticEvent:
    values: Mapping[str, object]
    weight: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot exact controller diagnostics from generated "
            "geometry-feedback SigmaDrift datasets."
        )
    )
    add_dataset_arguments(parser)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("geometry-feedback-diagnostics.png"),
    )
    parser.add_argument("--histogram-bins", type=int, default=24)
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _load_session(path: Path) -> tuple[list[DiagnosticEvent], int]:
    metadata_path = path / "metadata.json"
    if not metadata_path.is_file():
        raise ValueError(f"missing generated metadata: {metadata_path}")
    with metadata_path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)
    raw_events = metadata.get("events", [])
    if not isinstance(raw_events, list):
        raise ValueError(f"metadata events must be an array: {metadata_path}")

    events: list[DiagnosticEvent] = []
    missing = 0
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            missing += 1
            continue
        diagnostics = raw_event.get("generator_diagnostics")
        if not isinstance(diagnostics, dict) or not diagnostics:
            missing += 1
            continue
        weight = float(raw_event.get("analysis_weight", 1.0))
        if not math.isfinite(weight) or weight <= 0.0:
            raise ValueError(f"invalid analysis weight in {metadata_path}")
        events.append(DiagnosticEvent(diagnostics, weight))
    return events, missing


def _number(event: DiagnosticEvent, name: str) -> float:
    try:
        value = float(event.values[name])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"missing or invalid diagnostic {name!r}") from error
    if not math.isfinite(value):
        raise ValueError(f"non-finite diagnostic {name!r}")
    return value


def _boolean(event: DiagnosticEvent, name: str) -> bool:
    value = event.values.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"missing or invalid diagnostic {name!r}")
    return value


def _weighted_quantile(
    values: Sequence[float],
    weights: Sequence[float],
    quantile: float,
) -> float | None:
    if not values:
        return None
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    threshold = sum(weights) * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _summary(events: Sequence[DiagnosticEvent]) -> dict[str, object]:
    weights = [event.weight for event in events]
    total_weight = sum(weights)
    numeric_names = (
        "feedback_check_count",
        "correction_count",
        "first_visible_entry_ms",
        "first_safe_entry_ms",
        "visible_entry_count",
        "visible_exit_count",
        "safe_entry_count",
        "safe_exit_count",
        "applied_margin_steps",
    )
    numeric: dict[str, object] = {}
    for name in numeric_names:
        pairs = [
            (_number(event, name), event.weight)
            for event in events
            if not name.startswith("first_") or _number(event, name) >= 0.0
        ]
        values = [pair[0] for pair in pairs]
        local_weights = [pair[1] for pair in pairs]
        local_weight = sum(local_weights)
        numeric[name] = {
            "valid_count": len(values),
            "valid_weight": local_weight,
            "mean": (
                sum(value * weight for value, weight in pairs) / local_weight
                if local_weight > 0.0
                else None
            ),
            "median": _weighted_quantile(values, local_weights, 0.5),
        }
    return {
        "event_count": len(events),
        "total_weight": total_weight,
        "final_visible_rate": sum(
            event.weight for event in events if _boolean(event, "final_visible")
        ) / total_weight,
        "final_safe_rate": sum(
            event.weight for event in events if _boolean(event, "final_safe")
        ) / total_weight,
        "first_visible_entry_rate": sum(
            event.weight
            for event in events
            if _number(event, "first_visible_entry_ms") >= 0.0
        ) / total_weight,
        "first_safe_entry_rate": sum(
            event.weight
            for event in events
            if _number(event, "first_safe_entry_ms") >= 0.0
        ) / total_weight,
        "numeric": numeric,
    }


def _discrete_distribution(
    events: Sequence[DiagnosticEvent],
    name: str,
) -> tuple[list[int], list[float]]:
    counts: dict[int, float] = {}
    for event in events:
        value = round(_number(event, name))
        counts[value] = counts.get(value, 0.0) + event.weight
    total = sum(counts.values())
    x = sorted(counts)
    return x, [counts[value] / total for value in x]


def _plot_discrete(axis, datasets, name: str, colors) -> None:
    for index, (label, events) in enumerate(datasets):
        x, probability = _discrete_distribution(events, name)
        axis.plot(
            x,
            probability,
            marker="o",
            linewidth=1.7,
            color=colors[index],
            label=label,
        )
    axis.set_xlabel("count")
    axis.set_ylabel("weighted probability")
    axis.set_ylim(bottom=0.0)
    axis.grid(alpha=0.2)


def _plot_continuous(
    axis,
    datasets,
    names: Sequence[tuple[str, str]],
    colors,
    bins: int,
    xlabel: str,
) -> None:
    import numpy as np

    pooled = [
        _number(event, name)
        for _, events in datasets
        for event in events
        for name, _ in names
        if _number(event, name) >= 0.0
    ]
    if not pooled:
        axis.text(0.5, 0.5, "no valid values", ha="center", va="center")
        return
    lower = min(pooled)
    upper = max(pooled)
    if lower == upper:
        lower -= 0.5
        upper += 0.5
    edges = np.linspace(lower, upper, bins + 1)
    styles = ("-", "--", ":", "-.")
    for dataset_index, (label, events) in enumerate(datasets):
        for field_index, (name, field_label) in enumerate(names):
            pairs = [
                (_number(event, name), event.weight)
                for event in events
                if _number(event, name) >= 0.0
            ]
            if not pairs:
                continue
            values = np.asarray([pair[0] for pair in pairs])
            weights = np.asarray([pair[1] for pair in pairs])
            histogram, _ = np.histogram(values, bins=edges, weights=weights)
            if histogram.sum() > 0.0:
                histogram = histogram / histogram.sum()
            centers = (edges[:-1] + edges[1:]) / 2.0
            axis.plot(
                centers,
                histogram,
                color=colors[dataset_index],
                linestyle=styles[field_index],
                linewidth=1.7,
                label=f"{label}: {field_label}",
            )
    axis.set_xlabel(xlabel)
    axis.set_ylabel("weighted probability")
    axis.set_ylim(bottom=0.0)
    axis.grid(alpha=0.2)


def _plot_final_rates(axis, datasets, colors) -> None:
    import numpy as np

    x = np.arange(2)
    width = 0.8 / len(datasets)
    for index, (label, events) in enumerate(datasets):
        total = sum(event.weight for event in events)
        rates = [
            sum(
                event.weight
                for event in events
                if _boolean(event, field)
            ) / total
            for field in ("final_visible", "final_safe")
        ]
        axis.bar(
            x - 0.4 + width / 2.0 + index * width,
            rates,
            width=width,
            color=colors[index],
            label=label,
        )
    axis.set_xticks(x, ("visible", "safe inset"))
    axis.set_ylabel("weighted final-hit rate")
    axis.set_ylim(0.0, 1.05)
    axis.grid(axis="y", alpha=0.2)


def _plot_exit_counts(axis, datasets, colors) -> None:
    styles = ("-", "--")
    fields = (("visible_exit_count", "visible"), ("safe_exit_count", "safe"))
    for dataset_index, (label, events) in enumerate(datasets):
        for field_index, (name, field_label) in enumerate(fields):
            x, probability = _discrete_distribution(events, name)
            axis.plot(
                x,
                probability,
                marker="o",
                color=colors[dataset_index],
                linestyle=styles[field_index],
                label=f"{label}: {field_label}",
            )
    axis.set_xlabel("exit count")
    axis.set_ylabel("weighted probability")
    axis.set_ylim(bottom=0.0)
    axis.grid(alpha=0.2)


def main() -> None:
    args = parse_args()
    if args.histogram_bins <= 1:
        raise SystemExit("--histogram-bins must be greater than one")
    groups = resolve_dataset_groups(args.sessions, args.labels, args.dataset)

    datasets: list[tuple[str, tuple[DiagnosticEvent, ...]]] = []
    report_groups: list[dict[str, object]] = []
    for group in groups:
        group_events: list[DiagnosticEvent] = []
        missing = 0
        for session_path in group.sessions:
            events, session_missing = _load_session(session_path)
            group_events.extend(events)
            missing += session_missing
        if not group_events:
            raise SystemExit(
                f"{group.label}: no geometry-feedback diagnostics found"
            )
        events = tuple(group_events)
        datasets.append((group.label, events))
        report_groups.append(
            {
                "label": group.label,
                "sessions": [str(path.resolve()) for path in group.sessions],
                "missing_diagnostic_events": missing,
                **_summary(events),
            }
        )

    if not args.show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = [plt.get_cmap("tab10")(index % 10) for index in range(len(datasets))]
    figure, axes = plt.subplots(2, 3, figsize=(16, 9), constrained_layout=True)
    _plot_discrete(axes[0, 0], datasets, "feedback_check_count", colors)
    axes[0, 0].set_title("Feedback Evaluations")
    _plot_discrete(axes[0, 1], datasets, "correction_count", colors)
    axes[0, 1].set_title("Correction Impulses")
    _plot_continuous(
        axes[0, 2],
        datasets,
        (("first_visible_entry_ms", "visible"), ("first_safe_entry_ms", "safe")),
        colors,
        args.histogram_bins,
        "first entry time (ms)",
    )
    axes[0, 2].set_title("First Region Entry")
    _plot_exit_counts(axes[1, 0], datasets, colors)
    axes[1, 0].set_title("Post-entry Exits")
    _plot_final_rates(axes[1, 1], datasets, colors)
    axes[1, 1].set_title("Final Region Membership")
    _plot_continuous(
        axes[1, 2],
        datasets,
        (("applied_margin_steps", "margin"),),
        colors,
        args.histogram_bins,
        "applied margin (Minecraft steps)",
    )
    axes[1, 2].set_title("Applied Safe Margin")
    for axis in axes.flat:
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            axis.legend(fontsize=8)
    figure.suptitle("Geometry-feedback SigmaDrift controller diagnostics")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=160)
    if args.show:
        plt.show()
    plt.close(figure)

    report = {
        "diagnostic_schema": 1,
        "figure": str(args.output.resolve()),
        "groups": report_groups,
    }
    report_path = args.output.with_suffix(".json")
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {args.output.resolve()}")
    print(f"Wrote {report_path.resolve()}")


if __name__ == "__main__":
    main()
