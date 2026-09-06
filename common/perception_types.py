from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class EmotionLabel(str, Enum):
    """Unified labels shared by all perception agents."""

    FOCUSED = "focused"
    CONFUSED = "confused"
    DISTRACTED = "distracted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PerceptionResult:
    """Output produced by a single perception agent."""

    label: EmotionLabel
    prob: float
    confidence: float
    agent_id: str

    def __post_init__(self) -> None:
        for name, value in (("prob", self.prob), ("confidence", self.confidence)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")


@dataclass(frozen=True)
class EnvContext:
    """Environment quality signals used by the fusion layer."""

    brightness: float
    blur: float
    env_score: float

    def __post_init__(self) -> None:
        for name, value in (
            ("brightness", self.brightness),
            ("blur", self.blur),
            ("env_score", self.env_score),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class FusionOutput:
    """Instantaneous result before temporal smoothing."""

    label: EmotionLabel
    confidence: float
    weights: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalState:
    """Stable result exposed to the application layer."""

    label: EmotionLabel
    timestamp: float
