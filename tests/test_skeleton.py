"""骨架冒烟测试。

保留最早的契约与端到端断言，确保 ``pytest -q`` 在任何阶段都能跑通。
更细的模块测试见 ``tests/fusion/``、``tests/agents/``、``tests/pipeline/``。
"""

from __future__ import annotations

import pytest

from common.mock.generator import sample_stream
from common.perception_types import EmotionLabel, EnvContext, PerceptionResult
from fusion.fusion_engine import FusionEngine, majority_label
from fusion.temporal_smoother import TemporalSmoother


def test_perception_contract_validates_ranges() -> None:
    result = PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.9, "expression")
    assert result.agent_id == "expression"
    with pytest.raises(ValueError):
        PerceptionResult(EmotionLabel.FOCUSED, 1.1, 0.9, "expression")


def test_environment_contract_validates_ranges() -> None:
    context = EnvContext(0.8, 0.2, 0.7)
    assert context.env_score == 0.7


def test_majority_label_compat() -> None:
    """兼容函数仍在（供旧调用方），但主流程已改用 FusionEngine。"""
    results = [
        PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.8, "expression"),
        PerceptionResult(EmotionLabel.FOCUSED, 0.7, 0.7, "behavior"),
        PerceptionResult(EmotionLabel.CONFUSED, 0.6, 0.6, "env"),
    ]
    assert majority_label(results) == EmotionLabel.FOCUSED
    assert majority_label([]) == EmotionLabel.UNKNOWN


def test_three_sample_smoother() -> None:
    """窗口未填满时不确认；三帧 [F,C,F] 确认 F。"""
    smoother = TemporalSmoother()
    assert smoother.update(EmotionLabel.FOCUSED) is None
    assert smoother.update(EmotionLabel.CONFUSED) is None
    state = smoother.update(EmotionLabel.FOCUSED)
    assert state is not None
    assert state.label == EmotionLabel.FOCUSED
    assert state.stale is False


def test_mock_pipeline_end_to_end() -> None:
    """M1 完成标志的冒烟版：mock 输入 → 融合 → 平滑。"""
    engine = FusionEngine()
    smoother = TemporalSmoother()

    frames = list(sample_stream())
    assert len(frames) >= 4, "mock 场景应覆盖至少 4 类情形"

    for results, env in frames:
        fused = engine.fuse(results, env)
        smoother.update(fused.label, confidence=fused.confidence, ts=fused.ts)

    assert smoother.stable_label is not None
