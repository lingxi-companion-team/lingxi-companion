"""融合层与权重函数测试（A 主责）。

覆盖：权重函数的连续性与边界、三级协商各级、缺路降级、遮挡降权。
"""

from __future__ import annotations

import pytest

from common.mock.generator import get_scenario, scenario_frame
from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_EXPRESSION,
    EmotionLabel,
    EnvContext,
    PerceptionResult,
)
from fusion.fusion_engine import (
    REASON_CONSENSUS,
    REASON_EMPTY,
    REASON_LOW_CONFIDENCE,
    REASON_REWEIGHT,
    FusionEngine,
)
from fusion.temporal_smoother import TemporalSmoother
from fusion.weights import normalized_weights, w_behavior, w_face


def _result(agent_id: str, label: EmotionLabel, prob: float) -> PerceptionResult:
    return PerceptionResult(label, prob, prob, agent_id)


# ----------------------------------------------------------------------
# 权重函数
# ----------------------------------------------------------------------


class TestWeightFunction:
    def test_monotonic_increasing(self) -> None:
        """环境越好，表情权重越高。"""
        values = [w_face(e / 100) for e in range(0, 101, 5)]
        assert values == sorted(values)

    def test_at_center_point_equals_half(self) -> None:
        assert w_face(0.6) == pytest.approx(0.5, abs=1e-9)

    def test_high_env_boosts_face(self) -> None:
        assert w_face(0.9) > 0.9
        assert w_face(0.8) > 0.8

    def test_low_env_suppresses_face(self) -> None:
        assert w_face(0.35) < 0.15
        assert w_face(0.4) < 0.2

    def test_covers_documented_range(self) -> None:
        """文档描述的 0.7:0.3 ↔ 0.3:0.7 区间应被覆盖。"""
        assert w_face(0.85) > 0.7
        assert w_face(0.30) < 0.3

    def test_complementary(self) -> None:
        for e in (0.0, 0.3, 0.6, 0.9, 1.0):
            assert w_face(e) + w_behavior(e) == pytest.approx(1.0, abs=1e-12)

    def test_continuity(self) -> None:
        """连续性是文档「非开关切换」表述的数学保证。

        用细步长扫描，检查相邻点差值的最大值是否受控。
        分段常数函数会在这里产生约 0.2 的跳变。
        """
        step = 0.001
        prev = w_face(0.0)
        max_jump = 0.0
        e = step
        while e <= 1.0:
            cur = w_face(e)
            max_jump = max(max_jump, abs(cur - prev))
            prev = cur
            e += step
        assert max_jump < 0.01, f"存在阶跃，最大跳变 {max_jump:.4f}"

    def test_out_of_range_clamped_not_raised(self) -> None:
        """权重函数在数据流末端，宁可钳制也不要让整帧失败。"""
        assert 0.0 < w_face(-1.0) < 1.0
        assert 0.0 < w_face(2.0) < 1.0
        assert w_face(-1.0) == pytest.approx(w_face(0.0), abs=1e-9)


class TestNormalizedWeights:
    def test_two_channels_sum_to_one(self) -> None:
        w = normalized_weights(0.9)
        assert sum(w.values()) == pytest.approx(1.0)
        assert set(w) == {AGENT_EXPRESSION, AGENT_BEHAVIOR}

    def test_single_channel_gets_full_weight(self) -> None:
        """风险互备的落地点：行为智能体未交付时不报错、自动补满。"""
        w = normalized_weights(0.9, active=[AGENT_EXPRESSION])
        assert w == {AGENT_EXPRESSION: 1.0}

    def test_empty_active_falls_back_to_expression(self) -> None:
        assert normalized_weights(0.9, active=[]) == {AGENT_EXPRESSION: 1.0}

    def test_weight_transfer_reverses_sign(self) -> None:
        """环境从好到差，两路权重的相对关系应发生反转。"""
        good = normalized_weights(0.9)
        bad = normalized_weights(0.3)
        assert good[AGENT_EXPRESSION] > good[AGENT_BEHAVIOR]
        assert bad[AGENT_EXPRESSION] < bad[AGENT_BEHAVIOR]


