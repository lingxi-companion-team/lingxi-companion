"""``app.present.status`` 的契约测试。

守住两条：离线文案格式稳定（client 状态栏直接展示）；新鲜度三档的判定边界
（尤其 stale/None 的优先级高于时间比较）。
"""

from __future__ import annotations

from app.present.status import FRESH_AGING_SECONDS, freshness, offline
from common.perception_types import EmotionLabel, FinalState


def _state(*, stale: bool = False, timestamp: float = 100.0) -> FinalState:
    return FinalState(
        label=EmotionLabel.FOCUSED,
        timestamp=timestamp,
        stale=stale,
        confidence=0.9,
    )


class TestOffline:
    def test_uses_exception_type_name(self) -> None:
        assert offline(TimeoutError("x")) == "离线（TimeoutError）"
        assert offline(OSError("x")) == "离线（OSError）"

    def test_format_is_stable(self) -> None:
        # 文案是「离线（Type）」，别被顺手改成冒号或漏括号 —— 状态栏宽度按它排的。
        text = offline(ValueError("boom"))
        assert text.startswith("离线（")
        assert text.endswith("）")
        assert "ValueError" in text


class TestFreshness:
    def test_none_state_is_stale(self) -> None:
        # 没有可展示的数据 = 最弱档，不能因为它「没有 timestamp」就当成 fresh。
        assert freshness(1000.0, None) == "stale"

    def test_stale_flag_wins_over_time(self) -> None:
        # 哪怕时间很新，stale=True 也必须按 stale 处理（契约：显示「维持中」）。
        s = _state(stale=True, timestamp=999.9)
        assert freshness(1000.0, s) == "stale"

    def test_fresh_below_threshold(self) -> None:
        s = _state(timestamp=1000.0 - (FRESH_AGING_SECONDS - 0.1))
        assert freshness(1000.0, s) == "fresh"

    def test_aging_at_threshold(self) -> None:
        # 边界：恰好等于阈值算 aging（>= 语义），别让边界值飘。
        s = _state(timestamp=1000.0 - FRESH_AGING_SECONDS)
        assert freshness(1000.0, s) == "aging"

    def test_aging_above_threshold(self) -> None:
        s = _state(timestamp=1000.0 - (FRESH_AGING_SECONDS + 5.0))
        assert freshness(1000.0, s) == "aging"
