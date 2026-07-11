"""Minescript-Miner backend for paired generated trajectory datasets."""

from __future__ import annotations

import importlib.metadata
import math
from array import array
from dataclasses import asdict, replace
from pathlib import Path

from analysis.mining_session import RecordedMiningEvent, StateSample
from analysis.path_dataset import (
    GenerationCase,
    GeneratedTrajectory,
    PathPoint,
    TargetCondition,
)


class GenerationCaseError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class MinescriptMinerBackend:
    """Adapter around the installed Minescript-Miner Python/native package."""

    def __init__(self, generator: str, config_path: Path | None = None):
        try:
            from minescript_miner import aim
            from minescript_miner.adapter.catalog_contract import MAX_CUBE_SIDE
            from minescript_miner.adapter.native_bridge import (
                TargetMetrics,
                acquire_target_metrics,
            )
            from minescript_miner.adapter.shape_catalog import DEFAULT_CATALOG
        except ImportError as error:
            raise RuntimeError(
                "Minescript-Miner is not importable; install its wheel or use "
                "`python -m pip install -e /path/to/Minescript-Miner`"
            ) from error

        if generator not in aim.IMPLEMENTED_AIM_MODELS:
            raise ValueError(f"unsupported Minescript-Miner generator: {generator}")
        loaded_config = aim.load_aim_config(config_path) if config_path else aim.AimConfig()
        self.config = replace(loaded_config, aim_model=generator)
        self.generator = generator
        self._aim = aim
        self._TargetMetrics = TargetMetrics
        self._acquire_target_metrics = acquire_target_metrics
        self._catalog = DEFAULT_CATALOG
        self._max_cube_side = MAX_CUBE_SIDE

    @property
    def config_metadata(self) -> dict[str, object]:
        return asdict(self.config)

    @property
    def backend_metadata(self) -> dict[str, object]:
        try:
            version = importlib.metadata.version("minescript-miner")
        except importlib.metadata.PackageNotFoundError:
            version = "development"
        return {"package": "minescript-miner", "version": version}

    def angular_step_deg(self, sensitivity: float) -> float:
        return self._aim.sensitivity_to_angular_step_deg(sensitivity)

    @staticmethod
    def _target_orientation(
        eye: tuple[float, float, float],
        hit: tuple[float, float, float],
    ) -> tuple[float, float]:
        dx = hit[0] - eye[0]
        dy = hit[1] - eye[1]
        dz = hit[2] - eye[2]
        horizontal = math.hypot(dx, dz)
        return (
            math.degrees(math.atan2(-dx, dz)),
            math.degrees(math.atan2(-dy, horizontal)),
        )

    @staticmethod
    def _farthest_target_corner_distance(
        eye: tuple[float, float, float],
        block: tuple[int, int, int],
    ) -> float:
        return max(
            math.dist(eye, (x, y, z))
            for x in (block[0], block[0] + 1)
            for y in (block[1], block[1] + 1)
            for z in (block[2], block[2] + 1)
        )

    def prepare_case(
        self,
        source_session_id: str,
        recorded: RecordedMiningEvent,
        *,
        eye_height: float,
        start_sample: StateSample | None = None,
        start_source: str = "window_first_sample",
    ) -> GenerationCase:
        if not recorded.state_samples:
            raise GenerationCaseError("missing_state_samples", "event has no state samples")
        event = recorded.event
        if event.hit_x is None or event.hit_y is None or event.hit_z is None:
            raise GenerationCaseError("missing_hit_point", "event has no hit point")

        start = start_sample or recorded.state_samples[0]
        numeric_context = (
            start.yaw, start.pitch, start.player_x, start.player_y,
            start.player_z, start.sensitivity,
        )
        if not all(math.isfinite(value) for value in numeric_context):
            raise GenerationCaseError("invalid_start_sample", "start sample is not finite")
        if not 0.0 <= start.sensitivity <= 1.0:
            raise GenerationCaseError("invalid_sensitivity", "sensitivity is outside [0, 1]")

        eye = (start.player_x, start.player_y + eye_height, start.player_z)
        hit = (event.hit_x, event.hit_y, event.hit_z)
        target_block = (event.target_x, event.target_y, event.target_z)
        target_orientation = self._target_orientation(eye, hit)

        center = tuple(math.floor(value) for value in eye)
        half = max(
            abs(target_block[index] - center[index]) for index in range(3)
        ) + 1
        side = half * 2 + 1
        if side > self._max_cube_side:
            raise GenerationCaseError(
                "target_outside_solver_range",
                f"reconstruction requires side={side}, maximum is {self._max_cube_side}",
            )
        min_pos = tuple(center[index] - half for index in range(3))
        block_count = side**3
        block_states = ["minecraft:air"] * block_count

        def block_index(position: tuple[int, int, int]) -> int | None:
            offsets = tuple(position[index] - min_pos[index] for index in range(3))
            if any(offset < 0 or offset >= side for offset in offsets):
                return None
            return offsets[0] + offsets[2] * side + offsets[1] * side * side

        target_index = block_index(target_block)
        if target_index is None:
            raise GenerationCaseError("target_outside_solver_range", "target is outside cube")
        block_states[target_index] = event.block_state_before
        neighbor_states: list[tuple[int, int, int, str]] = []
        for neighbor in event.neighbors:
            try:
                dx = int(neighbor["dx"])
                dy = int(neighbor["dy"])
                dz = int(neighbor["dz"])
                state = str(neighbor["state"])
            except (KeyError, TypeError, ValueError) as error:
                raise GenerationCaseError(
                    "invalid_neighbors", "neighbors_json contains an invalid entry"
                ) from error
            position = (
                target_block[0] + dx,
                target_block[1] + dy,
                target_block[2] + dz,
            )
            index = block_index(position)
            if index is not None:
                block_states[index] = state
            neighbor_states.append((dx, dy, dz, state))

        encoded = self._catalog.encode_region(side, block_states)
        reconstructed = self._acquire_target_metrics(
            eye,
            target_orientation,
            encoded.shape_catalog_version,
            encoded.side,
            self._farthest_target_corner_distance(eye, target_block),
            encoded.shape_ids,
            array("H", [target_index]),
        )
        if reconstructed is None:
            raise GenerationCaseError(
                "local_target_not_visible", "local reconstruction found no visible target"
            )

        angular_step = self._aim.sensitivity_to_angular_step_deg(start.sensitivity)
        distance = math.dist(eye, hit)
        target_metrics = self._TargetMetrics(
            yaw=target_orientation[0],
            pitch=target_orientation[1],
            width_yaw=reconstructed.width_yaw,
            width_pitch=reconstructed.width_pitch,
            distance=distance,
            target_block=target_block,
            face_id=event.face_id,
            hit_point=hit,
            block_state_before=event.block_state_before,
            neighbors=tuple(neighbor_states),
        )
        return GenerationCase(
            source_session_id=source_session_id,
            source_event=event,
            start_sample=start,
            target=TargetCondition(
                yaw=target_metrics.yaw,
                pitch=target_metrics.pitch,
                width_yaw=target_metrics.width_yaw,
                width_pitch=target_metrics.width_pitch,
                distance=target_metrics.distance,
                width_source="local_26_neighbor_reconstruction",
            ),
            angular_step_deg=angular_step,
            start_source=start_source,
        )

    def generate(
        self,
        case: GenerationCase,
        *,
        replicate_index: int,
        replicate_count: int,
        seed: int,
    ) -> GeneratedTrajectory:
        target = self._TargetMetrics(
            yaw=case.target.yaw,
            pitch=case.target.pitch,
            width_yaw=case.target.width_yaw,
            width_pitch=case.target.width_pitch,
            distance=case.target.distance,
        )
        points = self._aim.generate_aim_path(
            (case.start_sample.yaw, case.start_sample.pitch),
            target,
            self.config,
            angular_step_deg=case.angular_step_deg,
            seed=seed,
        )
        return GeneratedTrajectory(
            case=case,
            generator=self.generator,
            replicate_index=replicate_index,
            replicate_count=replicate_count,
            seed=seed,
            points=tuple(PathPoint(point.yaw, point.pitch, point.t_ms) for point in points),
        )
