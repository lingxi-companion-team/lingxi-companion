import pytest

from common.perception_types import EmotionLabel, EnvContext, PerceptionResult
from fusion.fusion_engine import majority_label
from fusion.temporal_smoother import TemporalSmoother


def test_perception_contract_validates_ranges() -> None:
    result = PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.9, "mock")
    assert result.agent_id == "mock"
    with pytest.raises(ValueError):
        PerceptionResult(EmotionLabel.FOCUSED, 1.1, 0.9, "mock")


def test_environment_contract_validates_ranges() -> None:
    context = EnvContext(0.8, 0.2, 0.7)
    assert context.env_score == 0.7


def test_majority_label() -> None:
    results = [
        PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.8, "a"),
        PerceptionResult(EmotionLabel.FOCUSED, 0.7, 0.7, "b"),
        PerceptionResult(EmotionLabel.CONFUSED, 0.6, 0.6, "c"),
    ]
    assert majority_label(results) == EmotionLabel.FOCUSED


def test_three_sample_smoother() -> None:
    smoother = TemporalSmoother()
    assert smoother.update(EmotionLabel.FOCUSED) is None
    assert smoother.update(EmotionLabel.CONFUSED) is None
    assert smoother.update(EmotionLabel.FOCUSED) == EmotionLabel.FOCUSED