# ----------------------------------------------------------------------
# 融合引擎
# ----------------------------------------------------------------------


class TestFusionConsensus:
    """一级：高可信一致。"""

    def test_consistent_high_trust_direct_output(self) -> None:
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.92),
                _result(AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.88),
            ],
            EnvContext(0.9, 0.1, 0.92, occlusion=0.0),
        )
        assert out.label is EmotionLabel.FOCUSED
        assert out.reason == REASON_CONSENSUS
        assert out.confidence > 0.5

    def test_occlusion_prevents_consensus(self) -> None:
        """环境好但遮挡严重时不应判为一级。"""
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.92),
                _result(AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.88),
            ],
            EnvContext(0.9, 0.1, 0.92, occlusion=0.9),
        )
        assert out.reason != REASON_CONSENSUS


class TestFusionReweight:
    """二级：环境驱动降权。"""

    def test_low_light_transfers_weight_to_behavior(self) -> None:
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.6),
                _result(AGENT_BEHAVIOR, EmotionLabel.DISTRACTED, 0.65),
            ],
            EnvContext(0.22, 0.55, 0.30),
        )
        assert out.reason == REASON_REWEIGHT
        assert out.weights[AGENT_BEHAVIOR] > out.weights[AGENT_EXPRESSION]

    def test_behavior_wins_under_low_light(self) -> None:
        """弱光下即使表情概率更高，行为仍应主导判定。"""
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.70),
                _result(AGENT_BEHAVIOR, EmotionLabel.DISTRACTED, 0.66),
            ],
            EnvContext(0.2, 0.6, 0.25),
        )
        assert out.label is EmotionLabel.DISTRACTED

    def test_occlusion_suppresses_expression_weight(self) -> None:
        clear = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.8),
                _result(AGENT_BEHAVIOR, EmotionLabel.CONFUSED, 0.78),
            ],
            EnvContext(0.8, 0.2, 0.75, occlusion=0.0),
        )
        blocked = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.8),
                _result(AGENT_BEHAVIOR, EmotionLabel.CONFUSED, 0.78),
            ],
            EnvContext(0.8, 0.2, 0.75, occlusion=0.9),
        )
        assert (
            blocked.weights[AGENT_EXPRESSION] < clear.weights[AGENT_EXPRESSION]
        )
        assert blocked.label is EmotionLabel.CONFUSED


class TestFusionLowConfidence:
    """三级：低置信干预。"""

    def test_low_prob_marked_unknown(self) -> None:
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.35),
                _result(AGENT_BEHAVIOR, EmotionLabel.DISTRACTED, 0.33),
            ],
            EnvContext(0.7, 0.3, 0.70),
        )
        assert out.label is EmotionLabel.UNKNOWN
        assert out.reason == REASON_LOW_CONFIDENCE

    def test_weights_still_reported_when_unknown(self) -> None:
        """即使判为不确定，权重也要回填，供前端展示与消融分析。"""
        out = FusionEngine().fuse(
            [_result(AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.35)],
            EnvContext(0.7, 0.3, 0.70),
        )
        assert out.weights


class TestFusionDegradation:
    """缺路与空输入。"""

    def test_empty_input(self) -> None:
        out = FusionEngine().fuse([], EnvContext(0.9, 0.1, 0.9))
        assert out.label is EmotionLabel.UNKNOWN
        assert out.reason == REASON_EMPTY
        assert out.weights == {}

    def test_single_channel_works(self) -> None:
        """B 或 A 的模块未交付时，系统仍能给出判定。"""
        out = FusionEngine().fuse(
            [_result(AGENT_EXPRESSION, EmotionLabel.DISTRACTED, 0.70)],
            EnvContext(0.6, 0.3, 0.60),
        )
        assert out.label is EmotionLabel.DISTRACTED
        assert out.weights == {AGENT_EXPRESSION: 1.0}

    def test_unknown_results_filtered(self) -> None:
        """全为 UNKNOWN 时不应产出任何判定。"""
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.UNKNOWN, 0.1),
                _result(AGENT_BEHAVIOR, EmotionLabel.UNKNOWN, 0.1),
            ],
            EnvContext(0.5, 0.5, 0.5),
        )
        assert out.label is EmotionLabel.UNKNOWN
        assert out.reason == REASON_EMPTY

    def test_none_env_does_not_crash(self) -> None:
        out = FusionEngine().fuse(
            [_result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.8)], None
        )
        assert out.label is EmotionLabel.FOCUSED

    def test_duplicate_agent_keeps_highest_confidence(self) -> None:
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.50),
                _result(AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.90),
            ],
            EnvContext(0.9, 0.1, 0.9),
        )
        assert set(out.weights) == {AGENT_EXPRESSION}
        assert out.label is EmotionLabel.CONFUSED


