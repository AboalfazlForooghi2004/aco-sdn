from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RoutingEpisode:
    decision: dict[str, Any]
    outcome: dict[str, Any] | None


def load_episodes(
    path: str | Path,
) -> tuple[RoutingEpisode, ...]:
    decisions: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    source = Path(path)
    if not source.exists():
        return ()
    with source.open(encoding="utf-8") as records:
        for line in records:
            try:
                record = json.loads(line)
                decision_id = str(record["decision_id"])
                if record["record_type"] == "decision":
                    decisions[decision_id] = record
                elif record["record_type"] == "outcome":
                    outcomes[decision_id] = record
            except (
                json.JSONDecodeError,
                KeyError,
                TypeError,
            ):
                continue
    return tuple(
        RoutingEpisode(
            decision=decision,
            outcome=outcomes.get(decision_id),
        )
        for decision_id, decision in decisions.items()
    )


class OfflineRoutingEnv:
    """One-step, Gymnasium-style contextual routing environment.

    Action 0 keeps the current path. Action 1 selects the candidate path.
    It intentionally does not mutate a live controller.
    """

    action_space_n = 2

    def __init__(
        self,
        episodes: tuple[RoutingEpisode, ...],
        unsafe_penalty: float = -10.0,
        seed: int = 42,
    ) -> None:
        if not episodes:
            raise ValueError("at least one episode is required")
        self.episodes = episodes
        self.unsafe_penalty = unsafe_penalty
        self._random = random.Random(seed)
        self._current: RoutingEpisode | None = None

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if seed is not None:
            self._random.seed(seed)
        index = (
            int(options["index"])
            if options and "index" in options
            else self._random.randrange(len(self.episodes))
        )
        self._current = self.episodes[index]
        return self._observation(self._current), {
            "episode_index": index,
            "decision_id": self._current.decision[
                "decision_id"
            ],
        }

    def step(
        self, action: int
    ) -> tuple[
        dict[str, Any],
        float,
        bool,
        bool,
        dict[str, Any],
    ]:
        if self._current is None:
            raise RuntimeError("reset must be called before step")
        if action not in (0, 1):
            raise ValueError("action must be 0 (keep) or 1 (candidate)")
        decision = self._current.decision
        safe = bool(decision["simulation_safe"])
        if action == 1 and not safe:
            reward = self.unsafe_penalty
        else:
            key = (
                "candidate_cost"
                if action == 1
                else "current_cost"
            )
            cost = decision.get(key)
            reward = (
                -float(cost)
                if cost is not None
                else self.unsafe_penalty
            )
        outcome = self._current.outcome
        info = {
            "selected_path": decision[
                "candidate_path"
                if action == 1
                else "current_path"
            ],
            "simulation_safe": safe,
            "observed_reward": (
                outcome.get("reward")
                if outcome is not None
                else None
            ),
            "behavior_action": (
                1
                if outcome is not None
                and outcome.get("applied")
                else 0
            ),
        }
        self._current = None
        return {}, reward, True, False, info

    @staticmethod
    def _observation(
        episode: RoutingEpisode,
    ) -> dict[str, Any]:
        decision = episode.decision
        return {
            "source_dpid": decision["source_dpid"],
            "destination_dpid": decision["destination_dpid"],
            "current_cost": decision["current_cost"],
            "candidate_cost": decision["candidate_cost"],
            "simulation_safe": decision["simulation_safe"],
            "link_features": decision["link_features"],
        }