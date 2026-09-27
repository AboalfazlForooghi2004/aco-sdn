from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .environment import OfflineRoutingEnv
from .quality import DatasetQualityReport


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


@dataclass(frozen=True, slots=True)
class PromotionAssessment:
    eligible: bool
    violations: tuple[str, ...]
    reward_improvement: float


@dataclass(frozen=True, slots=True)
class PromotionGate:
    min_episodes: int = 100
    min_outcome_coverage: float = 0.95
    max_unsafe_action_rate: float = 0.0
    min_reward_improvement: float = 0.0

    def assess(
        self,
        *,
        candidate: ShadowEvaluation,
        baseline: ShadowEvaluation,
        quality: DatasetQualityReport,
    ) -> PromotionAssessment:
        violations = []
        improvement = (
            candidate.average_reward - baseline.average_reward
        )
        if candidate.episodes < self.min_episodes:
            violations.append("insufficient_episodes")
        if (
            quality.outcome_coverage
            < self.min_outcome_coverage
        ):
            violations.append("insufficient_outcome_coverage")
        if (
            candidate.unsafe_action_rate
            > self.max_unsafe_action_rate
        ):
            violations.append("unsafe_action_rate_exceeded")
        if improvement < self.min_reward_improvement:
            violations.append("reward_improvement_too_low")
        return PromotionAssessment(
            eligible=not violations,
            violations=tuple(violations),
            reward_improvement=improvement,
        )