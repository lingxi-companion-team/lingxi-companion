from __future__ import annotations

from collections.abc import Iterator

from common.perception_types import EmotionLabel, EnvContext, PerceptionResult


def sample_stream() -> Iterator[tuple[list[PerceptionResult], EnvContext]]:
    """Yield deterministic scenarios for local integration and unit tests."""

    yield (
        [
            PerceptionResult(EmotionLabel.FOCUSED, 0.90, 0.90, "expression"),
            PerceptionResult(EmotionLabel.FOCUSED, 0.85, 0.85, "behavior"),
        ],
        EnvContext(brightness=0.90, blur=0.10, env_score=0.90),
    )
    yield (
        [
            PerceptionResult(EmotionLabel.CONFUSED, 0.55, 0.55, "expression"),
            PerceptionResult(EmotionLabel.DISTRACTED, 0.52, 0.52, "behavior"),
        ],
        EnvContext(brightness=0.35, blur=0.45, env_score=0.35),
    )
