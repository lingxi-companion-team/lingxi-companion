"""趋势采样缓冲（纯逻辑，无 GUI 依赖、**不读时钟**）。

v7 仪表盘有一张「全班状态趋势」图（堆叠条 / 折线）。画它需要**一段时间上的采样**，
而「采样窗口多大、怎么取序列、缺分量怎么补」是规则，不是画法 —— 所以放在 present 层。

为什么不读时钟
--------------
:class:`TrendBuffer` 只在 :meth:`~TrendBuffer.push` 时把传入的 :class:`Summary`
存进一个定长环形缓冲，**自己不取 ``time.time()``**。这样：

1. 测试可以完全确定地构造一串采样，断言序列（没有 sleep、没有时间抖动）；
2. 「什么时刻 push」由调用方（轮询循环）决定 —— 采样频率是运行时关注点，
   不是展示规则。

采样是**人数快照**而不是比例快照：比例会随在线人数变化而失真（在线从 30 掉到 3，
占比可能不变但意义全变），所以底层存 ``by_label`` 的绝对人数，比例由
:meth:`~TrendBuffer.ratio_series` 按各自的在线人数现算。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from app.present.summary import CLOSED_KEY, LABEL_ORDER, Summary

__all__ = ["DEFAULT_TREND_WINDOW", "TrendBuffer", "TrendSample"]

#: 默认采样窗口（帧数）。轮询约 1.2s 一次，60 帧约 72 秒 —— 够看出「这一两分钟」的走向，
#: 又不至于把更早的、已经无关的分布一直画在图上。
DEFAULT_TREND_WINDOW = 60


@dataclass(frozen=True)
class TrendSample:
    """某一帧的分布快照（人数，不是比例）。

    Attributes:
        online_count: 该帧的在线人数（含已关闭者）—— 也是比例的分母。
        closed_count: 该帧已关闭感知的人数。
        by_label: 四个情感分量的人数（键取自 :data:`app.present.summary.LABEL_ORDER`，
            4 个键恒存在）。
    """

    online_count: int
    closed_count: int
    by_label: Mapping[str, int]


class TrendBuffer:
    """定长环形缓冲，存最近 ``window`` 帧的 :class:`TrendSample`。

    满了之后新采样挤掉最旧的一帧（``deque(maxlen=...)`` 的语义），
    所以内存占用恒定，长时间运行不会涨。
    """

    def __init__(self, window: int = DEFAULT_TREND_WINDOW) -> None:
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window!r}")
        self._window = window
        self._samples: deque[TrendSample] = deque(maxlen=window)

    @property
    def window(self) -> int:
        """缓冲容量（帧数）。"""
        return self._window

    def push(self, summary: Summary) -> TrendSample:
        """把一帧汇总压入缓冲，返回生成的快照（便于调用方直接用）。"""
        sample = TrendSample(
            online_count=summary.online_count,
            closed_count=summary.closed_count,
            by_label={key: int(summary.by_label.get(key, 0)) for key in LABEL_ORDER},
        )
        self._samples.append(sample)
        return sample

    def samples(self) -> list[TrendSample]:
        """按时间**从旧到新**返回当前缓冲里的采样。"""
        return list(self._samples)

    def latest(self) -> TrendSample | None:
        """最新一帧；缓冲为空时 ``None``。"""
        return self._samples[-1] if self._samples else None

    def series(self, key: str) -> list[int]:
        """某分量的**人数**序列（旧→新）。

        ``key`` 取 :data:`app.present.summary.LABEL_ORDER` 里的情感键，
        或 :data:`app.present.summary.CLOSED_KEY`（后者取 ``closed_count``）。
        未登记的键返回全 0 序列（长度与采样数一致）—— 不抛错，同展示层的兜底约定。
        """
        if key == CLOSED_KEY:
            return [sample.closed_count for sample in self._samples]
        if key not in LABEL_ORDER:
            return [0] * len(self._samples)
        return [int(sample.by_label.get(key, 0)) for sample in self._samples]

    def ratio_series(self, key: str) -> list[float]:
        """某分量的**占比**序列（旧→新）；该帧在线为 0 时记 ``0.0``。"""
        counts = self.series(key)
        return [
            (count / sample.online_count if sample.online_count else 0.0)
            for count, sample in zip(counts, self._samples, strict=True)
        ]

    def clear(self) -> None:
        """清空缓冲（如切换课堂 / 重置会话时调用）。"""
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)
