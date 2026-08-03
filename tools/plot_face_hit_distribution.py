#!/usr/bin/env python3
"""Compare block-local human and generated raycast hit distributions."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.dataset_groups import add_dataset_arguments, resolve_dataset_groups
from analysis.mining_session import MiningEvent, MiningSession, load_mining_session


FACE_LAYOUT = (
    ("down", "north", "west"),
    ("up", "south", "east"),
)
FACE_AXES = {
    "down": ("x", "z"),
    "up": ("x", "z"),
    "north": ("x", "y"),
    "south": ("x", "y"),
    "west": ("z", "y"),
    "east": ("z", "y"),
}


@dataclass(frozen=True)
class FaceHit:
    event_id: int
    face: str
    u: float
    v: float
    weight: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare block-local human raycast hits with reconstructed final "
            "hits from generated path datasets."
        )
    )
    add_dataset_arguments(parser)
    parser.add_argument(
        "--output", type=Path, default=Path("face-hit-distribution.png")
    )
    parser.add_argument("--histogram-bins", type=int, default=40)
    parser.add_argument(
        "--paired-only",
        action="store_true",
        help=(
            "Restrict every dataset to source session/event pairs present in "
            "all supplied generated dataset groups."
        ),
    )
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _local_face_hit(
    event_id: int,
    target: tuple[int, int, int],
    face_value: object,
    hit: tuple[object, object, object],
    *,
    weight: float,
    tolerance: float = 1.0e-6,
) -> FaceHit | None:
    face = str(face_value).lower() if face_value is not None else None
    if face not in FACE_AXES:
        return None
    try:
        coordinates = {
            "x": float(hit[0]) - target[0],
            "y": float(hit[1]) - target[1],
            "z": float(hit[2]) - target[2],
        }
    except (TypeError, ValueError):
        return None
    u_axis, v_axis = FACE_AXES[face]
    u = coordinates[u_axis]
    v = coordinates[v_axis]
    if not all(math.isfinite(value) for value in (u, v, weight)):
        return None
    if weight <= 0.0:
        return None
    if not -tolerance <= u <= 1.0 + tolerance:
        return None
    if not -tolerance <= v <= 1.0 + tolerance:
        return None
    return FaceHit(
        event_id=event_id,
        face=face,
        u=min(1.0, max(0.0, u)),
        v=min(1.0, max(0.0, v)),
        weight=weight,
    )


def _human_face_hit(event: MiningEvent) -> FaceHit | None:
    if event.hit_x is None or event.hit_y is None or event.hit_z is None:
        return None
    return _local_face_hit(
        event.event_id,
        (event.target_x, event.target_y, event.target_z),
        event.face_id,
        (event.hit_x, event.hit_y, event.hit_z),
        weight=1.0,
    )


def _generated_metadata_by_event(
    session: MiningSession,
) -> dict[int, Mapping[str, object]]:
    raw_events = session.metadata.get("events", [])
    if not isinstance(raw_events, list):
        return {}
    return {
        int(item["generated_event_id"]): item
        for item in raw_events
        if isinstance(item, dict)
        and isinstance(item.get("generated_event_id"), int)
    }


def _generated_face_hit(
    event: MiningEvent,
    metadata: Mapping[str, object] | None,
) -> tuple[FaceHit | None, str | None]:
    if metadata is None:
        return None, "missing_event_metadata"
    endpoint = metadata.get("generator_endpoint_hit")
    if not isinstance(endpoint, dict):
        return None, "missing_generator_endpoint"
    status = str(endpoint.get("status", "missing_status"))
    if status != "hit":
        return None, status
    try:
        weight = float(metadata.get("analysis_weight", 1.0))
    except (TypeError, ValueError):
        return None, "invalid_analysis_weight"
    hit = _local_face_hit(
        event.event_id,
        (event.target_x, event.target_y, event.target_z),
        endpoint.get("face_id"),
        (
            endpoint.get("hit_x"),
            endpoint.get("hit_y"),
            endpoint.get("hit_z"),
        ),
        weight=weight,
    )
    return (hit, None) if hit is not None else (None, "invalid_generator_hit")


def _session_hits(
    session: MiningSession,
    *,
    allowed_source_events: set[tuple[str, int]] | None = None,
) -> tuple[tuple[FaceHit, ...], Counter[str]]:
    generated = session.metadata.get("source") == "minescript-miner-generated"
    metadata_by_event = (
        _generated_metadata_by_event(session) if generated else {}
    )
    hits: list[FaceHit] = []
    invalid: Counter[str] = Counter()
    for recorded in session.events:
        if generated:
            event_metadata = metadata_by_event.get(recorded.event.event_id)
            source_event_id = (
                event_metadata.get("source_event_id")
                if event_metadata is not None
                else None
            )
            source_session_id = session.metadata.get("source_session_id")
            source_key = (
                (str(source_session_id), int(source_event_id))
                if source_session_id is not None
                and isinstance(source_event_id, int)
                else None
            )
            if (
                allowed_source_events is not None
                and source_key not in allowed_source_events
            ):
                invalid["not_in_paired_subset"] += 1
                continue
            hit, reason = _generated_face_hit(
                recorded.event,
                event_metadata,
            )
        else:
            if (
                allowed_source_events is not None
                and (session.session_id, recorded.event.event_id)
                not in allowed_source_events
            ):
                invalid["not_in_paired_subset"] += 1
                continue
            hit = _human_face_hit(recorded.event)
            reason = None if hit is not None else "invalid_or_missing_human_hit"
        if hit is None:
            invalid[reason or "invalid_hit"] += 1
        else:
            hits.append(hit)
    return tuple(hits), invalid


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


def _weighted_median(hits: Sequence[FaceHit], attribute: str) -> float | None:
    return _weighted_quantile(
        [float(getattr(hit, attribute)) for hit in hits],
        [hit.weight for hit in hits],
        0.5,
    )


def _center_summary(hits: Sequence[FaceHit]) -> dict[str, float | None]:
    if not hits:
        return {
            "center_distance_median": None,
            "center_distance_p90": None,
            "within_center_radius_0_1": None,
        }
    distances = [math.hypot(hit.u - 0.5, hit.v - 0.5) for hit in hits]
    weights = [hit.weight for hit in hits]
    total_weight = sum(weights)
    return {
        "center_distance_median": _weighted_quantile(distances, weights, 0.5),
        "center_distance_p90": _weighted_quantile(distances, weights, 0.9),
        "within_center_radius_0_1": sum(
            hit.weight
            for hit, distance in zip(hits, distances)
            if distance <= 0.1
        ) / total_weight,
    }


def _plot(
    datasets: Sequence[tuple[str, tuple[FaceHit, ...]]],
    output: Path,
    *,
    histogram_bins: int,
    show: bool,
) -> list[dict[str, object]]:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import PowerNorm

    histograms: dict[tuple[int, str], object | None] = {}
    hits_by_panel: dict[tuple[int, str], tuple[FaceHit, ...]] = {}
    for dataset_index, (_, hits) in enumerate(datasets):
        for row in FACE_LAYOUT:
            for face in row:
                face_hits = tuple(hit for hit in hits if hit.face == face)
                hits_by_panel[(dataset_index, face)] = face_hits
                if not face_hits:
                    histograms[(dataset_index, face)] = None
                    continue
                histogram, _, _ = np.histogram2d(
                    [hit.u for hit in face_hits],
                    [hit.v for hit in face_hits],
                    weights=[hit.weight for hit in face_hits],
                    bins=histogram_bins,
                    range=((0.0, 1.0), (0.0, 1.0)),
                )
                histograms[(dataset_index, face)] = histogram / histogram.sum()
    vmax = max(
        (
            float(histogram.max())
            for histogram in histograms.values()
            if histogram is not None
        ),
        default=1.0,
    )

    figure, axes = plt.subplots(
        len(datasets) * 2,
        3,
        figsize=(14.5, 7.8 * len(datasets)),
        squeeze=False,
        constrained_layout=True,
    )
    figure.suptitle(
        "Block-local final raycast hits: conditional weighted density per dataset and face",
        fontsize=14,
    )
    for dataset_index, (label, _) in enumerate(datasets):
        figure.text(
            0.006,
            1.0 - (dataset_index + 0.5) / len(datasets),
            label,
            rotation=90,
            va="center",
            ha="left",
            fontsize=12,
            fontweight="bold",
        )
    image = None
    dataset_reports: list[dict[str, object]] = []
    for dataset_index, (label, hits) in enumerate(datasets):
        total_weight = sum(hit.weight for hit in hits)
        face_reports: list[dict[str, object]] = []
        for local_row, row in enumerate(FACE_LAYOUT):
            for column_index, face in enumerate(row):
                axis = axes[dataset_index * 2 + local_row][column_index]
                face_hits = hits_by_panel[(dataset_index, face)]
                histogram = histograms[(dataset_index, face)]
                u_axis, v_axis = FACE_AXES[face]
                if histogram is None:
                    axis.text(
                        0.5,
                        0.5,
                        "no hits",
                        ha="center",
                        va="center",
                        transform=axis.transAxes,
                    )
                    median_u = None
                    median_v = None
                else:
                    image = axis.imshow(
                        histogram.T,
                        origin="lower",
                        extent=(0.0, 1.0, 0.0, 1.0),
                        cmap="magma",
                        norm=PowerNorm(gamma=0.45, vmin=0.0, vmax=vmax),
                        interpolation="nearest",
                    )
                    median_u = _weighted_median(face_hits, "u")
                    median_v = _weighted_median(face_hits, "v")
                    axis.scatter(
                        [median_u],
                        [median_v],
                        marker="+",
                        s=90,
                        linewidths=1.8,
                        color="cyan",
                        label="weighted median",
                    )
                    axis.legend(loc="upper right", fontsize="small")
                axis.axvline(0.5, color="white", alpha=0.16, linewidth=0.8)
                axis.axhline(0.5, color="white", alpha=0.16, linewidth=0.8)
                axis.set_xlim(0.0, 1.0)
                axis.set_ylim(0.0, 1.0)
                axis.set_aspect("equal")
                axis.set_xlabel(f"local {u_axis}")
                axis.set_ylabel(f"local {v_axis}")
                face_weight = sum(hit.weight for hit in face_hits)
                share = face_weight / total_weight if total_weight else 0.0
                axis.set_title(
                    f"{face.upper()} | n={len(face_hits)}\n"
                    f"weighted={face_weight:.1f} ({share:.1%})",
                    fontsize=10,
                )
                face_reports.append(
                    {
                        "face": face,
                        "u_axis": u_axis,
                        "v_axis": v_axis,
                        "hit_count": len(face_hits),
                        "hit_weight": face_weight,
                        "hit_share": share,
                        "median_u": median_u,
                        "median_v": median_v,
                        **_center_summary(face_hits),
                    }
                )
        dataset_reports.append(
            {
                "label": label,
                "valid_hits": len(hits),
                "valid_weight": total_weight,
                "all_faces_center_summary": _center_summary(hits),
                "faces": face_reports,
            }
        )
    if image is not None:
        figure.colorbar(
            image,
            ax=axes,
            label="conditional weighted hit density",
            shrink=0.82,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=170)
    print(f"Wrote {output.resolve()}")
    if show:
        plt.show()
    plt.close(figure)
    return dataset_reports


def main() -> None:
    args = parse_args()
    if args.histogram_bins <= 1:
        raise SystemExit("--histogram-bins must be greater than one")
    groups = resolve_dataset_groups(args.sessions, args.labels, args.dataset)
    loaded_groups = tuple(
        (
            group,
            tuple((path, load_mining_session(path)) for path in group.sessions),
        )
        for group in groups
    )
    generated_source_sets: list[set[tuple[str, int]]] = []
    for _, sessions in loaded_groups:
        group_source_events: set[tuple[str, int]] = set()
        is_generated_group = False
        for _, session in sessions:
            if session.metadata.get("source") != "minescript-miner-generated":
                continue
            is_generated_group = True
            source_session_id = session.metadata.get("source_session_id")
            if source_session_id is None:
                continue
            for metadata in _generated_metadata_by_event(session).values():
                source_event_id = metadata.get("source_event_id")
                if isinstance(source_event_id, int):
                    group_source_events.add(
                        (str(source_session_id), source_event_id)
                    )
        if is_generated_group:
            generated_source_sets.append(group_source_events)
    if args.paired_only:
        if not generated_source_sets:
            raise SystemExit("--paired-only requires a generated dataset group")
        allowed_source_events = set.intersection(*generated_source_sets)
        if not allowed_source_events:
            raise SystemExit("generated dataset groups share no source events")
    else:
        allowed_source_events = None

    datasets: list[tuple[str, tuple[FaceHit, ...]]] = []
    input_reports: list[dict[str, object]] = []
    for group, sessions in loaded_groups:
        group_hits: list[FaceHit] = []
        invalid: Counter[str] = Counter()
        session_reports: list[dict[str, object]] = []
        for path, session in sessions:
            hits, session_invalid = _session_hits(
                session,
                allowed_source_events=allowed_source_events,
            )
            group_hits.extend(hits)
            invalid.update(session_invalid)
            session_reports.append(
                {
                    "session": str(path.resolve()),
                    "input_events": len(session.events),
                    "valid_hits": len(hits),
                    "invalid_reasons": dict(session_invalid),
                }
            )
        if not group_hits:
            raise SystemExit(
                f"{group.label}: no valid face hits ({dict(invalid)})"
            )
        datasets.append((group.label, tuple(group_hits)))
        input_reports.append(
            {
                "label": group.label,
                "sessions": session_reports,
                "invalid_reasons": dict(invalid),
            }
        )

    plotted = _plot(
        datasets,
        args.output,
        histogram_bins=args.histogram_bins,
        show=args.show,
    )
    report = {
        "report_schema_version": 2,
        "plot": "block_local_face_hit_distribution_comparison",
        "coordinate_system": "block-local world axes in [0, 1]",
        "density_normalization": "each dataset and face independently sums to one",
        "paired_only": args.paired_only,
        "paired_source_event_count": (
            len(allowed_source_events)
            if allowed_source_events is not None
            else None
        ),
        "groups": [
            {**input_report, **plot_report}
            for input_report, plot_report in zip(input_reports, plotted)
        ],
    }
    report_path = args.output.with_suffix(".json")
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {report_path.resolve()}")


if __name__ == "__main__":
    main()
