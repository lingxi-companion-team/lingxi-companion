"""圆角几何的**机器化守卫**：钉住「角点必须被切掉」这条踩过坑的规则。

为什么要有这个模块
------------------
2026-10-01 实测发现：``rounded.rounded_rect_points`` 沿用的「重复角点」配方
**根本画不出圆角** —— Tk 的样条把相邻重复点当作「此处要尖角」，于是四个角
全是直角。也就是说，界面上一整轮 v7 设计里所有「大圆角卡片」其实都是方块。

这个 bug 能潜伏很久，是因为**没有任何测试会因为它变红**：点序列语法合法、
``create_polygon`` 不报错、截图缩略后直角与圆角也难分。

所以这里对**点序列本身**断言几何性质（纯数学，不创建 Tk root，CI 跑得到）：

1. 角点**不得**出现在点序列里（旧实现里出现两次）；
2. 离角点最近的点必须**离角足够远**（真圆角时约为 ``r·(√2−1) ≈ 0.414r``，
   直角时为 0）；
3. 每个点要么在直边上，要么落在某个角的圆弧上（距离圆心 = r）；
4. 外形尺寸等于传入矩形（不能因为切角而缩小）。

这四条里第 1、2 条就是针对那个 bug 的回归守卫。
"""

from __future__ import annotations

import math

import pytest

from app.client import rounded

__all__ = []

#: 圆弧采样的段数（与实现保持一致；这里只是用来算期望点数）。
_STEPS = 6
#: 浮点比较容差（像素）。
_EPS = 1e-6


def _pairs(points: list[float]) -> list[tuple[float, float]]:
    return list(zip(points[0::2], points[1::2], strict=False))


