"""Common records and batch writer for generated trajectory datasets."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from analysis.mining_session import MiningEvent, StateSample


SCHEMA_VERSION = 1
METADATA_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PathPoint:
    yaw: float
    pitch: float
    t_ms: float


@dataclass(frozen=True)
class TargetCondition:
    yaw: float
    pitch: float
    width_yaw: float
    width_pitch: float
    distance: float
    width_source: str


@dataclass(frozen=True)
class GenerationCase:
    source_session_id: str
    source_event: MiningEvent
    start_sample: StateSample
    target: TargetCondition
    angular_step_deg: float


@dataclass(frozen=True)
class GeneratedTrajectory:
    case: GenerationCase
    generator: str
    replicate_index: int
    replicate_count: int
    seed: int
    points: tuple[PathPoint, ...]


def deterministic_seed(
    source_session_id: str,
    source_event_id: int,
    generator: str,
    replicate_index: int,
) -> int:
    identity = (
        f"{source_session_id}:{source_event_id}:{generator}:{replicate_index}"
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(identity).digest()[:8], "big")


def _shortest_yaw_delta(source: float, target: float) -> float:
    return ((target - source + 180.0) % 360.0) - 180.0


def _session_id(
    source_session_id: str,
    generator: str,
    generator_config: Mapping[str, object],
) -> str:
    payload = json.dumps(
        {
            "source_session_id": source_session_id,
            "generator": generator,
            "generator_config": generator_config,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_generated_dataset(
    output_directory: Path,
    trajectories: Sequence[GeneratedTrajectory],
    *,
    source_session_path: Path,
    generator_config: Mapping[str, object],
    backend_metadata: Mapping[str, object],
    skipped_reasons: Mapping[str, int],
) -> Path:
    if not trajectories:
        raise ValueError("cannot write a generated dataset without trajectories")

    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    first = trajectories[0]
    session_id = _session_id(
        first.case.source_session_id,
        first.generator,
        generator_config,
    )

    event_fields = (
        "schema_version", "session_id", "event_id", "event_time_ns",
        "target_x", "target_y", "target_z", "face_id", "hit_x", "hit_y",
        "hit_z", "block_state_before", "block_state_after", "neighbors_json",
    )
    state_fields = (
        "schema_version", "session_id", "event_id", "sample_time_ns",
        "event_time_ns", "relative_ms", "yaw", "pitch", "player_x",
        "player_y", "player_z", "fov", "gui_scale", "fps_estimate",
        "sensitivity",
    )
    mouse_fields = (
        "schema_version", "session_id", "event_id", "sample_time_ns",
        "event_time_ns", "relative_ms", "mouse_dx", "mouse_dy",
    )

    event_metadata: list[dict[str, object]] = []
    with (
        (output_directory / "events.csv").open("w", encoding="utf-8", newline="") as event_file,
        (output_directory / "state_samples.csv").open("w", encoding="utf-8", newline="") as state_file,
        (output_directory / "mouse_trajectory.csv").open("w", encoding="utf-8", newline="") as mouse_file,
    ):
        event_writer = csv.DictWriter(event_file, fieldnames=event_fields)
        state_writer = csv.DictWriter(state_file, fieldnames=state_fields)
        mouse_writer = csv.DictWriter(mouse_file, fieldnames=mouse_fields)
        event_writer.writeheader()
        state_writer.writeheader()
        mouse_writer.writeheader()

        for generated_event_id, trajectory in enumerate(trajectories, start=1):
            source = trajectory.case.source_event
            hit_point = (source.hit_x, source.hit_y, source.hit_z)
            event_writer.writerow(
                {
                    "schema_version": SCHEMA_VERSION,
                    "session_id": session_id,
                    "event_id": generated_event_id,
                    "event_time_ns": source.event_time_ns,
                    "target_x": source.target_x,
                    "target_y": source.target_y,
                    "target_z": source.target_z,
                    "face_id": source.face_id or "",
                    "hit_x": "" if hit_point[0] is None else hit_point[0],
                    "hit_y": "" if hit_point[1] is None else hit_point[1],
                    "hit_z": "" if hit_point[2] is None else hit_point[2],
                    "block_state_before": source.block_state_before,
                    "block_state_after": source.block_state_after,
                    "neighbors_json": json.dumps(source.neighbors, separators=(",", ":")),
                }
            )

            final_t_ms = trajectory.points[-1].t_ms
            start = trajectory.case.start_sample
            for point in trajectory.points:
                relative_ms = point.t_ms - final_t_ms
                state_writer.writerow(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "session_id": session_id,
                        "event_id": generated_event_id,
                        "sample_time_ns": source.event_time_ns + round(relative_ms * 1_000_000),
                        "event_time_ns": source.event_time_ns,
                        "relative_ms": relative_ms,
                        "yaw": point.yaw,
                        "pitch": point.pitch,
                        "player_x": start.player_x,
                        "player_y": start.player_y,
                        "player_z": start.player_z,
                        "fov": start.fov,
                        "gui_scale": start.gui_scale,
                        "fps_estimate": start.fps_estimate,
                        "sensitivity": start.sensitivity,
                    }
                )

            for previous, current in zip(trajectory.points, trajectory.points[1:]):
                relative_ms = current.t_ms - final_t_ms
                mouse_writer.writerow(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "session_id": session_id,
                        "event_id": generated_event_id,
                        "sample_time_ns": source.event_time_ns + round(relative_ms * 1_000_000),
                        "event_time_ns": source.event_time_ns,
                        "relative_ms": relative_ms,
                        "mouse_dx": _shortest_yaw_delta(previous.yaw, current.yaw)
                        / trajectory.case.angular_step_deg,
                        "mouse_dy": (current.pitch - previous.pitch)
                        / trajectory.case.angular_step_deg,
                    }
                )

            event_metadata.append(
                {
                    "generated_event_id": generated_event_id,
                    "source_event_id": source.event_id,
                    "replicate_index": trajectory.replicate_index,
                    "replicate_count": trajectory.replicate_count,
                    "analysis_weight": 1.0 / trajectory.replicate_count,
                    "seed": trajectory.seed,
                    "start_source": "window_first_sample",
                    "target_condition": {
                        "yaw": trajectory.case.target.yaw,
                        "pitch": trajectory.case.target.pitch,
                        "width_yaw": trajectory.case.target.width_yaw,
                        "width_pitch": trajectory.case.target.width_pitch,
                        "distance": trajectory.case.target.distance,
                        "width_source": trajectory.case.target.width_source,
                    },
                }
            )

    metadata = {
        "metadata_schema_version": METADATA_SCHEMA_VERSION,
        "source": "minescript-miner-generated",
        "conditioned_on_human_target": True,
        "evaluates_target_selection": False,
        "source_session_id": first.case.source_session_id,
        "source_session_path": str(source_session_path.resolve()),
        "generator": first.generator,
        "generator_config": generator_config,
        "backend": backend_metadata,
        "generated_event_count": len(trajectories),
        "skipped_reasons": dict(skipped_reasons),
        "field_sources": {
            "target_and_world_context": "human DAQ event",
            "camera_orientation": "path generator",
            "player_and_client_context": "human DAQ start sample",
            "mouse_dx_dy": "derived orientation-step equivalents",
        },
        "events": event_metadata,
    }
    with (output_directory / "metadata.json").open("w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2, allow_nan=False)
        file.write("\n")
    return output_directory
