"""成员明细表的画法（纯画法，无业务判断）。

行数据来自 :func:`app.present.roster.roster_rows`（已测，含筛选）。本模块把
``RosterRow`` 列表画成一张表。

为什么自绘而不用 ``ttk.Treeview``（方案 §2.2 实测）
---------------------------------------------------
Treeview 只能做「文本列 + 整行背景」，**画不了**设计稿要的状态胶囊（圆角 + 色点）
与置信度进度条 —— 而这两样恰是这张表的重点。自绘反而更可控，也与
``window._render_summary`` 的「按坐标画」风格一致。

列宽按比例分配（比例在 :data:`_COLUMN_WEIGHTS`），所以窗口变宽时各列等比拉伸，
不会出现「编号列很宽、状态列挤成一条」。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence

from app.client import rounded
from app.client.theme import (
    BRAND_SUBTLE,
    CARD_BG,
    FONT_BOLD,
    FONT_MICRO,
    FONT_SMALL,
    HAIRLINE,
    HIDDEN_BG,
    INK,
    INK_2,
    MICRO,
    RADIUS_LG,
    RADIUS_SM,
    RADIUS_XS,
    SHADOW,
)
from app.present.roster import RosterRow

__all__ = ["ROW_HEIGHT", "draw_table"]

#: 状态胶囊的底色（中性浅底，见 ``draw_table`` 里的说明）。
CHIP_BG = HIDDEN_BG

#: 单行高度。
ROW_HEIGHT = 38
#: 表头高度。
HEADER_HEIGHT = 32
#: 头像圆直径。
_AVATAR = 24

#: 各列的**相对宽度**（编号 / 状态 / 置信度 / 更新时间）。按比例分配总宽。
_COLUMN_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("编号", 1.15),
    ("状态", 1.0),
    ("置信度", 1.5),
    ("更新时间", 0.95),
)


def _column_edges(x0: float, width: float) -> list[float]:
    """按权重算出各列的左右边界（``len = 列数 + 1``）。"""
    total = sum(weight for _, weight in _COLUMN_WEIGHTS)
    edges = [x0]
    cursor = x0
    for _, weight in _COLUMN_WEIGHTS:
        cursor += width * (weight / total)
        edges.append(cursor)
    return edges


def draw_table(
    canvas: tk.Canvas,
    rows: Sequence[RosterRow],
    x0: float,
    y0: float,
    width: float,
    height: float,
    *,
    hover_index: int | None = None,
) -> int:
    """画整张表（表头 + 行），返回**实际画出的行数**。

    ``hover_index`` 是当前鼠标所在的行号（``None`` = 无）—— 只影响底色，
    判定「鼠标在哪一行」由调用方按坐标算（那是画法层面的命中测试，不是业务规则）。

    行数超出可用高度时**截断**（不做滚动）—— 分页/滚动是后续项；先保证
    画得下、不溢出。空列表画一句空态文案（筛选后为空是常态，不能留一片白）。
    """
    x1 = x0 + width
    # 表格外框用 **RADIUS_LG（20）**，与它上方的「状态分布」卡片同档 —— 中栏两张
    # 大卡半径一致，整列读起来是一套；此前这里是 ``RADIUS_XS + 4``（12），
    # 比上方卡片小一圈，两张卡摞在一起时圆角「一深一浅」很明显。
    rounded.draw_card_shadow(canvas, x0, y0, x1, y0 + height, RADIUS_LG, color=SHADOW)
    rounded.draw_rounded_rect(canvas, x0, y0, x1, y0 + height, RADIUS_LG, fill=CARD_BG)

    edges = _column_edges(x0, width)
    pad = 14
    # ── 表头 ──
    header_cy = y0 + HEADER_HEIGHT / 2
    for index, (name, _) in enumerate(_COLUMN_WEIGHTS):
        canvas.create_text(
            edges[index] + pad,
            header_cy,
            text=name,
            anchor="w",
            fill=MICRO,
            font=FONT_SMALL,
        )
    canvas.create_line(x0, y0 + HEADER_HEIGHT, x1, y0 + HEADER_HEIGHT, fill=HAIRLINE)

    if not rows:
        canvas.create_text(
            (x0 + x1) / 2,
            y0 + HEADER_HEIGHT + 40,
            text="没有符合条件的成员",
            fill=MICRO,
            font=FONT_SMALL,
        )
        return 0

    available = height - HEADER_HEIGHT
    max_rows = max(0, int(available // ROW_HEIGHT))
    drawn = 0
    for index, row in enumerate(rows[:max_rows]):
        top = y0 + HEADER_HEIGHT + index * ROW_HEIGHT
        cy = top + ROW_HEIGHT / 2
        if hover_index == index:
            rounded.draw_rounded_rect(
                canvas, x0 + 6, top + 2, x1 - 6, top + ROW_HEIGHT - 2, RADIUS_SM, fill=BRAND_SUBTLE
            )

        # 头像圆：编号首字。
        avatar_cx = edges[0] + pad + _AVATAR / 2
        canvas.create_oval(
            avatar_cx - _AVATAR / 2,
            cy - _AVATAR / 2,
            avatar_cx + _AVATAR / 2,
            cy + _AVATAR / 2,
            fill=row.color,
            outline="",
        )
        canvas.create_text(avatar_cx, cy, text=row.initial, fill=CARD_BG, font=FONT_BOLD)
        canvas.create_text(
            avatar_cx + _AVATAR / 2 + 9,
            cy,
            text=row.participant_id,
            anchor="w",
            fill=INK,
            font=FONT_SMALL,
        )

        # 状态胶囊：色点 + 中文名。底色用**中性浅底**而不是品牌浅蓝 ——
        # 品牌浅蓝压在「分神」这种暖锈状态上会显得串色；让色点单独承担颜色通道，
        # 文字保持中性，胶囊的形状与位置才是它的识别特征。
        chip_x = edges[1] + pad
        chip_w = 72.0
        rounded.draw_rounded_rect(
            canvas,
            chip_x,
            cy - 11,
            chip_x + chip_w,
            cy + 11,
            RADIUS_XS,
            fill=CHIP_BG,
        )
        canvas.create_oval(chip_x + 10, cy - 3.5, chip_x + 17, cy + 3.5, fill=row.color, outline="")
        canvas.create_text(
            chip_x + 23, cy, text=row.label_text, anchor="w", fill=INK, font=FONT_SMALL
        )

        # 置信度条（已关闭者 confidence 为 None → 画空轨道 + 一个「—」）。
        bar_x0 = edges[2] + pad
        bar_x1 = edges[3] - pad - 34
        if row.confidence is not None:
            rounded.draw_progress_bar(
                canvas,
                bar_x0,
                cy - 3,
                max(bar_x0 + 8, bar_x1),
                cy + 3,
                row.confidence,
                track=HAIRLINE,
                fill=row.color,
                height=6,
            )
            canvas.create_text(
                bar_x1 + 6,
                cy,
                text=f"{row.confidence * 100:.0f}%",
                anchor="w",
                fill=INK_2,
                font=FONT_MICRO,
            )
        else:
            canvas.create_text(bar_x0, cy, text="—", anchor="w", fill=MICRO, font=FONT_SMALL)

        # 更新时间：按新鲜度给不同文字（判定在 present.status，这里只映射文案）。
        age_text = {"fresh": "刚刚", "aging": "稍旧", "stale": "维持中"}.get(row.freshness, "—")
        canvas.create_text(
            edges[3] + pad,
            cy,
            text=age_text,
            anchor="w",
            fill=MICRO if row.freshness != "fresh" else INK_2,
            font=FONT_SMALL,
        )
        drawn += 1
        if index < max_rows - 1:
            canvas.create_line(x0 + 10, top + ROW_HEIGHT, x1 - 10, top + ROW_HEIGHT, fill=HAIRLINE)
    return drawn
