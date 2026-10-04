"""``app.present.scale`` 的契约测试。

守住分档边界：阈值往哪边归（``<`` 语义）、三档倍率的相对大小、几何表完备、
非法输入不抛错。
"""

from __future__ import annotations

import pytest

from app.present.scale import (
    SCALE_COMPACT,
    SCALE_ROOMY,
    SCALE_STANDARD,
    WIDTH_COMPACT,
    WIDTH_ROOMY,
    card_geometry,
    columns_at,
    scale_for_width,
)


class TestScaleForWidth:
    def test_compact_below_threshold(self) -> None:
        assert scale_for_width(WIDTH_COMPACT - 1) == SCALE_COMPACT
        assert scale_for_width(0) == SCALE_COMPACT

    def test_boundary_is_standard_not_compact(self) -> None:
        # 实现是 ``width < WIDTH_COMPACT``，恰好 720 不满足 → 落进标准档。
        # 把边界语义钉死，别让人以为是 compact。
        assert scale_for_width(WIDTH_COMPACT) == SCALE_STANDARD

    def test_standard_mid_range(self) -> None:
        assert scale_for_width((WIDTH_COMPACT + WIDTH_ROOMY) // 2) == SCALE_STANDARD

    def test_boundary_is_roomy(self) -> None:
        assert scale_for_width(WIDTH_ROOMY) == SCALE_ROOMY

    def test_roomy_above_threshold(self) -> None:
        assert scale_for_width(WIDTH_ROOMY + 500) == SCALE_ROOMY

    def test_monotonic_non_decreasing(self) -> None:
        # 窗口越宽倍率只增不减（分档是离散非减函数，跨档不回头）。
        widths = [100, 719, 720, 900, 1119, 1120, 1400, 2000]
        scales = [scale_for_width(w) for w in widths]
        assert scales == sorted(scales)

    def test_scales_are_distinct_and_ordered(self) -> None:
        assert SCALE_COMPACT < SCALE_STANDARD < SCALE_ROOMY

    def test_unparseable_input_falls_back_to_compact(self) -> None:
        # 非法输入按最保守的紧凑档处理，不抛错（同 color_for_key 的兜底约定）。
        assert scale_for_width("wide") == SCALE_COMPACT  # type: ignore[arg-type]


class TestCardGeometry:
    """卡片几何表：三档齐备、逐档放大、圆角随尺寸降档。"""

    def test_every_scale_has_geometry(self) -> None:
        for scale in (SCALE_COMPACT, SCALE_STANDARD, SCALE_ROOMY):
            geo = card_geometry(scale)
            assert geo.width > 0 and geo.height > 0 and geo.radius > 0

    def test_geometry_grows_with_scale(self) -> None:
        small = card_geometry(SCALE_COMPACT)
        mid = card_geometry(SCALE_STANDARD)
        big = card_geometry(SCALE_ROOMY)
        assert small.width < mid.width < big.width
        assert small.height < mid.height < big.height
        assert small.panel_width < mid.panel_width < big.panel_width

    def test_unknown_scale_falls_back_to_standard(self) -> None:
        # 窗口未映射时 winfo_width() 给 1，调用方可能顺手传 1.0；必须容错不抛错。
        assert card_geometry(1.0) == card_geometry(SCALE_STANDARD)
        assert card_geometry(float("nan")) == card_geometry(SCALE_STANDARD)  # type: ignore[arg-type]

    @pytest.mark.parametrize("scale", [SCALE_COMPACT, SCALE_STANDARD, SCALE_ROOMY])
    def test_radius_stays_under_the_pill_line(self, scale: float) -> None:
        """圆角不得超过短边的 20% —— 越线卡片就读成「药丸」而不是卡片。

        这条是被设计评审逼出来的：v6 只给了 16px 通吃三档，紧凑档卡片小
        （86×70）时 16/70 = 22.9% 已经越过药丸线，所以紧凑档必须降到 12px。
        """
        geo = card_geometry(scale)
        share = geo.radius / min(geo.width, geo.height)
        assert share <= 0.20, f"倍率 {scale} 的圆角占短边 {share:.1%}，超了药丸线"

    @pytest.mark.parametrize("scale", [SCALE_COMPACT, SCALE_STANDARD, SCALE_ROOMY])
    def test_radius_is_big_enough_for_the_spline(self, scale: float) -> None:
        """圆角也不能太小：Tk 的样条在小半径下会显出钝角，≥12px 才平滑。"""
        assert card_geometry(scale).radius >= 12


class TestColumnsAt:
    """按可用宽度算列数：窄了要少排，绝不横向溢出。"""

    def test_wide_room_uses_max_columns(self) -> None:
        assert columns_at(2000) == 6

    def test_narrow_room_falls_to_one(self) -> None:
        assert columns_at(10) == 1

    def test_zero_or_negative_is_one(self) -> None:
        assert columns_at(0) == 1
        assert columns_at(-50) == 1

    def test_unparseable_input_is_one(self) -> None:
        assert columns_at("wide") == 1  # type: ignore[arg-type]

    def test_never_exceeds_max(self) -> None:
        for width in (100, 500, 900, 1500, 5000):
            assert 1 <= columns_at(width) <= 6

    def test_columns_are_monotonic_non_decreasing(self) -> None:
        widths = [0, 50, 100, 200, 400, 800, 1600, 3200]
        counts = [columns_at(w) for w in widths]
        assert counts == sorted(counts)
