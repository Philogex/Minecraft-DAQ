#!/usr/bin/env python3
"""Generate paired path-generator datasets from recorded human DAQ events."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.aim_features import AimPoint
from analysis.minescript_miner_backend import GenerationCaseError, MinescriptMinerBackend
from analysis.mining_session import load_mining_session
from analysis.movement_segmentation import (
    MovementSegmentationConfig,
    segment_target_movement,
)
from analysis.path_density import AngularTarget
from analysis.path_dataset import deterministic_seed, write_generated_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reproducible path-generator trajectories conditioned on "
            "the target and initial state of human DAQ mining events."
        )
    )
    parser.add_argument("session", type=Path, help="Human DAQ mining-* directory.")
    parser.add_argument("output", type=Path, help="New generated dataset directory.")
    parser.add_argument("--generator", default="sigmadrift")
    parser.add_argument("--replicates", type=int, default=5)
    parser.add_argument("--config", type=Path, help="Minescript-Miner aim_config.txt.")
    parser.add_argument("--eye-height", type=float, default=1.62)
    parser.add_argument("--max-events", type=int, default=0)
    parser.add_argument(
        "--no-segmentation",
        action="store_true",
        help="Use the first 1.5 s window sample instead of detected movement onset.",
    )
    parser.add_argument("--max-idle-gap-ms", type=float, default=150.0)
    parser.add_argument("--minimum-motion-ratio", type=float, default=0.1)
    parser.add_argument("--max-player-displacement", type=float, default=0.05)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.replicates <= 0:
        raise SystemExit("--replicates must be positive")
    if args.max_events < 0:
        raise SystemExit("--max-events must be non-negative")

    session = load_mining_session(args.session)
    backend = MinescriptMinerBackend(args.generator, args.config)
    skipped: Counter[str] = Counter()
    trajectories = []
    segmentation_config = MovementSegmentationConfig(
        max_idle_gap_ms=args.max_idle_gap_ms,
        minimum_motion_ratio=args.minimum_motion_ratio,
        max_player_displacement=args.max_player_displacement,
    )
    source_events = session.events[: args.max_events or None]
    for recorded in source_events:
        try:
            case = backend.prepare_case(
                session.session_id,
                recorded,
                eye_height=args.eye_height,
            )
        except GenerationCaseError as error:
            skipped[error.reason] += 1
            continue
        if not args.no_segmentation:
            points = tuple(
                AimPoint(sample.yaw, sample.pitch, sample.relative_ms)
                for sample in recorded.state_samples
            )
            segmentation = segment_target_movement(
                points,
                AngularTarget(
                    yaw=case.target.yaw,
                    pitch=case.target.pitch,
                    width_yaw=case.target.width_yaw,
                    width_pitch=case.target.width_pitch,
                ),
                angular_step_deg=case.angular_step_deg,
                player_positions=tuple(
                    (sample.player_x, sample.player_y, sample.player_z)
                    for sample in recorded.state_samples
                ),
                config=segmentation_config,
            )
            if segmentation.segment is None:
                skipped[segmentation.reason or "unknown_segmentation_failure"] += 1
                continue
            try:
                case = backend.prepare_case(
                    session.session_id,
                    recorded,
                    eye_height=args.eye_height,
                    start_sample=recorded.state_samples[
                        segmentation.segment.start_index
                    ],
                    start_source="detected_movement_onset",
                )
            except GenerationCaseError as error:
                skipped[error.reason] += 1
                continue
        for replicate_index in range(args.replicates):
            seed = deterministic_seed(
                session.session_id,
                recorded.event.event_id,
                backend.generator,
                replicate_index,
            )
            trajectories.append(
                backend.generate(
                    case,
                    replicate_index=replicate_index,
                    replicate_count=args.replicates,
                    seed=seed,
                )
            )

    if not trajectories:
        reasons = ", ".join(f"{name}={count}" for name, count in sorted(skipped.items()))
        raise SystemExit(f"no generator trajectories produced ({reasons or 'no events'})")
    write_generated_dataset(
        args.output,
        trajectories,
        source_session_path=args.session,
        generator_config=backend.config_metadata,
        backend_metadata=backend.backend_metadata,
        skipped_reasons=skipped,
        preprocessing_metadata={
            "movement_segmentation": {
                "enabled": not args.no_segmentation,
                "config": asdict(segmentation_config),
            }
        },
    )
    print(
        f"Wrote {args.output.resolve()} with {len(trajectories)} trajectories "
        f"from {len(trajectories) // args.replicates} source events."
    )
    if skipped:
        print("Skipped: " + ", ".join(f"{key}={value}" for key, value in sorted(skipped.items())))


if __name__ == "__main__":
    main()
