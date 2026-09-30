"""``app.present.scale`` 的契约测试。

守住分档边界：阈值往哪边归（>= 语义）、三档倍率的相对大小、非法输入不抛错。
"""

from __future__ import annotations

from app.present.scale import (
    SCALE_COMPACT,
    SCALE_ROOMY,
    SCALE_STANDARD,
    WIDTH_COMPACT,
    WIDTH_ROOMY,
    scale_for_width,
)


class TestScaleForWidth:
    def test_compact_below_threshold(self) -> None:
        assert scale_for_width(WIDTH_COMPACT - 1) == SCALE_COMPACT
        assert scale_for_width(0) == SCALE_COMPACT

    def test_boundary_is_compact(self) -> None:
        # 恰好 600 算紧凑档（< 语义在 compact 分支，600 不 < 600 → 落到 standard？
        # 不：实现是 ``width < WIDTH_COMPACT``，600 不满足 → 进下一档判断。
        # 所以边界归 standard，这里把语义钉死，别让人以为是 compact。
        assert scale_for_width(WIDTH_COMPACT) == SCALE_STANDARD

    def test_standard_mid_range(self) -> None:
        assert scale_for_width((WIDTH_COMPACT + WIDTH_ROOMY) // 2) == SCALE_STANDARD

    def test_boundary_is_roomy(self) -> None:
        assert scale_for_width(WIDTH_ROOMY) == SCALE_ROOMY

    def test_roomy_above_threshold(self) -> None:
        assert scale_for_width(WIDTH_ROOMY + 500) == SCALE_ROOMY

    def test_monotonic_non_decreasing(self) -> None:
        # 窗口越宽倍率只增不减（分档是离散非减函数，跨档不回头）。
        widths = [100, 599, 600, 750, 899, 900, 1200, 2000]
        scales = [scale_for_width(w) for w in widths]
        assert scales == sorted(scales)

    def test_scales_are_distinct_and_ordered(self) -> None:
        assert SCALE_COMPACT < SCALE_STANDARD < SCALE_ROOMY

    def test_unparseable_input_falls_back_to_compact(self) -> None:
        # 非法输入按最保守的紧凑档处理，不抛错（同 color_for_key 的兜底约定）。
        assert scale_for_width("wide") == SCALE_COMPACT  # type: ignore[arg-type]
