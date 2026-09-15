"""时间维度平滑："3中2" 时序滑动窗口。

对应 ``docs/algorithm.md`` §4，以及说明文档 5.1.2 第三阶段。

语义修订说明
------------
文档原文写「否则**继续等待**后续帧输入」，而旧实现返回
``EmotionLabel.UNKNOWN``。两者不等价：

- 「继续等待」= 保持上一个已确认状态，输出不跳变；
- 「返回 UNKNOWN」= 立即对外宣告「不确定」，会让状态码在高频抖动下闪断。

教学场景下闪断是明显的体验缺陷，因此本实现采用前者：票数不足时
``FinalState.label`` 沿用上一稳定状态，并置 ``stale=True`` 让前端可以
标注「维持中」。该行为由 ``configs/thresholds.yaml`` 的
``smoothing.hold_on_insufficient`` 控制，需要旧语义时可切回。

内存复杂度 O(1)：仅保存固定长度窗口与一个上一稳定状态。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from common.config import get, load_config
from common.perception_types import EmotionLabel, FinalState

__all__ = ["TemporalSmoother"]


class TemporalSmoother:
    """ "3中2" 滑动窗口确认器。

    Args:
        window_size: 窗口长度，默认取配置 ``smoothing.window``（3）。
        min_votes: 确认所需票数，默认取配置 ``smoothing.min_votes``（2）。
        hold_on_insufficient: 票数不足时是否保持上一状态。默认取配置
            ``smoothing.hold_on_insufficient``（true）。
        config: 配置字典，``None`` 时读取 ``configs/thresholds.yaml``。
    """

    def __init__(
        self,
        window_size: int | None = None,
        min_votes: int | None = None,
        hold_on_insufficient: bool | None = None,
        config: Mapping | None = None,
    ) -> None:
        self._config = dict(config) if config is not None else load_config()

        if window_size is None:
            window_size = int(get("smoothing", "window", default=3, config=self._config))
        if min_votes is None:
            min_votes = int(get("smoothing", "min_votes", default=2, config=self._config))
        if hold_on_insufficient is None:
            hold_on_insufficient = bool(
                get(
                    "smoothing",
                    "hold_on_insufficient",
                    default=True,
                    config=self._config,
                )
            )

        if window_size < 1:
            raise ValueError("window_size must be positive")
        if not 1 <= min_votes <= window_size:
            raise ValueError("min_votes must be within [1, window_size]")

        self._min_votes = min_votes
        self._hold = hold_on_insufficient
        self._window_size = window_size
        self._window: deque[EmotionLabel] = deque(maxlen=window_size)

        # 已确认的稳定状态（用于 hold 语义）
        self._stable: EmotionLabel | None = None
        self._stable_confidence: float = 0.0
        self._stable_weights: Mapping[str, float] = {}
        self._last_ts: float = 0.0
        self._last_frame_id: int = 0

    # ------------------------------------------------------------------

    def update(
        self,
        label: EmotionLabel,
        confidence: float = 0.0,
        weights: Mapping[str, float] | None = None,
        ts: float = 0.0,
        frame_id: int = 0,
    ) -> FinalState | None:
        """送入一帧瞬时结果，返回确认后的稳定状态。

        Args:
            label: 融合层给出的瞬时标签。
            confidence: 该帧的置信度，用于回填 ``FinalState``。
            weights: 该帧的实际权重，用于回填 ``FinalState``。
            ts: 帧时间戳。
            frame_id: 帧序号。

        Returns:
            确认后的 :class:`FinalState`。窗口未填满且尚无历史稳定状态时
            返回 ``None``（尚无任何可对外输出的内容）。
        """
        self._last_ts = ts
        self._last_frame_id = frame_id

        # UNKNOWN 不参与投票：它表达的是「本帧无判定」，不是一种情感状态。
        # 若把 UNKNOWN 计入窗口，两帧 UNKNOWN 会占满窗口，反而延迟确认。
        if label is not EmotionLabel.UNKNOWN:
            self._window.append(label)

        # 用自存的 _window_size 而非 deque.maxlen：后者类型是 int | None，
        # 会让静态检查无法确认这是「整数比较」（构造时已保证 >= 1）。
        if len(self._window) < self._window_size:
            return self._held_or_none()

        counts: dict[EmotionLabel, int] = {}
        for candidate in self._window:
            counts[candidate] = counts.get(candidate, 0) + 1

        winner, votes = max(counts.items(), key=lambda item: (item[1], item[0].value))

        if votes >= self._min_votes:
            # 确认：更新稳定状态
            self._stable = winner
            self._stable_confidence = confidence
            self._stable_weights = dict(weights or {})
            return FinalState(
                label=winner,
                timestamp=ts,
                stale=False,
                confidence=confidence,
                weights=self._stable_weights,
                frame_id=frame_id,
            )

        # 票数不足
        if self._hold:
            return self._held_or_none()

        return FinalState(
            label=EmotionLabel.UNKNOWN,
            timestamp=ts,
            stale=True,
            confidence=confidence,
            weights=dict(weights or {}),
            frame_id=frame_id,
        )

    # ------------------------------------------------------------------

    def _held_or_none(self) -> FinalState | None:
        """保持上一稳定状态；从未确认过任何状态时返回 None。"""
        if self._stable is None:
            return None
        return FinalState(
            label=self._stable,
            timestamp=self._last_ts,
            stale=True,
            confidence=self._stable_confidence,
            weights=dict(self._stable_weights),
            frame_id=self._last_frame_id,
        )

    # ------------------------------------------------------------------

    @property
    def stable_label(self) -> EmotionLabel | None:
        """当前已确认的稳定标签，未确认时为 None。"""
        return self._stable

    def reset(self) -> None:
        """清空窗口与稳定状态（切换视频源或重新开始会话时调用）。"""
        self._window.clear()
        self._stable = None
        self._stable_confidence = 0.0
        self._stable_weights = {}
