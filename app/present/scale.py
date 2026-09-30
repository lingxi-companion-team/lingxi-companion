"""窗口缩放分档：**纯函数**，给 client 的格子尺寸一个随窗口宽度变化的倍率。

为什么这条规则要在 present（而不是 client 里直接 if/else）：
分档阈值（<600 → 0.85、<900 → 1.0、否则 1.15）是**产品决策**——多大的窗口算
「拥挤」「标准」「宽敞」，要调只动这里，且有测试把边界钉死。client 拿到倍率后
只做一件事：``CELL_WIDTH * scale``，那是纯画法。

缩放**只影响画法**（格子多大），不影响任何判定（谁占格、什么颜色、汇总怎么算），
所以本模块不读 payload、不认识 EmotionLabel，输入就是一个像素宽度。
"""

from __future__ import annotations

__all__ = [
    "SCALE_COMPACT",
    "SCALE_ROOMY",
    "SCALE_STANDARD",
    "WIDTH_COMPACT",
    "WIDTH_ROOMY",
    "scale_for_width",
]

#: 窗口宽度分档阈值（像素）。``< WIDTH_COMPACT`` 算紧凑，``< WIDTH_ROOMY`` 算标准，
#: 其余算宽敞。两个值是「一眼还能扫完」的实用分界：600px 大约挤下 6 列带面板，
#: 900px 是 28 格 + 汇总面板不挤的典型宽度。
WIDTH_COMPACT = 600
WIDTH_ROOMY = 900

#: 三档倍率。紧凑档收一点让宫格别溢出；宽敞档放一点利用空间；标准档 1.0 不动。
#: 刻意不用连续缩放（如 width/100）：连续值会让格子尺寸随拖动逐像素抖动，分档
#: 只在跨档时变一次，视觉稳定。
SCALE_COMPACT = 0.85
SCALE_STANDARD = 1.0
SCALE_ROOMY = 1.15


def scale_for_width(width_px: float) -> float:
    """按窗口宽度返回格子尺寸倍率（0.85 / 1.0 / 1.15）。

    边界语义：恰好等于阈值归**下一档**（``<`` 语义）——600px 算标准、900px 算
    宽敞，与 :func:`columns_for` 的夹逼风格一致，别让边界值在档间抖动。
    非法输入（非数值）按最保守的紧凑档处理，不抛错。
    """
    try:
        width = float(width_px)
    except (TypeError, ValueError):
        return SCALE_COMPACT
    if width < WIDTH_COMPACT:
        return SCALE_COMPACT
    if width < WIDTH_ROOMY:
        return SCALE_STANDARD
    return SCALE_ROOMY
