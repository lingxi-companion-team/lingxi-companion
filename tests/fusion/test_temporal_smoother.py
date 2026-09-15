"""时序平滑测试（A 主责）。

关键验证点：
1. "3中2" 确认逻辑正确；
2. 票数不足时**保持上一稳定状态**并标记 stale（而非输出 UNKNOWN）；
3. UNKNOWN 不污染投票窗口；
4. 内存为 O(1)。
"""

from __future__ import annotations

import pytest

from common.mock.generator import get_scenario
from common.perception_types import AGENT_EXPRESSION, EmotionLabel
from fusion.fusion_engine import FusionEngine
from fusion.temporal_smoother import TemporalSmoother

F = EmotionLabel.FOCUSED
C = EmotionLabel.CONFUSED
D = EmotionLabel.DISTRACTED
U = EmotionLabel.UNKNOWN


class TestWindowBehavior:
    def test_window_not_full_returns_none(self) -> None:
        s = TemporalSmoother()
        assert s.update(F) is None
        assert s.update(C) is None
        assert s.stable_label is None

    def test_three_of_two_confirms(self) -> None:
        s = TemporalSmoother()
        s.update(F)
        s.update(C)
        out = s.update(F)
        assert out is not None
        assert out.label is F
        assert out.stale is False

    def test_two_consecutive_do_not_confirm(self) -> None:
        """[C,C] 只有两帧，窗口未满 → 不确认。"""
        s = TemporalSmoother()
        s.update(C)
        assert s.update(C) is None

    def test_three_same_confirms(self) -> None:
        s = TemporalSmoother()
        s.update(F)
        s.update(F)
        out = s.update(F)
        assert out is not None and out.label is F and out.stale is False


class TestHoldSemantics:
    """票数不足时保持上一状态 —— 本次语义修订的核心。"""

    def test_hold_previous_state_instead_of_unknown(self) -> None:
        s = TemporalSmoother()
        for _ in range(3):
            s.update(F)  # 窗口=[F,F,F] → 确认 F

        # 之后连续推入 C,F,C：窗口依次为
        #   [F,F,C] → 2票F，仍确认 F
        #   [F,C,F] → 2票F，仍确认 F
        #   [C,F,C] → 2票C，重确认 C
        # 因此抖动应至少在两帧内维持 F（而不是立刻跳变）
        first = s.update(C)
        assert first is not None and first.label is F

        second = s.update(F)
        assert second is not None and second.label is F

    def test_insufficient_votes_holds_and_marks_stale(self) -> None:
        """构造真正的「无 ≥2 票」窗口：三格全不同。

        窗口内每格一种标签时无人达 2 票 → 保持上一稳定状态并置 stale。
        """
        s = TemporalSmoother()
        for _ in range(3):
            s.update(F)  # 确认 F，窗口=[F,F,F]

        # 逐个替换，确保任一时刻窗口内出现三种不同标签
        s.update(C)  # [F,F,C]
        s.update(D)  # [F,C,D]  ← 三格全不同
        held = s.update(U)  # UNKNOWN 不入窗，窗口仍 [F,C,D]
        assert held is not None
        assert held.label is F, "无 ≥2 票时应保持上一稳定状态 F"
        assert held.stale is True, "应标记 stale 让前端显示「维持中」"

    def test_stale_flag_cleared_on_new_confirmation(self) -> None:
        s = TemporalSmoother()
        for _ in range(3):
            s.update(F)

        s.update(C)
        s.update(D)
        held = s.update(U)  # 窗口 [F,C,D]，无 ≥2 票
        assert held is not None and held.stale is True and held.label is F

        # 连续三帧 C → 重新确认，stale 应清除
        for _ in range(3):
            out = s.update(C)
        assert out is not None
        assert out.label is C
        assert out.stale is False

    def test_first_frames_return_none_not_stale(self) -> None:
        """从未确认过任何状态时返回 None（尚无内容可输出）。"""
        s = TemporalSmoother()
        assert s.update(C) is None

    def test_hold_disabled_returns_unknown(self) -> None:
        """可切回旧语义（配置驱动），保证向后兼容。"""
        s = TemporalSmoother(hold_on_insufficient=False)
        for _ in range(3):
            s.update(F)

        s.update(C)
        s.update(D)
        out = s.update(U)  # 窗口 [F,C,D]，无 ≥2 票且 hold 关闭
        assert out is not None
        assert out.label is U
        assert out.stale is True


