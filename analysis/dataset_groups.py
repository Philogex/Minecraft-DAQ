"""Shared command-line grouping for one or more analysis sessions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class DatasetGroup:
    label: str
    sessions: tuple[Path, ...]


def add_dataset_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "sessions",
        nargs="*",
        type=Path,
        help="Legacy shorthand: one independently plotted dataset per session.",
    )
    parser.add_argument(
        "--label",
        action="append",
        dest="labels",
        help="Legacy dataset label; repeat once per positional session.",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        nargs="+",
        metavar="LABEL_OR_SESSION",
        help=(
            "Grouped dataset: label followed by one or more session paths. "
            "Repeat --dataset for additional plotted datasets."
        ),
    )


def resolve_dataset_groups(
    sessions: Sequence[Path],
    labels: Sequence[str] | None,
    dataset_specs: Sequence[Sequence[str]] | None,
) -> tuple[DatasetGroup, ...]:
    """Resolve the legacy positional form or explicit grouped datasets."""

    if dataset_specs:
        if sessions or labels:
            raise SystemExit(
                "do not combine positional sessions/--label with --dataset"
            )
        groups: list[DatasetGroup] = []
        labels_seen: set[str] = set()
        for specification in dataset_specs:
            if len(specification) < 2:
                raise SystemExit(
                    "each --dataset requires a label and at least one session"
                )
            label = specification[0]
            if not label:
                raise SystemExit("dataset labels must not be empty")
            if label in labels_seen:
                raise SystemExit(f"duplicate --dataset label: {label}")
            labels_seen.add(label)
            groups.append(
                DatasetGroup(
                    label=label,
                    sessions=tuple(Path(value) for value in specification[1:]),
                )
            )
        return tuple(groups)

    if not sessions:
        raise SystemExit("provide positional sessions or at least one --dataset")
    if labels is not None and len(labels) != len(sessions):
        raise SystemExit("repeat --label exactly once per positional session")
    resolved_labels = labels or [path.name for path in sessions]
    return tuple(
        DatasetGroup(label, (path,))
        for label, path in zip(resolved_labels, sessions)
    )
