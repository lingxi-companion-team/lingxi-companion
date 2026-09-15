"""契约层测试：字段完整性、范围校验、命名唯一性。

这些断言的作用是**防止契约被无意改坏**。任何人在合并前跑一次，
就能发现自己的改动是否破坏了三人共用的接口。
"""

from __future__ import annotations

import dataclasses

import pytest

from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_ENV,
    AGENT_EXPRESSION,
    AGENT_IDS,
    EMOTION_LABELS,
    EmotionLabel,
    EnvContext,
    FinalState,
    FusionOutput,
    PerceptionResult,
)


class TestNamingConvention:
    """命名必须全局唯一 —— 防止文档的 face/pose 混进来。"""

    def test_agent_ids_are_fixed(self) -> None:
        assert AGENT_EXPRESSION == "expression"
        assert AGENT_BEHAVIOR == "behavior"
        assert AGENT_ENV == "env"

    def test_default_fusion_agents(self) -> None:
        assert AGENT_IDS == (AGENT_EXPRESSION, AGENT_BEHAVIOR)

    def test_no_legacy_aliases(self) -> None:
        """face / pose 不应出现在任何 agent_id 中。"""
        for agent_id in (AGENT_EXPRESSION, AGENT_BEHAVIOR, AGENT_ENV):
            assert "face" not in agent_id
            assert "pose" not in agent_id


class TestEmotionLabel:
    def test_four_labels(self) -> None:
        assert len(EmotionLabel) == 4

    def test_values(self) -> None:
        assert EmotionLabel.FOCUSED.value == "focused"
        assert EmotionLabel.CONFUSED.value == "confused"
        assert EmotionLabel.DISTRACTED.value == "distracted"
        assert EmotionLabel.UNKNOWN.value == "unknown"

    def test_is_str_enum(self) -> None:
        """继承 str 便于 JSON 序列化与前端直接使用。"""
        assert isinstance(EmotionLabel.FOCUSED, str)


class TestPerceptionResult:
    def test_valid_construction(self) -> None:
        r = PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.9, AGENT_EXPRESSION)
        assert r.agent_id == AGENT_EXPRESSION
        assert r.ts == 0.0
        assert r.frame_id == 0

    def test_new_timestamp_fields(self) -> None:
        """新增字段：B4 的丢帧判据依赖它们。"""
        r = PerceptionResult(
            EmotionLabel.FOCUSED, 0.8, 0.9, AGENT_EXPRESSION, ts=1.5, frame_id=7
        )
        assert r.ts == 1.5
        assert r.frame_id == 7

    @pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
    def test_prob_out_of_range(self, bad: float) -> None:
        with pytest.raises(ValueError, match="prob"):
            PerceptionResult(EmotionLabel.FOCUSED, bad, 0.9, AGENT_EXPRESSION)

    @pytest.mark.parametrize("bad", [-0.01, 1.5])
    def test_confidence_out_of_range(self, bad: float) -> None:
        with pytest.raises(ValueError, match="confidence"):
            PerceptionResult(EmotionLabel.FOCUSED, 0.8, bad, AGENT_EXPRESSION)

    def test_empty_agent_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="agent_id"):
            PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.9, "   ")

    def test_negative_frame_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="frame_id"):
            PerceptionResult(
                EmotionLabel.FOCUSED, 0.8, 0.9, AGENT_EXPRESSION, frame_id=-1
            )

    def test_boundary_values_accepted(self) -> None:
        PerceptionResult(EmotionLabel.FOCUSED, 0.0, 0.0, AGENT_EXPRESSION)
        PerceptionResult(EmotionLabel.FOCUSED, 1.0, 1.0, AGENT_EXPRESSION)

    def test_frozen(self) -> None:
        r = PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.9, AGENT_EXPRESSION)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.prob = 0.5  # type: ignore[misc]


