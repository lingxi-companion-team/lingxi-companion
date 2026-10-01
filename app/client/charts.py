"""图表画法：环形图 / 堆叠条 / 趋势柱（纯画法，无业务判断）。

数据从 :mod:`app.present` 的纯函数来（``summarize`` / ``roster_rows`` / ``TrendBuffer``），
本模块只负责把给定的比例画成形状。放在 ``app/client/`` 意味着不参与覆盖率统计，
所以这里**不做任何判定**（谁是教师、0 人怎么算），只画。

环形图为什么能画（方案 §2.2 实测）
----------------------------------
设计稿用 CSS ``conic-gradient`` 画环形。Tkinter 没有渐变，但
``create_arc(style="arc", width=N)`` 能画**粗圆弧** —— 实测可用，这是本轮最好的
消息：核心图表不用降级成条形。每段用 ``start`` + ``extent`` 控制起止角，
``width`` 就是环的厚度（画在椭圆边界上，向外/向内各占一半）。

Tk 的角度约定（容易错，记在这）
-------------------------------
``start`` 是**逆时针**从 +x 轴（3 点钟方向）算起的角度。要画「从 12 点钟开始
顺时针铺开」的环，就得让 ``start`` 从 90 递减、``extent`` 取负值。
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Sequence

from app.client import rounded

__all__ = [
    "arc_point",
    "draw_donut",
    "draw_sparkline",
    "draw_stacked_bar",
]

#: 环形图的最小可见角度（度）。占比再小也要留一丝弧，否则「有这个人」和
#: 「没这个人」在环上分不出来。1.2° 在 22px 厚的环上约等于 1px 弧长。
_MIN_EXTENT = 1.2


def draw_donut(
    canvas: tk.Canvas,
    cx: float,
    cy: float,
    radius: float,
    thickness: float,
    segments: Sequence[tuple[float, str]],
    *,
    track: str,
    tags: str | tuple[str, ...] | None = None,
) -> None:
    """画环形图。``segments`` 是 ``[(fraction, color), ...]``，按顺序顺时针铺开。

    - 先画一整圈 ``track`` 作底环，再逐段覆盖 —— 这样「总量不满 100%」时剩下的
      部分自然显示为底环，不必额外补一段。
    - ``fraction`` 会被夹到 ``[0, 1]``；0 段跳过（Tk 的 ``extent=0`` 会画出一个
      退化形状）。极小的非零段抬到 :data:`_MIN_EXTENT`，保证「有但很少」看得出。
    - 从 12 点钟（90°）开始，顺时针铺开。
    """
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    if track:
        canvas.create_oval(*box, outline=track, width=thickness, tags=tags or ())
    start = 90.0
    for fraction, color in segments:
        value = 0.0 if fraction != fraction else max(0.0, min(1.0, float(fraction)))
        if value <= 0:
            continue
        extent = -max(_MIN_EXTENT, value * 360.0)
        canvas.create_arc(
            *box,
            start=start,
            extent=extent,
            style="arc",
            outline=color,
            width=thickness,
            tags=tags or (),
        )
        start += extent


def draw_stacked_bar(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    segments: Sequence[tuple[float, str]],
    *,
    track: str,
    tags: str | tuple[str, ...] | None = None,
) -> None:
    """画一根横向堆叠条（各分量占比首尾相接）。圆头用胶囊基元。

    与 :func:`app.client.rounded.draw_progress_bar` 的区别：那个画**单值**进度条，
    这个画**多分量**分布。分段宽度按总宽 × 占比切；过窄的段仍保留最小可见宽度，
    代价是总长会略微超出 —— 但那比「某一类完全消失」好。
    """
    rounded.draw_pill(canvas, x0, y0, x1, y1, fill=track, tags=tags)
    span = x1 - x0
    height = y1 - y0
    min_w = max(2.0, min(span, height * 0.6))
    cursor = x0
    for fraction, color in segments:
        value = 0.0 if fraction != fraction else max(0.0, min(1.0, float(fraction)))
        if value <= 0:
            continue
        seg_w = max(min_w, span * value)
        seg_w = min(seg_w, x1 - cursor)
        if seg_w <= 0:
            break
        rounded.draw_pill(canvas, cursor, y0, cursor + seg_w, y1, fill=color, tags=tags)
        cursor += seg_w
        if cursor >= x1:
            break


def draw_sparkline(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    values: Sequence[float],
    *,
    color: str,
    baseline: str | None = None,
    tags: str | tuple[str, ...] | None = None,
) -> None:
    """把一串数值画成折线（趋势）。空/单点输入画一条基线，不报错。

    纵轴按输入自身的 ``max`` 归一 —— 这是**画法**不是判定：趋势图看的是形状，
    不是绝对刻度（绝对人数由 KPI 卡承担）。全 0 时画平线。
    """
    if baseline:
        canvas.create_line(x0, y1, x1, y1, fill=baseline, tags=tags or ())
    if not values:
        return
    width = x1 - x0
    height = y1 - y0
    if len(values) == 1:
        canvas.create_line(x0, y1, x1, y1, fill=color, width=2, tags=tags or ())
        return
    lo, hi = min(values), max(values)
    if hi <= lo:
        # 数据持平（刚启动时在线人数恒定）：画在**中间**而不是顶端。
        # 归一化到顶端会让「一直没变」看起来像「一直最高」，是误导。
        mid = y1 - height / 2
        canvas.create_line(x0, mid, x1, mid, fill=color, width=2, tags=tags or ())
        return
    step = width / (len(values) - 1)
    points: list[float] = []
    for index, value in enumerate(values):
        # 留 8% 上下余量，折线不会贴着卡片边缘（贴边会被读成「被裁掉了」）。
        ratio = (value - lo) / (hi - lo)
        points.append(x0 + index * step)
        points.append(y1 - (0.08 + 0.84 * ratio) * height)
    canvas.create_line(*points, fill=color, width=2, smooth=True, tags=tags or ())


def arc_point(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    """环上某角度处的坐标（``degrees`` 用 Tk 的逆时针约定）。

    给「把文字摆在环的某个方向」这类需求用（如中心的在线人数）。
    """
    radians = math.radians(degrees)
    return cx + radius * math.cos(radians), cy - radius * math.sin(radians)
