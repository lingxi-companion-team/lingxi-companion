"""窗口缩放分档：**纯函数**，给 client 的格子尺寸一个随窗口宽度变化的倍率。

为什么这条规则要在 present（而不是 client 里直接 if/else）：
分档阈值（<720 → 紧凑、<1120 → 标准、否则宽敞）是**产品决策**——多大的窗口算
「拥挤」「标准」「宽敞」，要调只动这里，且有测试把边界钉死。client 拿到倍率后
只做一件事：``CELL_WIDTH * scale``，那是纯画法。

缩放**只影响画法**（格子多大、圆角多大、间距多宽），不影响任何判定（谁占格、
什么颜色、汇总怎么算），所以本模块不读 payload、不认识 EmotionLabel，
输入就是一个像素宽度。

v7 调整（2026-09-30）
--------------------
阈值从 ``600 / 900`` 上移到 ``720 / 1120``。理由：状态卡从 74×64 放大到 98×80，
28 格按 5 列排开就要 5×98 + 4×8 = 522px 才不挤，再加 268px 汇总面板与两侧留白，
600px 宽已经放不下「宫格 + 面板」这一行 —— 那时应该切紧凑档而不是硬挤。
1120 则对应「宽敞档卡片放大后仍不换行」的宽度。

三档的**圆角也变**：紧凑档卡片降到 12px。这不是为了省空间，而是 86×70 的卡片
配 16px 圆角会「发胖」得像药丸，失去卡片的方正感。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SCALE_COMPACT",
    "SCALE_ROOMY",
    "SCALE_STANDARD",
    "WIDTH_COMPACT",
    "WIDTH_ROOMY",
    "CardGeometry",
    "card_geometry",
    "columns_at",
    "scale_for_width",
]

#: 窗口宽度分档阈值（像素）。``< WIDTH_COMPACT`` 算紧凑，``< WIDTH_ROOMY`` 算标准，
#: 其余算宽敞。720 是「宫格 + 268px 汇总面板」仍能排下 5 列 98px 卡的下限；
#: 1120 是卡片放到 110px 仍不换行的宽度。
WIDTH_COMPACT = 720
WIDTH_ROOMY = 1120

#: 三档倍率。紧凑档收一点让宫格别溢出；宽敞档放一点利用空间；标准档 1.0 不动。
#: 刻意不用连续缩放（如 width/100）：连续值会让格子尺寸随拖动逐像素抖动，分档
#: 只在跨档时变一次，视觉稳定。
SCALE_COMPACT = 0.88
SCALE_STANDARD = 1.0
SCALE_ROOMY = 1.12


def scale_for_width(width_px: float) -> float:
    """按窗口宽度返回格子尺寸倍率（0.88 / 1.0 / 1.12）。

    边界语义：恰好等于阈值归**下一档**（``<`` 语义）——720px 算标准、1120px 算
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


@dataclass(frozen=True)
class CardGeometry:
    """一档的卡片几何。全部是像素整数，client 直接拿去画。

    ``radius`` 与 ``gap`` 也随档变化 —— 它们和卡片尺寸是一组协同的视觉参数，
    拆开定义会让「紧凑档卡片变小但圆角仍 16px」这种失调组合成为可能。
    """

    #: 卡片宽 / 高（像素）。
    width: int
    height: int
    #: 卡片圆角半径。
    radius: int
    #: 格子之间的间距。
    gap: int
    #: 汇总面板宽度。
    panel_width: int


#: 标准档卡片的基准尺寸（``radius``/``gap`` 也在这里，作为放大倍率的基准）。
_BASE_WIDTH = 98
_BASE_HEIGHT = 80

#: 三档几何表。键是 :func:`scale_for_width` 的返回值。
_GEOMETRY: dict[float, CardGeometry] = {
    SCALE_COMPACT: CardGeometry(width=86, height=70, radius=12, gap=6, panel_width=248),
    SCALE_STANDARD: CardGeometry(
        width=_BASE_WIDTH, height=_BASE_HEIGHT, radius=16, gap=8, panel_width=268
    ),
    SCALE_ROOMY: CardGeometry(width=110, height=90, radius=18, gap=10, panel_width=296),
}


def card_geometry(scale: float) -> CardGeometry:
    """按倍率取卡片几何；未登记的倍率退回标准档（不抛错）。

    client 传进来的 ``scale`` 一般来自 :func:`scale_for_width`，但窗口还没映射时
    ``winfo_width()`` 会返回 1 之类，调用方可能直接给 1.0 —— 所以这里必须容错。
    """
    return _GEOMETRY.get(scale, _GEOMETRY[SCALE_STANDARD])


#: 网格式排布时「宫格区」能用的宽度里，每列至少要留出的像素。
#: 用来把「宽度 → 列数」的决策也收进 present（与 ``grid.columns_for`` 的
#: 上限 8 列配合：这里管的是**宽度装不下时少排几列**）。
_MIN_COLUMN_WIDTH = 92
_MAX_AUTO_COLUMNS = 6


def columns_at(available_px: float, gap: int = 8) -> int:
    """按可用宽度算能排几列（1~:data:`_MAX_AUTO_COLUMNS`）。

    与 ``columns_for``（按人数算方阵）分工不同：那条管「多少个格子最接近正方形」，
    这条管「这块宽度装得下几列」。两者取小才是最终列数 —— 前者防止 30 人时排成
    一条长龙，后者防止窗口窄了还硬排 6 列导致水平溢出。

    非法输入按 1 列处理（最保守，绝不横向溢出）。
    """
    try:
        width = float(available_px)
    except (TypeError, ValueError):
        return 1
    if width <= 0:
        return 1
    count = int((width + gap) // (_MIN_COLUMN_WIDTH + gap))
    return max(1, min(_MAX_AUTO_COLUMNS, count))
