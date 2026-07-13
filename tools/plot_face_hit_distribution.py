#!/usr/bin/env python3
"""Plot block-local raycast hit distributions for each Minecraft face."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.mining_session import MiningEvent, load_mining_session


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot raycast hit positions in block-local coordinates, separated "
            "by hit face."
        )
    )
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("face-hit-distribution.png")
    )
    parser.add_argument("--histogram-bins", type=int, default=40)
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def _local_coordinates(event: MiningEvent) -> dict[str, float] | None:
    if event.hit_x is None or event.hit_y is None or event.hit_z is None:
        return None
    return {
        "x": event.hit_x - event.target_x,
        "y": event.hit_y - event.target_y,
        "z": event.hit_z - event.target_z,
    }


def _face_hit(event: MiningEvent, *, tolerance: float = 1e-6) -> FaceHit | None:
    face = event.face_id.lower() if event.face_id is not None else None
    coordinates = _local_coordinates(event)
    if face not in FACE_AXES or coordinates is None:
        return None
    u_axis, v_axis = FACE_AXES[face]
    u = coordinates[u_axis]
    v = coordinates[v_axis]
    if not all(math.isfinite(value) for value in (u, v)):
        return None
    if not -tolerance <= u <= 1.0 + tolerance:
        return None
    if not -tolerance <= v <= 1.0 + tolerance:
        return None
    return FaceHit(
        event_id=event.event_id,
        face=face,
        u=min(1.0, max(0.0, u)),
        v=min(1.0, max(0.0, v)),
    )


def _plot(
    hits: tuple[FaceHit, ...],
    output: Path,
    *,
    histogram_bins: int,
    session_name: str,
    show: bool,
) -> list[dict[str, object]]:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import PowerNorm

    hits_by_face = {
        face: tuple(hit for hit in hits if hit.face == face)
        for row in FACE_LAYOUT
        for face in row
    }
    histograms: dict[str, object | None] = {}
    for face, face_hits in hits_by_face.items():
        if not face_hits:
            histograms[face] = None
            continue
        histogram, _, _ = np.histogram2d(
            [hit.u for hit in face_hits],
            [hit.v for hit in face_hits],
            bins=histogram_bins,
            range=((0.0, 1.0), (0.0, 1.0)),
        )
        histograms[face] = histogram / histogram.sum()
    vmax = max(
        (
            float(histogram.max())
            for histogram in histograms.values()
            if histogram is not None
        ),
        default=1.0,
    )

    figure, axes = plt.subplots(
        2,
        3,
        figsize=(14.5, 9.0),
        squeeze=False,
        constrained_layout=True,
    )
    figure.suptitle(
        f"Block-local raycast hit distribution: {session_name}\n"
        "coordinates retain world-axis orientation; density is normalized per face"
    )
    image = None
    reports: list[dict[str, object]] = []
    total_count = len(hits)
    for row_index, row in enumerate(FACE_LAYOUT):
        for column_index, face in enumerate(row):
            axis = axes[row_index][column_index]
            face_hits = hits_by_face[face]
            histogram = histograms[face]
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
                median_u = float(np.median([hit.u for hit in face_hits]))
                median_v = float(np.median([hit.v for hit in face_hits]))
                axis.scatter(
                    [median_u],
                    [median_v],
                    marker="+",
                    s=90,
                    linewidths=1.8,
                    color="cyan",
                    label="median hit",
                )
                axis.legend(loc="upper right", fontsize="small")
            axis.axvline(0.5, color="white", alpha=0.16, linewidth=0.8)
            axis.axhline(0.5, color="white", alpha=0.16, linewidth=0.8)
            axis.set_xlim(0.0, 1.0)
            axis.set_ylim(0.0, 1.0)
            axis.set_aspect("equal")
            axis.set_xlabel(f"local {u_axis}")
            axis.set_ylabel(f"local {v_axis}")
            share = len(face_hits) / total_count if total_count else 0.0
            axis.set_title(f"{face.upper()} | n={len(face_hits)} ({share:.1%})")
            reports.append(
                {
                    "face": face,
                    "u_axis": u_axis,
                    "v_axis": v_axis,
                    "hit_count": len(face_hits),
                    "hit_share": share,
                    "median_u": median_u,
                    "median_v": median_v,
                }
            )
    if image is not None:
        figure.colorbar(
            image,
            ax=axes,
            label="conditional hit density per face",
            shrink=0.82,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=170)
    print(f"Wrote {output.resolve()}")
    if show:
        plt.show()
    plt.close(figure)
    return reports


def main() -> None:
    args = parse_args()
    if args.histogram_bins <= 1:
        raise SystemExit("--histogram-bins must be greater than one")
    session = load_mining_session(args.session)
    hits = tuple(
        hit
        for recorded in session.events
        if (hit := _face_hit(recorded.event)) is not None
    )
    if not hits:
        raise SystemExit(f"{args.session}: no valid face hit points")
    invalid_count = len(session.events) - len(hits)
    print(
        f"{args.session.name}: {len(hits)} valid face hits, "
        f"{invalid_count} invalid or missing"
    )
    faces = _plot(
        hits,
        args.output,
        histogram_bins=args.histogram_bins,
        session_name=args.session.name,
        show=args.show,
    )
    report = {
        "report_schema_version": 1,
        "plot": "block_local_face_hit_distribution",
        "session": str(args.session.resolve()),
        "input_events": len(session.events),
        "valid_hits": len(hits),
        "invalid_or_missing_hits": invalid_count,
        "coordinate_system": "block-local world axes in [0, 1]",
        "density_normalization": "each face independently sums to one",
        "faces": faces,
    }
    report_path = args.output.with_suffix(".json")
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {report_path.resolve()}")


if __name__ == "__main__":
    main()
