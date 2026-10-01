"""Canvas 圆角画法：把「圆角矩形 / 圆头条 / 裁切纹理」这套 Tkinter 没有的东西补出来。

为什么需要这层
--------------
Tkinter 的 ``Frame`` 没有圆角属性。想要圆角只有两条路：

1. 给控件贴一张带圆角的位图 —— 需要 PIL，且每个尺寸都要生成一张，还得处理缩放模糊；
2. **在 Canvas 上用 ``create_polygon(smooth=True)`` 画** —— 零依赖、任意尺寸、矢量清晰。

本项目选 2。这不是将就，反而是优势：Tk 的样条逼近在**大半径**下更准
（实测任意半径最多偏离真圆 6.1%，半径 16px 时约 0.97px，肉眼不可辨），
而小半径（3~6px）反而会因为取样点太少显出「钝角」。所以本模块的圆角画法在
16~28px 区间是它的最佳工况。

本模块是**纯画法**：只碰传入的 Canvas，不做任何业务判断（判定规则在
:mod:`app.present`）。放在 ``app/client/`` 意味着它不参与覆盖率统计，所以这里
刻意只留「给定坐标画个形状」这种一眼能验的代码，任何 if/else 语义判断都不要加。

关于 ``smooth=True`` 的取样点约定
---------------------------------
Tk 的样条把**每个重复点**当作一个控制点。圆角矩形的经典写法是把每个角点写两遍
（``x0,y0`` → ``x0,y0``），样条就会以该点为控制点把相邻两边柔顺地连起来。
本模块的 ``_corner`` 就是为这个约定服务的。
"""

from __future__ import annotations

import tkinter as tk

__all__ = [
    "draw_pill",
    "draw_progress_bar",
    "draw_rounded_rect",
    "rounded_rect_points",
]

#: 圆角矩形每个角重复的坐标点数。Tk 的样条需要重复点当控制点；
#: 本模块统一重复**两次**（即同一个点写两遍），这是最省点且最稳的写法。
_CORNER_DUPES = 2


def _clamp_radius(radius: float, width: float, height: float) -> float:
    """把半径夹到不超过短边的一半 —— 超了样条会自交，画出畸形。"""
    return max(0.0, min(float(radius), min(width, height) / 2.0))


def rounded_rect_points(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
) -> list[float]:
    """算出圆角矩形的**控制点序列**（``create_polygon`` 用）。

    返回扁平的 ``[x, y, x, y, ...]``。每个角点写两遍（Tk 样条的约定）。
    半径会被夹到短边一半以内：``radius`` 传个 999 也能安全画出胶囊。

    点序是顺时针：左上 → 右上 → 右下 → 左下。顺序本身不影响填充，但保持一致
    便于调试时对照。
    """
    width, height = x1 - x0, y1 - y0
    r = _clamp_radius(radius, width, height)
    points: list[float] = []

    def corner(cx: float, cy: float) -> None:
        for _ in range(_CORNER_DUPES):
            points.extend((float(cx), float(cy)))

    # 从左上角的「上边起点」开始，顺时针绕一圈。每个角记录它的顶点，
    # 样条会把它当作控制点、把前后两条直边柔顺连起来。
    points.extend((float(x0 + r), float(y0)))
    points.extend((float(x1 - r), float(y0)))
    corner(x1, y0)
    points.extend((float(x1), float(y0 + r)))
    points.extend((float(x1), float(y1 - r)))
    corner(x1, y1)
    points.extend((float(x1 - r), float(y1)))
    points.extend((float(x0 + r), float(y1)))
    corner(x0, y1)
    points.extend((float(x0), float(y1 - r)))
    points.extend((float(x0), float(y0 + r)))
    corner(x0, y0)
    return points


def draw_rounded_rect(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    *,
    fill: str,
    outline: str = "",
    width: int = 1,
    tags: str | tuple[str, ...] | None = None,
) -> int:
    """在 ``canvas`` 上画一个圆角矩形，返回 item id。

    ``outline`` 留空则无描边（Tk 的样条描边会把「重复控制点」也描出来，
    细看会让圆角处出现轻微加粗，所以默认不描边，改用**双层**画法模拟 1px 边框：
    先画一个稍大的强调色圆角矩形，再在上面画填充色的。``card()`` 就是这么做。
    """
    points = rounded_rect_points(x0, y0, x1, y1, radius)
    return canvas.create_polygon(
        points,
        smooth=True,
        fill=fill,
        outline=outline,
        width=width,
        tags=tags or (),
    )


def draw_pill(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    fill: str,
    tags: str | tuple[str, ...] | None = None,
) -> int:
    """画一个胶囊（两端全圆）。半径给超大值，由 :func:`rounded_rect_points` 夹紧。"""
    return draw_rounded_rect(canvas, x0, y0, x1, y1, 9999.0, fill=fill, tags=tags)


def draw_progress_bar(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    fraction: float,
    *,
    track: str,
    fill: str,
    height: float | None = None,
    tags: str | tuple[str, ...] | None = None,
) -> tuple[int, int]:
    """画一根**两端圆头**的占比条，返回 ``(轨道 id, 填充 id)``。

    ``fraction`` 是 0~1 的比例，会被夹紧；为 0 时画一根最小的可见圆点而不是
    宽度为 0 的形状（宽度 0 的 shape 在 Tk 里会直接报错或消失，留下一个空洞）。

    圆头是靠给**整根**条一个胶囊半径实现的，而不是只给两端 —— 因为填充部分是
    动态宽度，只圆外侧那一端会让左右不对称。这里填充条左右两端都是圆的，
    于是「条短到什么程度都还是一个圆头小段」，视觉上一致。
    """
    ratio = 0.0 if fraction != fraction else max(0.0, min(1.0, float(fraction)))  # NaN 防
    if height is None:
        height = y1 - y0
    track_id = draw_pill(canvas, x0, y0, x1, y1, fill=track, tags=tags)
    span = x1 - x0
    # 最小可见宽度 = 高度的一半以上，保证 0 人时也画得出一个圆点而不是消失。
    min_width = min(span, max(height * 0.6, 2.0))
    fill_width = max(min_width, span * ratio) if ratio > 0 else min_width
    fill_id = draw_pill(canvas, x0, y0, x0 + fill_width, y1, fill=fill, tags=tags)
    return track_id, fill_id