class TestProbDist:
    """``prob_dist`` 完整分布（算法文档 §3.1 的数据来源）。

    背景：仅凭标量 ``prob`` 无法实现 ``S(ℓ) = Σ w_i·P_i(ℓ)``，融合层只能退化
    为加权多数投票。该字段是修复该问题的契约支撑，因此必须有守卫测试。
    """

    def test_emotion_labels_excludes_unknown(self) -> None:
        """规范顺序必须是三类情感，且不含 UNKNOWN 哨兵。"""
        assert EMOTION_LABELS == (
            EmotionLabel.FOCUSED,
            EmotionLabel.CONFUSED,
            EmotionLabel.DISTRACTED,
        )
        assert EmotionLabel.UNKNOWN not in EMOTION_LABELS

    def test_default_is_empty(self) -> None:
        """向后兼容：不传 prob_dist 时为空，走融合层降级路径。"""
        r = PerceptionResult(EmotionLabel.FOCUSED, 0.8, 0.9, AGENT_EXPRESSION)
        assert dict(r.prob_dist) == {}

    def test_valid_distribution_accepted(self) -> None:
        r = PerceptionResult(
            EmotionLabel.FOCUSED,
            0.7,
            0.9,
            AGENT_EXPRESSION,
            prob_dist={
                EmotionLabel.FOCUSED: 0.7,
                EmotionLabel.CONFUSED: 0.2,
                EmotionLabel.DISTRACTED: 0.1,
            },
        )
        assert r.prob_dist[EmotionLabel.FOCUSED] == pytest.approx(0.7)
        assert sum(r.prob_dist.values()) == pytest.approx(1.0)

    def test_one_hot_accepted(self) -> None:
        r = PerceptionResult(
            EmotionLabel.DISTRACTED,
            1.0,
            1.0,
            AGENT_BEHAVIOR,
            prob_dist={
                EmotionLabel.FOCUSED: 0.0,
                EmotionLabel.CONFUSED: 0.0,
                EmotionLabel.DISTRACTED: 1.0,
            },
        )
        assert r.prob_dist[EmotionLabel.DISTRACTED] == 1.0

    def test_unknown_key_rejected(self) -> None:
        """UNKNOWN 是哨兵不是情感类别，不允许混进分布。"""
        with pytest.raises(ValueError, match="UNKNOWN"):
            PerceptionResult(
                EmotionLabel.FOCUSED,
                0.5,
                0.9,
                AGENT_EXPRESSION,
                prob_dist={
                    EmotionLabel.FOCUSED: 0.5,
                    EmotionLabel.UNKNOWN: 0.5,
                },
            )

    def test_non_enum_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="EmotionLabel"):
            PerceptionResult(
                EmotionLabel.FOCUSED,
                0.5,
                0.9,
                AGENT_EXPRESSION,
                prob_dist={"focused": 0.5, EmotionLabel.CONFUSED: 0.5},  # type: ignore[dict-item]
            )

    @pytest.mark.parametrize(
        "dist",
        [
            {EmotionLabel.FOCUSED: 0.5, EmotionLabel.CONFUSED: 0.4},  # 和 0.9
            {EmotionLabel.FOCUSED: 0.6, EmotionLabel.CONFUSED: 0.6},  # 和 1.2
        ],
    )
    def test_not_normalized_rejected(self, dist: dict) -> None:
        with pytest.raises(ValueError, match="sum to 1"):
            PerceptionResult(
                EmotionLabel.FOCUSED, dist[EmotionLabel.FOCUSED], 0.9,
                AGENT_EXPRESSION, prob_dist=dist,
            )

    def test_value_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="prob_dist"):
            PerceptionResult(
                EmotionLabel.FOCUSED,
                1.0,
                0.9,
                AGENT_EXPRESSION,
                prob_dist={EmotionLabel.FOCUSED: 1.2, EmotionLabel.CONFUSED: -0.2},
            )

    def test_label_missing_from_dist_rejected(self) -> None:
        with pytest.raises(ValueError, match="reported label"):
            PerceptionResult(
                EmotionLabel.FOCUSED,
                0.5,
                0.9,
                AGENT_EXPRESSION,
                prob_dist={
                    EmotionLabel.CONFUSED: 0.5,
                    EmotionLabel.DISTRACTED: 0.5,
                },
            )

    def test_prob_must_match_dist(self) -> None:
        """标量 prob 与分布必须自洽，否则融合层会用错数值。"""
        with pytest.raises(ValueError, match="must equal"):
            PerceptionResult(
                EmotionLabel.FOCUSED,
                0.8,  # ≠ 0.6
                0.9,
                AGENT_EXPRESSION,
                prob_dist={
                    EmotionLabel.FOCUSED: 0.6,
                    EmotionLabel.CONFUSED: 0.4,
                },
            )

    def test_frozen_still_holds_with_dist(self) -> None:
        r = PerceptionResult(
            EmotionLabel.FOCUSED,
            0.6,
            0.9,
            AGENT_EXPRESSION,
            prob_dist={EmotionLabel.FOCUSED: 0.6, EmotionLabel.CONFUSED: 0.4},
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.prob_dist = {}  # type: ignore[misc]


class TestEnvContext:
    def test_valid_construction(self) -> None:
        c = EnvContext(0.8, 0.2, 0.7)
        assert c.env_score == 0.7
        assert c.occlusion == 0.0

    def test_new_occlusion_field(self) -> None:
        """新增字段：三级协商的遮挡降权依赖它。"""
        c = EnvContext(0.8, 0.2, 0.7, occlusion=0.9)
        assert c.occlusion == 0.9

    def test_new_timestamp_fields(self) -> None:
        c = EnvContext(0.8, 0.2, 0.7, ts=2.0, frame_id=5)
        assert c.ts == 2.0
        assert c.frame_id == 5

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"brightness": 1.2},
            {"blur": -0.1},
            {"env_score": 1.01},
            {"occlusion": 1.5},
        ],
    )
    def test_range_validation(self, kwargs: dict) -> None:
        base = {"brightness": 0.8, "blur": 0.2, "env_score": 0.7, "occlusion": 0.0}
        base.update(kwargs)
        with pytest.raises(ValueError):
            EnvContext(**base)


class TestFusionOutput:
    def test_defaults(self) -> None:
        f = FusionOutput(EmotionLabel.FOCUSED, 0.9)
        assert f.weights == {}
        assert f.reason == ""

    def test_weights_keyed_by_agent_id(self) -> None:
        """weights 的 key 必须与 agent_id 同源，前端才能直接关联。"""
        f = FusionOutput(
            EmotionLabel.FOCUSED,
            0.9,
            weights={AGENT_EXPRESSION: 0.7, AGENT_BEHAVIOR: 0.3},
            reason="reweight",
        )
        assert set(f.weights) == {AGENT_EXPRESSION, AGENT_BEHAVIOR}
        assert f.reason == "reweight"

    def test_confidence_range(self) -> None:
        with pytest.raises(ValueError):
            FusionOutput(EmotionLabel.FOCUSED, 1.5)


class TestFinalState:
    def test_minimal(self) -> None:
        s = FinalState(EmotionLabel.FOCUSED, 12.5)
        assert s.label is EmotionLabel.FOCUSED
        assert s.stale is False

    def test_stale_flag(self) -> None:
        """stale 让前端能区分「新确认」与「维持中」。"""
        s = FinalState(EmotionLabel.CONFUSED, 12.5, stale=True, confidence=0.8)
        assert s.stale is True
        assert s.confidence == 0.8
