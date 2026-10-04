"""Canvas 圆角画法：把「圆角矩形 / 圆头条 / 裁切纹理」这套 Tkinter 没有的东西补出来。

为什么需要这层
--------------
Tkinter 的 ``Frame`` 没有圆角属性。想要圆角只有两条路：

1. 给控件贴一张带圆角的位图 —— 需要 PIL，且每个尺寸都要生成一张，还得处理缩放模糊；
2. **在 Canvas 上用 ``create_polygon(smooth=True)`` 画** —— 零依赖、任意尺寸、矢量清晰。

本项目选 2。

⚠️ 2026-10-01 纠正：**「重复角点」写法画不出圆角**
--------------------------------------------------
本模块原先沿用了一个流传很广的 Tk 圆角配方：把每个角点**写两遍**，
指望 ``smooth=True`` 的样条以它为控制点把两条边柔顺连起来。

**这个配方是错的** —— 实测（``Canvas.find_overlapping`` 精确命中测试，见下）
得到的四个角是**纯直角**，一点都没切。原因：Tk 的样条把**相邻重复点**当作
「此处要尖角」，于是重复角点恰好把圆角写成了尖角。

实测数据（画一个 120×120、传入半径 40 的矩形，沿顶边逐行量「切角量」，
理想圆弧应给出 ``33.7 24.8 18.9 13.5 8.8 5.4 2.5 0.8``）::

    旧写法（角点重复 2 次）   0.0  0.0  0.0  0.0  0.0  0.0  0.0  0.0   ← 直角！
    经典配方（角点 1 次, off=r） 13.0 7.0 4.0 1.5 0.5 0.0 0.0 0.0  ← 半径只有 r/3
    圆弧采样（本实现, 6 段）   34.0 25.0 19.0 13.5 9.0 5.5 2.5 1.0  ← 与理想重合

所以本实现改成**沿每个角的 1/4 圆弧密集取点**：点落在真圆弧上，Tk 的样条
（它穿过相邻控制点的中点）自然贴合圆弧，实测与理想圆的偏差 ≤1px。

代价是点数变多（每角 7 个点，共 28 个），但 Canvas 多边形这点开销可以忽略。

关于「半径越大越平滑」
----------------------
Tk 的样条在**大半径**下更准；小半径（3~6px）取样点太少反而显「钝角」。
所以本模块的圆角在 16~28px 区间是它的最佳工况。

本模块是**纯画法**：只碰传入的 Canvas，不做任何业务判断（判定规则在
:mod:`app.present`）。放在 ``app/client/`` 意味着它不参与覆盖率统计，所以这里
刻意只留「给定坐标画个形状」这种一眼能验的代码，任何 if/else 语义判断都不要加。
"""

from __future__ import annotations

import math
import tkinter as tk

__all__ = [
    "draw_card_shadow",
    "draw_pill",
    "draw_progress_bar",
    "draw_rounded_rect",
    "rounded_rect_points",
]

#: 每个角的 1/4 圆弧采样段数。实测 6 段已与理想圆重合（偏差 ≤1px），
#: 再加密收益递减 —— 见模块 docstring 的对照表。
_CORNER_STEPS = 6


def _clamp_radius(radius: float, width: float, height: float) -> float:
    """把半径夹到不超过短边的一半 —— 超了圆弧会互相穿透，画出畸形。"""
    return max(0.0, min(float(radius), min(width, height) / 2.0))


def rounded_rect_points(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
) -> list[float]:
    """算出圆角矩形的**控制点序列**（``create_polygon`` 用）。

    返回扁平的 ``[x, y, x, y, ...]``。半径会被夹到短边一半以内：``radius``
    传个 999 也能安全画出胶囊。

    点序是顺时针：右上角弧 → 右下角弧 → 左下角弧 → 左上角弧。四条直边由相邻
    两段弧的端点自然连成，不需要额外加点。
    """
    width, height = x1 - x0, y1 - y0
    r = _clamp_radius(radius, width, height)
    if r <= 0.0:
        # 退化情形：没有半径就退回一个普通矩形（仍然按顺时针给点）。
        return [
            float(x0),
            float(y0),
            float(x1),
            float(y0),
            float(x1),
            float(y1),
            float(x0),
            float(y1),
        ]

    points: list[float] = []

    def arc(cx: float, cy: float, start_deg: float) -> None:
        """从 ``start_deg`` 起，顺时针扫 90°，沿圆弧取 ``_CORNER_STEPS+1`` 个点。"""
        for index in range(_CORNER_STEPS + 1):
            angle = math.radians(start_deg + 90.0 * index / _CORNER_STEPS)
            points.extend((cx + r * math.cos(angle), cy + r * math.sin(angle)))

    # 屏幕坐标 y 向下。四个圆心各自内缩 r。
    arc(x1 - r, y0 + r, -90.0)  # 右上：(x1-r,y0) -> (x1,y0+r)
    arc(x1 - r, y1 - r, 0.0)  # 右下：(x1,y1-r) -> (x1-r,y1)
    arc(x0 + r, y1 - r, 90.0)  # 左下：(x0+r,y1) -> (x0,y1-r)
    arc(x0 + r, y0 + r, 180.0)  # 左上：(x0,y0+r) -> (x0+r,y0)
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


#: 「偏移暗块」阴影的默认参数。Finserv 那种轻阴影是 ``0 1px 3px``，
#: Tk 画不出真模糊，用「下移 1px、外扩 1px 的硬边暗块」近似（见 ``draw_card_shadow``）。
SHADOW_DX = 0
SHADOW_DY = 1
SHADOW_SPREAD = 1


def draw_card_shadow(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    *,
    color: str,
    dx: float = SHADOW_DX,
    dy: float = SHADOW_DY,
    spread: float = SHADOW_SPREAD,
    tags: str | tuple[str, ...] | None = None,
) -> int:
    """画「偏移暗块」阴影：在卡片**下方**垫一个更大、略微下移的圆角矩形。

    为什么是这个做法（方案 §2.1 方案 A）
    ------------------------------------
    Tkinter **没有阴影 API**（实测窗口 ``attributes()`` 只有
    ``-alpha/-transparentcolor/-disabled/-fullscreen/-toolwindow/-topmost``，
    Canvas 也只有 ``arc/bitmap/image/line/oval/polygon/rectangle/text/window``，
    无渐变、无滤镜）。真阴影的三条路里：多层描边会画成「光晕」显得脏；
    预生成位图每尺寸一张、要缓存还要处理缩放模糊，成本最高。
    本项目已有「双层圆角矩形模拟 1px 边」的成熟做法，**偏移暗块是同一思路的延伸**：
    零新依赖，得到一层硬边扁平投影，是 Finserv 轻阴影的合理近似。

    调用顺序很重要：**先画这个，再画卡片本体**，否则暗块会盖住卡片。
    返回值是暗块的 item id（调用方一般不关心，但保留以便将来做动画）。
    """
    return draw_rounded_rect(
        canvas,
        x0 - spread + dx,
        y0 - spread + dy,
        x1 + spread + dx,
        y1 + spread + dy,
        radius + spread,
        fill=color,
        tags=tags,
    )