class TestUnknownHandling:
    def test_unknown_does_not_fill_window(self) -> None:
        """UNKNOWN 表达「本帧无判定」，不应占用窗口。

        若计入窗口，两帧 UNKNOWN 就把 3 格占满，反而延迟确认。
        """
        s = TemporalSmoother()
        assert s.update(U) is None
        assert s.update(U) is None
        assert s.update(U) is None
        assert s.stable_label is None

    def test_unknown_does_not_break_pending_window(self) -> None:
        s = TemporalSmoother()
        s.update(F)
        s.update(U)  # 被忽略
        s.update(F)
        out = s.update(F)
        assert out is not None and out.label is F

    def test_all_unknown_never_confirms(self) -> None:
        s = TemporalSmoother()
        for _ in range(10):
            s.update(U)
        assert s.stable_label is None


class TestMetadata:
    def test_metadata_propagated(self) -> None:
        s = TemporalSmoother()
        s.update(F, ts=1.0, frame_id=1)
        s.update(F, ts=2.0, frame_id=2)
        out = s.update(F, confidence=0.88, weights={AGENT_EXPRESSION: 0.7}, ts=3.0, frame_id=3)
        assert out is not None
        assert out.timestamp == 3.0
        assert out.frame_id == 3
        assert out.confidence == 0.88
        assert out.weights == {AGENT_EXPRESSION: 0.7}

    def test_held_frame_carries_current_timestamp(self) -> None:
        """保持状态时，timestamp 应是当前帧而非旧帧，否则前端会以为卡死。"""
        s = TemporalSmoother()
        for i in range(3):
            s.update(F, ts=i, frame_id=i)
        s.update(C, ts=10.0, frame_id=10)
        s.update(F, ts=11.0, frame_id=11)
        out = s.update(C, ts=12.0, frame_id=12)
        assert out is not None
        assert out.timestamp == 12.0
        assert out.frame_id == 12


class TestConstruction:
    def test_invalid_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="window_size"):
            TemporalSmoother(window_size=0)

    def test_min_votes_must_fit_window(self) -> None:
        with pytest.raises(ValueError, match="min_votes"):
            TemporalSmoother(window_size=3, min_votes=4)

    def test_custom_window(self) -> None:
        s = TemporalSmoother(window_size=5, min_votes=3)
        for _ in range(2):
            s.update(F)
        assert s.update(F) is None  # 窗口未满
        s.update(F)
        out = s.update(F)
        assert out is not None and out.label is F

    def test_reset_clears_state(self) -> None:
        s = TemporalSmoother()
        for _ in range(3):
            s.update(F)
        assert s.stable_label is F
        s.reset()
        assert s.stable_label is None
        assert s.update(F) is None


class TestMemoryComplexity:
    def test_constant_memory(self) -> None:
        """O(1)：无论处理多少帧，窗口长度恒定。"""
        s = TemporalSmoother()
        for i in range(1000):
            s.update(F if i % 2 == 0 else C)
        # 刻意直接读私有属性：本用例要验证的正是内部结构本身。
        assert len(s._window) == 3

    def test_config_defaults_loaded(self) -> None:
        """默认参数应来自 configs/thresholds.yaml。"""
        s = TemporalSmoother()
        # 同上，直接读私有属性以核对配置默认值确实被载入。
        assert s._window.maxlen == 3
        assert s._min_votes == 2
        assert s._hold is True


class TestEndToEndPipeline:
    """融合 + 平滑的串联，即 M1 的假流程。"""

    def test_mock_stream_produces_stable_state(self) -> None:
        engine = FusionEngine()
        smoother = TemporalSmoother()
        scenario = get_scenario("consensus")

        last = None
        for results, env in scenario.frames:
            fused = engine.fuse(results, env)
            last = smoother.update(
                fused.label,
                confidence=fused.confidence,
                weights=fused.weights,
                ts=fused.ts,
                frame_id=fused.frame_id,
            )
        assert last is not None
        assert last.label is F
        assert last.stale is False

    def test_jitter_scenario_holds_state(self) -> None:
        """jitter 场景：先确认 F，再抖动不改变输出。"""
        engine = FusionEngine()
        smoother = TemporalSmoother()

        # 先跑共识场景三帧确认 F
        for results, env in get_scenario("consensus").frames[:3]:
            fused = engine.fuse(results, env)
            smoother.update(fused.label, confidence=fused.confidence, ts=fused.ts)

        # 再跑抖动场景
        last = None
        for results, env in get_scenario("jitter").frames:
            fused = engine.fuse(results, env)
            last = smoother.update(fused.label, confidence=fused.confidence, ts=fused.ts)

        assert last is not None
        assert last.label is F, "抖动不应改变稳定状态"
