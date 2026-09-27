"""Safe offline-learning and shadow-evaluation primitives."""

from .dataset import LearningDataset
from .encoder import GraphObservation, GraphObservationEncoder
from .environment import OfflineRoutingEnv, load_episodes
from .quality import DatasetQualityReport, inspect_dataset
from .shadow import (
    AlwaysKeepPolicy,
    GreedySafePolicy,
    PromotionAssessment,
    PromotionGate,
    ShadowEvaluation,
    ShadowEvaluator,
)

__all__ = [
    "LearningDataset",
    "GraphObservation",
    "GraphObservationEncoder",
    "OfflineRoutingEnv",
    "load_episodes",
    "DatasetQualityReport",
    "inspect_dataset",
    "AlwaysKeepPolicy",
    "GreedySafePolicy",
    "PromotionAssessment",
    "PromotionGate",
    "ShadowEvaluation",
    "ShadowEvaluator",
]