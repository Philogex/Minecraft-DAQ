"""Read Minecraft DAQ mining recordings into event-oriented Python objects."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TypeVar


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MiningEvent:
    session_id: str
    event_id: int
    event_time_ns: int
    target_x: int
    target_y: int
    target_z: int
    face_id: str | None
    hit_x: float | None
    hit_y: float | None
    hit_z: float | None
    block_state_before: str
    block_state_after: str
    neighbors: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class StateSample:
    relative_ms: float
    yaw: float
    pitch: float
    player_x: float
    player_y: float
    player_z: float
    fov: float
    gui_scale: int
    fps_estimate: float
    sensitivity: float


@dataclass(frozen=True)
class MouseDeltaSample:
    relative_ms: float
    mouse_dx: float
    mouse_dy: float


@dataclass(frozen=True)
class RecordedMiningEvent:
    event: MiningEvent
    state_samples: tuple[StateSample, ...]
    mouse_samples: tuple[MouseDeltaSample, ...]


@dataclass(frozen=True)
class MiningSession:
    path: Path
    session_id: str
    events: tuple[RecordedMiningEvent, ...]
    metadata: Mapping[str, object]


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"missing DAQ recording file: {path}")
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _read_metadata(path: Path) -> Mapping[str, object]:
    metadata_path = path / "metadata.json"
    if not metadata_path.is_file():
        return {}
    try:
        with metadata_path.open("r", encoding="utf-8") as file:
            metadata = json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid metadata JSON in {metadata_path}") from error
    if not isinstance(metadata, dict):
        raise ValueError(f"metadata root must be an object in {metadata_path}")
    return metadata


def _schema_version(row: dict[str, str], path: Path) -> None:
    try:
        version = int(row["schema_version"])
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid schema_version in {path}") from error
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported DAQ schema version {version} in {path}; "
            f"expected {SCHEMA_VERSION}"
        )


def _optional_float(row: dict[str, str], name: str) -> float | None:
    value = row.get(name, "")
    return float(value) if value else None


def _parse_event(row: dict[str, str], path: Path) -> MiningEvent:
    _schema_version(row, path)
    try:
        neighbors = json.loads(row["neighbors_json"])
        if not isinstance(neighbors, list):
            raise ValueError("neighbors_json is not an array")
        return MiningEvent(
            session_id=row["session_id"],
            event_id=int(row["event_id"]),
            event_time_ns=int(row["event_time_ns"]),
            target_x=int(row["target_x"]),
            target_y=int(row["target_y"]),
            target_z=int(row["target_z"]),
            face_id=row.get("face_id") or None,
            hit_x=_optional_float(row, "hit_x"),
            hit_y=_optional_float(row, "hit_y"),
            hit_z=_optional_float(row, "hit_z"),
            block_state_before=row["block_state_before"],
            block_state_after=row["block_state_after"],
            neighbors=tuple(neighbors),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid event row in {path}") from error


def _parse_state_sample(row: dict[str, str], path: Path) -> tuple[int, str, StateSample]:
    _schema_version(row, path)
    try:
        return (
            int(row["event_id"]),
            row["session_id"],
            StateSample(
                relative_ms=float(row["relative_ms"]),
                yaw=float(row["yaw"]),
                pitch=float(row["pitch"]),
                player_x=float(row["player_x"]),
                player_y=float(row["player_y"]),
                player_z=float(row["player_z"]),
                fov=float(row["fov"]),
                gui_scale=int(row["gui_scale"]),
                fps_estimate=float(row["fps_estimate"]),
                sensitivity=float(row["sensitivity"]),
            ),
        )
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid state sample row in {path}") from error


def _parse_mouse_sample(
    row: dict[str, str], path: Path
) -> tuple[int, str, MouseDeltaSample]:
    _schema_version(row, path)
    try:
        return (
            int(row["event_id"]),
            row["session_id"],
            MouseDeltaSample(
                relative_ms=float(row["relative_ms"]),
                mouse_dx=float(row["mouse_dx"]),
                mouse_dy=float(row["mouse_dy"]),
            ),
        )
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid mouse sample row in {path}") from error


Sample = TypeVar("Sample")


def _group_samples(
    rows: list[dict[str, str]],
    path: Path,
    parser: Callable[[dict[str, str], Path], tuple[int, str, Sample]],
) -> tuple[dict[int, list[Sample]], set[str]]:
    samples_by_event: dict[int, list[Sample]] = {}
    session_ids: set[str] = set()
    for row in rows:
        event_id, session_id, sample = parser(row, path)
        session_ids.add(session_id)
        samples_by_event.setdefault(event_id, []).append(sample)
    if len(session_ids) > 1:
        raise ValueError(f"multiple session ids in {path}")
    for samples in samples_by_event.values():
        samples.sort(key=lambda sample: sample.relative_ms)
    return samples_by_event, session_ids


def load_mining_session(path: Path) -> MiningSession:
    """Load one `mining-*` directory written by Minecraft DAQ."""

    path = path.resolve()
    events_path = path / "events.csv"
    state_path = path / "state_samples.csv"
    mouse_path = path / "mouse_trajectory.csv"

    events = [_parse_event(row, events_path) for row in _read_rows(events_path)]
    if not events:
        raise ValueError(f"DAQ recording contains no events: {path}")
    session_ids = {event.session_id for event in events}
    if len(session_ids) != 1:
        raise ValueError(f"multiple session ids in {events_path}")
    event_ids = {event.event_id for event in events}
    if len(event_ids) != len(events):
        raise ValueError(f"duplicate event ids in {events_path}")
    session_id = events[0].session_id

    state_samples, state_session_ids = _group_samples(
        _read_rows(state_path), state_path, _parse_state_sample
    )
    mouse_samples, mouse_session_ids = _group_samples(
        _read_rows(mouse_path), mouse_path, _parse_mouse_sample
    )
    unknown_ids = (set(state_samples) | set(mouse_samples)) - event_ids
    if unknown_ids:
        raise ValueError(f"samples reference unknown event ids in {path}: {sorted(unknown_ids)}")
    if state_session_ids - {session_id}:
        raise ValueError(f"session id mismatch in {state_path}")
    if mouse_session_ids - {session_id}:
        raise ValueError(f"session id mismatch in {mouse_path}")

    return MiningSession(
        path=path,
        session_id=session_id,
        events=tuple(
            RecordedMiningEvent(
                event=event,
                state_samples=tuple(state_samples.get(event.event_id, ())),
                mouse_samples=tuple(mouse_samples.get(event.event_id, ())),
            )
            for event in sorted(events, key=lambda event: event.event_id)
        ),
        metadata=_read_metadata(path),
    )
