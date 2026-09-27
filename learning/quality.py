from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DatasetQualityReport:
    total_lines: int
    valid_records: int
    invalid_records: int
    decisions: int
    outcomes: int
    completed_episodes: int
    pending_outcomes: int
    orphan_outcomes: int
    duplicate_decisions: int
    duplicate_outcomes: int
    outcome_coverage: float
    unsafe_candidate_rate: float
    average_link_count: float
    unique_topology_shapes: int


def inspect_dataset(
    path: str | Path,
) -> DatasetQualityReport:
    decisions: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    total_lines = 0
    invalid = 0
    duplicate_decisions = 0
    duplicate_outcomes = 0
    source = Path(path)
    if source.exists():
        with source.open(encoding="utf-8") as records:
            for line in records:
                total_lines += 1
                try:
                    record = json.loads(line)
                    decision_id = str(record["decision_id"])
                    record_type = record["record_type"]
                    if record_type == "decision":
                        if decision_id in decisions:
                            duplicate_decisions += 1
                        decisions[decision_id] = record
                    elif record_type == "outcome":
                        if decision_id in outcomes:
                            duplicate_outcomes += 1
                        outcomes[decision_id] = record
                    else:
                        invalid += 1
                except (
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                ):
                    invalid += 1

    decision_ids = set(decisions)
    outcome_ids = set(outcomes)
    completed = decision_ids & outcome_ids
    link_counts = [
        len(record.get("link_features", ()))
        for record in decisions.values()
    ]
    shapes = {
        (
            len(
                {
                    int(link["source"])
                    for link in record.get(
                        "link_features", ()
                    )
                }
                | {
                    int(link["target"])
                    for link in record.get(
                        "link_features", ()
                    )
                }
            ),
            len(record.get("link_features", ())),
        )
        for record in decisions.values()
    }
    unsafe = sum(
        1
        for record in decisions.values()
        if not bool(record.get("simulation_safe"))
    )
    return DatasetQualityReport(
        total_lines=total_lines,
        valid_records=total_lines - invalid,
        invalid_records=invalid,
        decisions=len(decisions),
        outcomes=len(outcomes),
        completed_episodes=len(completed),
        pending_outcomes=len(decision_ids - outcome_ids),
        orphan_outcomes=len(outcome_ids - decision_ids),
        duplicate_decisions=duplicate_decisions,
        duplicate_outcomes=duplicate_outcomes,
        outcome_coverage=(
            len(completed) / len(decisions)
            if decisions
            else 0.0
        ),
        unsafe_candidate_rate=(
            unsafe / len(decisions) if decisions else 0.0
        ),
        average_link_count=(
            sum(link_counts) / len(link_counts)
            if link_counts
            else 0.0
        ),
        unique_topology_shapes=len(shapes),
    )