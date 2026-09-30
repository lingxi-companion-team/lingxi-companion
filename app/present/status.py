"""连接状态与数据新鲜度判定（纯逻辑；供 client 离线降级与 stale 视觉分层）。

这一层被 CI 逼出来：`app/client/` 整体 omit，任何判定写进 GUI 就永远测不到。
所以「断网时状态栏怎么写」「数据算不算旧」这两条规则落在这里，client 只负责
调用返回值去改样式。

- 离线文案：统一由 :func:`offline` 产出，避免各处置出错别字或不一致的括号；
- 新鲜度：三档（fresh/aging/stale），让「刚出结果」「有点旧」「在维持」在视觉上
  可区分，而不是只有 stale 一个布尔开关。
"""

from __future__ import annotations

from common.perception_types import FinalState

__all__ = ["FRESH_AGING_SECONDS", "offline", "freshness"]

#: 「有点旧」的阈值（秒）。小于它算 fresh，大于等于它且未 stale 算 aging。
#: 取值参照 client 轮询周期（约 1.2s）的 2 倍留裕量；只在展示层用，不进融合。
FRESH_AGING_SECONDS = 3.0


def offline(exc: BaseException) -> str:
    """把 provider 抛出的异常归一成状态栏文案。

    文案格式 ``离线（TypeName）`` 与 client 现状一致 —— 这里只是把散落各处的
    字符串拼接收拢到一处，改文案只动这一个函数。
    """
    return f"离线（{type(exc).__name__}）"


def freshness(now_ts: float, state: FinalState | None) -> str:
    """新鲜度三档：``"fresh"`` / ``"aging"`` / ``"stale"``。

    - ``state`` 为 ``None``（本帧无结果/教师/被掩）：恒 ``"stale"`` —— 没有可
      展示的新鲜数据，视觉上按最弱处理；
    - ``state.stale`` 为 True：恒 ``"stale"``（契约要求显示「维持中」）；
    - 否则按 ``now_ts - state.timestamp`` 与 :data:`FRESH_AGING_SECONDS` 比较。
    """
    if state is None or state.stale:
        return "stale"
    age = now_ts - state.timestamp
    return "aging" if age >= FRESH_AGING_SECONDS else "fresh"
