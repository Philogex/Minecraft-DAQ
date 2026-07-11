#!/usr/bin/env python3
"""Generate paired path-generator datasets from recorded human DAQ events."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root = str(PROJECT_ROOT)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from analysis.minescript_miner_backend import GenerationCaseError, MinescriptMinerBackend
from analysis.mining_session import load_mining_session
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
    )
    print(
        f"Wrote {args.output.resolve()} with {len(trajectories)} trajectories "
        f"from {len(trajectories) // args.replicates} source events."
    )
    if skipped:
        print("Skipped: " + ", ".join(f"{key}={value}" for key, value in sorted(skipped.items())))


if __name__ == "__main__":
    main()
