from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .environment import OfflineRoutingEnv


class ShadowPolicy(Protocol):
    def select_action(
        self, observation: dict[str, Any]
    ) -> int: ...


class AlwaysKeepPolicy:
    def select_action(
        self, observation: dict[str, Any]
    ) -> int:
        return 0


class GreedySafePolicy:
    def select_action(
        self, observation: dict[str, Any]
    ) -> int:
        current = observation["current_cost"]
        candidate = observation["candidate_cost"]
        return int(
            observation["simulation_safe"]
            and (
                current is None
                or float(candidate) < float(current)
            )
        )


@dataclass(frozen=True, slots=True)
class ShadowEvaluation:
    episodes: int
    average_reward: float
    candidate_selection_rate: float
    unsafe_action_rate: float


class ShadowEvaluator:
    """Evaluate a policy without installing any OpenFlow rule."""

    def evaluate(
        self,
        env: OfflineRoutingEnv,
        policy: ShadowPolicy,
    ) -> ShadowEvaluation:
        rewards = []
        candidates = 0
        unsafe = 0
        for index in range(len(env.episodes)):
            observation, _ = env.reset(
                options={"index": index}
            )
            action = policy.select_action(observation)
            if action == 1:
                candidates += 1
                if not observation["simulation_safe"]:
                    unsafe += 1
            _, reward, _, _, _ = env.step(action)
            rewards.append(reward)
        total = len(rewards)
        return ShadowEvaluation(
            episodes=total,
            average_reward=sum(rewards) / total,
            candidate_selection_rate=candidates / total,
            unsafe_action_rate=unsafe / total,
        )