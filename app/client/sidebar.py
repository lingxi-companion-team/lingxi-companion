"""左导航画法（纯画法，无业务判断）。

决策 ③：**单页可用 + 其余占位**。导航列 4 个一级项，但当前只有「实时看板」是真页面，
其余三项画成**置灰占位** —— 点了给一句提示，不切页。这比「假装能点」诚实，
也避免用户以为功能坏了。

为什么置灰项仍然要画出来（而不是直接隐藏）
------------------------------------------
导航是「框架」，它同时承担「你在哪、这里一共有几块」的信息。只画一个可点项，
用户无法判断「是不是还有别的地方」；画出四项但让三项明确置灰，意图就清楚了。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence

from app.client import rounded
from app.client.theme import (
    BRAND,
    BRAND_SUBTLE,
    FONT_LABEL,
    FONT_SMALL,
    INK,
    INK_2,
    INK_3,
    PANEL_BG,
    RADIUS_LG,
    RADIUS_SM,
)

__all__ = [
    "NAV_ITEM_HEIGHT",
    "NAV_MODE_FULL",
    "NAV_MODE_ICONS",
    "draw_sidebar",
    "item_center",
    "item_top",
    "nav_hit_index",
]

#: 单个导航项高度。
NAV_ITEM_HEIGHT = 40
#: 顶部留白（让第一项与顶栏拉开）。
_TOP_PAD = 18
#: 左右内缩。
_SIDE_PAD = 12

#: 两种画法。``full`` = 图标 + 文字；``icons`` = 只画一个字（窄栏，见 ``present.layout``）。
NAV_MODE_FULL = "full"
NAV_MODE_ICONS = "icons"


def draw_sidebar(
    canvas: tk.Canvas,
    labels: Sequence[str],
    x0: float,
    y0: float,
    width: float,
    height: float,
    *,
    active_index: int = 0,
    hover_index: int | None = None,
    mode: str = NAV_MODE_FULL,
) -> None:
    """画左导航。

    ``active_index`` 是当前页面（默认 0 = 实时看板）；其余项**一律画成占位**
    （淡字 + 无强调），它们是否可点由调用方决定 —— 这里只表达「哪一个是当前的」。

    ``mode`` 为 ``icons`` 时只画每项的**首字**（如 实 / 趋 / 名 / 设）并居中 ——
    窄栏（72px）下再画全称会被右缘切掉，截断的文字比不显示更糟。
    """
    # 面板底用 **RADIUS_LG（20）**：与右面板、中栏两张卡、以及学生端汇总面板同档 ——
    # 「大面板 = 20px 圆角」是全局一致的一条规则（见 theme 的圆角分档说明）。
    rounded.draw_rounded_rect(canvas, x0, y0, x0 + width, y0 + height, RADIUS_LG, fill=PANEL_BG)
    icons_mode = mode == NAV_MODE_ICONS
    for index, label in enumerate(labels):
        top = y0 + _TOP_PAD + index * NAV_ITEM_HEIGHT
        if top + NAV_ITEM_HEIGHT > y0 + height:
            break
        cy = top + NAV_ITEM_HEIGHT / 2
        is_active = index == active_index
        inset = 6 if icons_mode else _SIDE_PAD
        if is_active or hover_index == index:
            rounded.draw_rounded_rect(
                canvas,
                x0 + inset,
                top + 4,
                x0 + width - inset,
                top + NAV_ITEM_HEIGHT - 4,
                RADIUS_SM,
                fill=BRAND_SUBTLE if is_active else PANEL_BG,
            )
        text_color = INK if is_active else (INK_2 if hover_index == index else INK_3)
        if icons_mode:
            # 只画首字，居中；选中项加粗（颜色之外的第二通道）。
            canvas.create_text(
                x0 + width / 2,
                cy,
                text=label[:1],
                fill=text_color,
                font=FONT_LABEL if is_active else FONT_SMALL,
            )
            continue
        if is_active:
            # 选中竖线：贴在项的左侧，圆头。
            rounded.draw_pill(
                canvas,
                x0 + _SIDE_PAD + 2,
                top + 11,
                x0 + _SIDE_PAD + 5,
                top + NAV_ITEM_HEIGHT - 11,
                fill=BRAND,
            )
        canvas.create_text(
            x0 + _SIDE_PAD + 18,
            cy,
            text=label,
            anchor="w",
            fill=text_color,
            font=FONT_SMALL,
        )


def nav_hit_index(
    labels: Sequence[str],
    y0: float,
    height: float,
    y: float,
) -> int | None:
    """把画布 y 坐标换成导航项下标；不在任何项上返回 ``None``。

    这是**画法层面的命中测试**（把像素换成行号），不是业务规则 —— 所以留在 client 层。
    行号 → 是否可点，仍由调用方按 ``active_index`` 判断。
    """
    if y < y0 + _TOP_PAD:
        return None
    offset = y - (y0 + _TOP_PAD)
    index = int(offset // NAV_ITEM_HEIGHT)
    if 0 <= index < len(labels) and y0 + _TOP_PAD + index * NAV_ITEM_HEIGHT < y0 + height:
        return index
    return None


def item_top(y0: float, index: int) -> float:
    """第 ``index`` 项的顶边 y（供 hover 判定复用）。"""
    return y0 + _TOP_PAD + index * NAV_ITEM_HEIGHT


def item_center(y0: float, index: int) -> float:
    """第 ``index`` 项的中心 y。"""
    return item_top(y0, index) + NAV_ITEM_HEIGHT / 2
