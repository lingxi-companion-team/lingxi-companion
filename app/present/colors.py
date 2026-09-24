"""状态 → 颜色 映射（纯逻辑，无 GUI 依赖）。

四色语义见设计稿 §04.1。这里只产出**色值字符串**，不碰任何绘图库，
因此可以在 headless CI 里满跑（GUI 外壳在 ``app/client/``，整体 omit）。

色值取值与设计稿 §04.1 的图例一致，客户端可直接当 CSS 色值使用。
"""

from __future__ import annotations

from common.perception_types import EmotionLabel

__all__ = [
    "DIM_COLOR",
    "STATE_COLORS",
    "color_for",
    "color_for_count",
    "color_for_key",
    "is_dim",
]

#: 四个状态的展示色。
STATE_COLORS: dict[EmotionLabel, str] = {
    EmotionLabel.FOCUSED: "#3b82c4",
    EmotionLabel.CONFUSED: "#d09a2c",
    EmotionLabel.DISTRACTED: "#c0584f",
    EmotionLabel.UNKNOWN: "#9aa5b1",
}

#: 「该分量人数为 0」时的置灰色（设计稿 §10.1 d1）。
#:
#: 刻意比 ``UNKNOWN`` 的 ``#9aa5b1`` 再浅一档：两者都是灰，但语义不同 ——
#: 一个是「本帧未形成判定」，一个是「该状态当前无人」。同色会让教师误读。
DIM_COLOR = "#c9d0d9"


def color_for(label: EmotionLabel) -> str:
    """取状态色。

    未登记的标签**退回置灰色而不是抛错** —— 展示层不该因为一条异常数据就整个崩掉。
    """
    return STATE_COLORS.get(label, DIM_COLOR)


def is_dim(count: int) -> bool:
    """人数为 0 的分量置灰但仍占位（设计稿 §10.1 d1）。"""
    return count <= 0


def color_for_count(label: EmotionLabel, count: int) -> str:
    """按「状态 + 人数」取色：0 人的分量置灰。"""
    return DIM_COLOR if is_dim(count) else color_for(label)


def color_for_key(label: str) -> str:
    """按**线路格式的字符串键**取状态色。

    客户端从 payload 里拿到的是 ``"focused"`` 这样的字符串而不是枚举，所以需要一个
    str 入口。未登记的键退回置灰色而不是抛错 —— 与 :func:`color_for` 同一取舍：
    展示层不为一条异常数据整个崩掉。这个兜底刻意留在**本模块**（有测试），
    而不是写在 GUI 里 —— ``app/client/`` 整体 omit 于覆盖率，规则写在那儿等于没有护栏。
    """
    try:
        return color_for(EmotionLabel(label))
    except ValueError:
        return DIM_COLOR
