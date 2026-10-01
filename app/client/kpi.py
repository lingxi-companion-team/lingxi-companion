"""KPI 卡片行的画法（纯画法，无业务判断）。

数据来自 :func:`app.present.kpi.kpi_cards`（已测）：每张卡一个分量的人数与占比。
本模块只把 ``KpiCard`` 列表摆成一行画出来。

卡片结构（自上而下）
--------------------
::

    ┌─────────────────────────┐
    │ ● 专注            52%   │   ← 色点 + 中文名 + 占比
    │ 14                      │   ← 大数字（用分量色）
    │ ▓▓▓▓▓▓▓░░░░░░░░░        │   ← 占比条
    └─────────────────────────┘

「0 人」的分量整张卡**置灰**（``card.dim``，规则在 present），而不是画一条空条 ——
空条容易被读成「渲染坏了」，灰卡则明确是「当前无人」。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence

from app.client import rounded
from app.client.theme import (
    CARD_BG,
    FONT_LABEL,
    FONT_MICRO,
    FONT_SMALL,
    FONT_TITLE_LG,
    HAIRLINE,
    INK_2,
    MICRO,
    RADIUS_MD,
    SHADOW,
)
from app.present.kpi import KpiCard

__all__ = ["KPI_CARD_HEIGHT", "draw_kpi_row"]

#: KPI 卡片高度（像素）。够放「名 + 占比」一行、大数字一行、占比条一行。
KPI_CARD_HEIGHT = 92

#: 卡片间距。
_KPI_GAP = 12
#: 卡片内边距。
_PAD = 14


def draw_kpi_row(
    canvas: tk.Canvas,
    cards: Sequence[KpiCard],
    x0: float,
    y0: float,
    width: float,
    *,
    height: float = KPI_CARD_HEIGHT,
    gap: float = _KPI_GAP,
) -> None:
    """把 ``cards`` 等宽摆成一行。``x0`` / ``y0`` 是这一行的左上角，``width`` 是总宽。

    卡片数从 ``cards`` 推 —— 不写死 5。宽度按 ``(width - gap*(n-1)) / n`` 均分，
    所以窄屏下卡片自动变窄，不会溢出（是否该收成两行是布局决策，由调用方定）。
    """
    count = len(cards)
    if count <= 0 or width <= 0:
        return
    card_w = (width - gap * (count - 1)) / count
    for index, card in enumerate(cards):
        left = x0 + index * (card_w + gap)
        _draw_kpi_card(canvas, card, left, y0, card_w, height)


def _draw_kpi_card(
    canvas: tk.Canvas,
    card: KpiCard,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> None:
    """画单张 KPI 卡。"""
    x1, y1 = x0 + width, y0 + height
    # 偏移暗块阴影 + 白卡（决策 ⑥：Tk 无阴影 API 的近似画法）。
    rounded.draw_card_shadow(canvas, x0, y0, x1, y1, RADIUS_MD, color=SHADOW)
    rounded.draw_rounded_rect(canvas, x0, y0, x1, y1, RADIUS_MD, fill=CARD_BG)

    dot_r = 4.0
    dot_cx = x0 + _PAD + dot_r
    dot_cy = y0 + _PAD + 4
    canvas.create_oval(
        dot_cx - dot_r,
        dot_cy - dot_r,
        dot_cx + dot_r,
        dot_cy + dot_r,
        fill=card.color,
        outline="",
    )
    canvas.create_text(
        dot_cx + dot_r + 7,
        dot_cy,
        text=card.label,
        anchor="w",
        fill=INK_2,
        font=FONT_SMALL,
    )

    # 大数字用分量色；置灰卡的数字也跟着灰，避免「灰色卡上一个大蓝数字」的错位。
    number_cy = y0 + height * 0.58
    canvas.create_text(
        x0 + _PAD,
        number_cy,
        text=str(card.count),
        anchor="w",
        fill=card.color,
        font=FONT_TITLE_LG,
    )
    canvas.create_text(
        x0 + _PAD + 32,
        number_cy + 3,
        text="人",
        anchor="w",
        fill=MICRO,
        font=FONT_LABEL,
    )
    # 占比与**数字同一行**（右侧）而不是与名称同行：窄卡下「已关闭感知」这种长名称
    # 会与占比数字撞在一起（960px 宽时实测重叠）。数字行更短，撞不上。
    canvas.create_text(
        x1 - _PAD,
        number_cy,
        text=f"{card.ratio * 100:.0f}%",
        anchor="e",
        fill=MICRO,
        font=FONT_MICRO,
    )

    bar_y = y1 - _PAD - 6
    rounded.draw_progress_bar(
        canvas,
        x0 + _PAD,
        bar_y,
        x1 - _PAD,
        bar_y + 6,
        card.ratio,
        track=HAIRLINE,
        fill=card.color,
        height=6,
    )