class TestFusionMetadata:
    """元信息回填，供前端与消融实验使用。"""

    def test_ts_and_frame_id_propagated(self) -> None:
        env = EnvContext(0.9, 0.1, 0.9, ts=3.3, frame_id=33)
        out = FusionEngine().fuse(
            [_result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.9)], env
        )
        assert out.ts == 3.3
        assert out.frame_id == 33

    def test_weights_keyed_by_agent_id(self) -> None:
        out = FusionEngine().fuse(
            [
                _result(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.9),
                _result(AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.9),
            ],
            EnvContext(0.9, 0.1, 0.9),
        )
        assert set(out.weights) <= {AGENT_EXPRESSION, AGENT_BEHAVIOR}


# ----------------------------------------------------------------------
# 与 mock 场景的联动（保证场景与算法对得上）
# ----------------------------------------------------------------------


class TestMockScenarioAlignment:
    @pytest.mark.parametrize(
        "name,expected_reason,expected_label",
        [
            ("consensus", REASON_CONSENSUS, EmotionLabel.FOCUSED),
            ("conflict", REASON_REWEIGHT, EmotionLabel.CONFUSED),
            ("low_light", REASON_REWEIGHT, EmotionLabel.DISTRACTED),
            ("low_confidence", REASON_LOW_CONFIDENCE, EmotionLabel.UNKNOWN),
            ("occluded", REASON_REWEIGHT, EmotionLabel.CONFUSED),
            ("single_channel", REASON_REWEIGHT, EmotionLabel.DISTRACTED),
        ],
    )
    def test_scenario_behaves_as_documented(
        self, name: str, expected_reason: str, expected_label: EmotionLabel
    ) -> None:
        results, env = scenario_frame(name)
        out = FusionEngine().fuse(results, env)
        assert out.reason == expected_reason, f"{name}: 期望 {expected_reason}"
        assert out.label is expected_label, f"{name}: 期望 {expected_label}"

    def test_scenario_notes_present(self) -> None:
        """每个场景都要有说明，否则测试失败时看不懂原因。"""
        for scenario in get_scenario.__globals__["SCENARIOS"]:
            assert scenario.note, f"{scenario.name} 缺少 note"
            assert scenario.frames, f"{scenario.name} 缺少帧"

    def test_jitter_scenario_really_demonstrates_hold(self) -> None:
        """jitter 场景必须真的产出「保持上一状态 + stale」。

        回归背景：该场景最初写作 ``[F, C, F]``，而三帧窗口内 F 占 2 票会被
        **确认**（stale=False），与场景声称的"抖动不确认"完全相反。仅检查
        ``note`` 非空无法发现这类错误，必须实跑整条链路。
        """
        engine = FusionEngine()
        smoother = TemporalSmoother()

        states = []
        for results, env in get_scenario("jitter").frames:
            fused = engine.fuse(results, env)
            states.append(
                smoother.update(
                    fused.label, fused.confidence, fused.weights, fused.ts, fused.frame_id
                )
            )

        confirmed = [s for s in states if s is not None and not s.stale]
        held = [s for s in states if s is not None and s.stale]

        assert confirmed, "jitter 场景应至少有帧能确认出稳定状态，否则无从'保持'"
        assert held, "jitter 场景应至少有一帧因票数不足而保持上一状态并置 stale"
        # 保持时标签必须等于此前的稳定标签
        assert held[-1].label is confirmed[-1].label
