#!/usr/bin/env python3
"""Render a compact visual overview of one recorded Minecraft mining event."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.aim_features import shortest_yaw_delta
from analysis.mining_session import RecordedMiningEvent, load_mining_session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot camera motion and raw mouse deltas before one mined block."
    )
    parser.add_argument("session", type=Path, help="Path to a DAQ mining-* directory.")
    parser.add_argument("--event-id", type=int, default=1, help="Event id to render.")
    parser.add_argument(
        "--output",
        type=Path,
        help="PNG path to write. Defaults below the recording directory.",
    )
    parser.add_argument("--show", action="store_true", help="Open a matplotlib window.")
    return parser.parse_args()


def _unwrap_yaws(yaws: list[float]) -> list[float]:
    if not yaws:
        return []
    unwrapped = [yaws[0]]
    for yaw in yaws[1:]:
        unwrapped.append(unwrapped[-1] + shortest_yaw_delta(unwrapped[-1], yaw))
    return unwrapped


def _angular_velocities(event: RecordedMiningEvent) -> tuple[list[float], list[float]]:
    samples = event.state_samples
    times: list[float] = []
    velocities: list[float] = []
    yaws = _unwrap_yaws([sample.yaw for sample in samples])
    for index in range(1, len(samples)):
        dt_s = (samples[index].relative_ms - samples[index - 1].relative_ms) / 1000.0
        if dt_s <= 0.0:
            continue
        angular_delta = math.hypot(
            yaws[index] - yaws[index - 1],
            samples[index].pitch - samples[index - 1].pitch,
        )
        times.append((samples[index].relative_ms + samples[index - 1].relative_ms) / 2.0)
        velocities.append(angular_delta / dt_s)
    return times, velocities


def plot_event(
    event: RecordedMiningEvent,
    output: Path | None,
    show: bool,
    *,
    source: str,
) -> None:
    if not show:
        import matplotlib

        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(3, 1, figsize=(12, 10), constrained_layout=True)
    target = event.event
    figure.suptitle(
        f"{source}: mining event "
        f"{target.event_id}: {target.block_state_before} at "
        f"({target.target_x}, {target.target_y}, {target.target_z})"
    )

    state_times = [sample.relative_ms for sample in event.state_samples]
    yaws = _unwrap_yaws([sample.yaw for sample in event.state_samples])
    pitches = [sample.pitch for sample in event.state_samples]
    orientation_axis = axes[0]
    orientation_axis.plot(state_times, yaws, label="yaw")
    orientation_axis.plot(state_times, pitches, label="pitch")
    orientation_axis.axvline(0.0, color="black", alpha=0.45, linewidth=1.0)
    orientation_axis.set_title("Camera Orientation")
    orientation_axis.set_xlabel("time relative to block break [ms]")
    orientation_axis.set_ylabel("angle [deg]")
    orientation_axis.grid(True, alpha=0.3)
    orientation_axis.legend()

    velocity_times, velocities = _angular_velocities(event)
    velocity_axis = axes[1]
    velocity_axis.plot(velocity_times, velocities, color="tab:orange")
    velocity_axis.axvline(0.0, color="black", alpha=0.45, linewidth=1.0)
    velocity_axis.set_title("Camera Angular Velocity")
    velocity_axis.set_xlabel("time relative to block break [ms]")
    velocity_axis.set_ylabel("velocity [deg/s]")
    velocity_axis.grid(True, alpha=0.3)

    mouse_axis = axes[2]
    mouse_times = [sample.relative_ms for sample in event.mouse_samples]
    mouse_dx = [sample.mouse_dx for sample in event.mouse_samples]
    mouse_dy = [sample.mouse_dy for sample in event.mouse_samples]
    mouse_axis.scatter(mouse_times, mouse_dx, s=12, alpha=0.7, label="mouse dx")
    mouse_axis.scatter(mouse_times, mouse_dy, s=12, alpha=0.7, label="mouse dy")
    mouse_axis.axvline(0.0, color="black", alpha=0.45, linewidth=1.0)
    is_synthetic = source == "minescript-miner-synthetic"
    mouse_axis.set_title(
        "Derived Orientation Steps" if is_synthetic else "Raw Mouse Deltas"
    )
    mouse_axis.set_xlabel("time relative to block break [ms]")
    mouse_axis.set_ylabel("orientation step" if is_synthetic else "accumulated delta")
    mouse_axis.grid(True, alpha=0.3)
    mouse_axis.legend()

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output, dpi=150)
        print(f"Wrote {output}")
    if show:
        plt.show()
    plt.close(figure)


def main() -> None:
    args = parse_args()
    session = load_mining_session(args.session)
    event = next((event for event in session.events if event.event.event_id == args.event_id), None)
    if event is None:
        available = ", ".join(str(item.event.event_id) for item in session.events)
        raise SystemExit(f"event {args.event_id} not found; available event ids: {available}")
    output = args.output or args.session / "analysis" / f"event-{args.event_id}.png"
    source = str(session.metadata.get("source", "minecraft-daq"))
    plot_event(event, output, args.show, source=source)


if __name__ == "__main__":
    main()