def _corners(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ── 1. 角点不得出现 ───────────────────────────────────────────────────────


def test_corner_vertices_are_not_in_the_point_list() -> None:
    """**核心回归**：矩形的四个角点一个都不能出现在点序列里。

    旧实现把每个角点写了两遍 —— 那正是「画成直角」的原因。
    """
    pts = _pairs(rounded.rounded_rect_points(10, 20, 210, 140, 40))
    for corner in _corners(10, 20, 210, 140):
        assert corner not in pts, (
            f"角点 {corner} 出现在点序列里 —— 重复角点会让 Tk 画出**直角**，"
            "必须改成沿圆弧采样（见 rounded 模块 docstring）。"
        )


def test_nearest_point_to_corner_is_at_least_the_arc_distance() -> None:
    """离角点最近的点应当离角约 ``r·(√2−1)``。

    真圆角时，45° 处的弧点离角最近，距离 ``r·(√2−1) ≈ 0.414r``；
    直角时距离为 0（角点本身就在序列里）。这条比「角点不在列表」更强：
    即使有人用别的写法把角点挪走，只要切角量不够，这里也会红。
    """
    r = 40.0
    pts = _pairs(rounded.rounded_rect_points(0, 0, 200, 160, r))
    for corner in _corners(0, 0, 200, 160):
        nearest = min(math.dist(corner, p) for p in pts)
        assert nearest >= r * (math.sqrt(2) - 1) - 0.5, (
            f"角 {corner} 的最近点只有 {nearest:.1f}px —— 切角量不足，"
            f"真圆角应 ≥ {r * (math.sqrt(2) - 1):.1f}px"
        )


# ── 2. 每个点都在边界上（直边或圆弧）────────────────────────────────────


@pytest.mark.parametrize(
    "x0,y0,x1,y1,r",
    [
        (0.0, 0.0, 200.0, 160.0, 40.0),
        (10.0, 30.0, 210.0, 190.0, 16.0),
        (0.0, 0.0, 98.0, 80.0, 20.0),
        (5.0, 5.0, 105.0, 25.0, 9999.0),  # 胶囊（半径被夹紧）
    ],
)
def test_every_point_is_on_the_boundary(
    x0: float, y0: float, x1: float, y1: float, r: float
) -> None:
    """每个点要么在四条直边上，要么落在某个角的圆弧上。"""
    eff = min(r, min(x1 - x0, y1 - y0) / 2.0)
    centers = [
        (x1 - eff, y0 + eff),
        (x1 - eff, y1 - eff),
        (x0 + eff, y1 - eff),
        (x0 + eff, y0 + eff),
    ]
    for px, py in _pairs(rounded.rounded_rect_points(x0, y0, x1, y1, r)):
        on_top = abs(py - y0) < _EPS and x0 + eff - _EPS <= px <= x1 - eff + _EPS
        on_bottom = abs(py - y1) < _EPS and x0 + eff - _EPS <= px <= x1 - eff + _EPS
        on_left = abs(px - x0) < _EPS and y0 + eff - _EPS <= py <= y1 - eff + _EPS
        on_right = abs(px - x1) < _EPS and y0 + eff - _EPS <= py <= y1 - eff + _EPS
        on_arc = any(abs(math.dist((px, py), c) - eff) < 0.01 for c in centers)
        assert on_top or on_bottom or on_left or on_right or on_arc, (
            f"点 ({px:.2f}, {py:.2f}) 既不在直边上也不在圆弧上"
        )


# ── 3. 外形尺寸不变 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("r", [0.0, 8.0, 20.0, 9999.0])
def test_bounding_box_equals_the_given_rect(r: float) -> None:
    """切角不能让外形缩水：包围盒必须等于传入矩形。"""
    x0, y0, x1, y1 = 12.0, 34.0, 212.0, 154.0
    pts = _pairs(rounded.rounded_rect_points(x0, y0, x1, y1, r))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert (min(xs), max(xs)) == (x0, x1)
    assert (min(ys), max(ys)) == (y0, y1)


def test_point_count_follows_the_arc_sampling() -> None:
    """点数 = 4 个角 × (段数 + 1)。段数太少会显钝角，太多是浪费。"""
    pts = rounded.rounded_rect_points(0, 0, 200, 160, 30)
    assert len(pts) // 2 == 4 * (_STEPS + 1)


# ── 4. 退化与夹紧 ────────────────────────────────────────────────────────


def test_zero_radius_degrades_to_a_plain_rect() -> None:
    pts = _pairs(rounded.rounded_rect_points(0, 0, 100, 60, 0))
    assert set(pts) == {(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)}


def test_huge_radius_clamps_to_a_pill() -> None:
    """200×40 的胶囊：半径夹到 20，左端极值点只出现在竖直中线上。

    注意左端极值点会**出现两次**（左下角弧的终点与左上角弧的起点重合）——
    这个重复点是**必要的**：Tk 的样条只有穿过控制点才会真的到达 x=0，
    去掉它胶囊会缩窄。所以这里断言「去重后只剩一个点，且在中线上」。
    """
    pts = _pairs(rounded.rounded_rect_points(0, 0, 200, 40, 9999))
    leftmost = [p for p in pts if abs(p[0]) < _EPS]
    assert leftmost, "胶囊左端应当有极值点"
    assert len(set(leftmost)) == 1, f"左端极值点应当只有一个位置，实际 {set(leftmost)}"
    assert all(abs(p[1] - 20.0) < 0.01 for p in leftmost), (
        f"左端极值点必须在竖直中线上（y=20）；实际 {leftmost} —— 说明半径没夹紧"
    )


def test_pill_end_is_a_true_semicircle() -> None:
    """胶囊左端中点必须离左缘 0 距离，而左上角必须被圆掉（不在形状内）。"""
    r = 20.0
    pts = _pairs(rounded.rounded_rect_points(0, 0, 200, 40, 9999))
    # 左端弧心 (r, r)；所有落在左端弧上的点必须距它 r。
    left_arc = [p for p in pts if p[0] < r + _EPS]
    assert left_arc, "左端弧上没有点"
    for p in left_arc:
        assert abs(math.dist(p, (r, r)) - r) < 0.01
