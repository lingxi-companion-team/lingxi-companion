"""``app.present.shape`` 的契约测试。

守住「颜色之外的第二编码通道」：四种状态必须有四种互不相同的形状，否则
这个通道就退化成摆设。
"""

from __future__ import annotations

from app.present.shape import STATE_SHAPES, shape_for, shape_for_key
from common.perception_types import EmotionLabel


class TestShapeFor:
    def test_covers_every_label(self) -> None:
        # 与 colors.STATE_COLORS 同一约定：枚举里加一个状态，这里漏了就编译期
        # 不会报错，只能靠测试守住完备性。
        assert set(STATE_SHAPES) == set(EmotionLabel)

    def test_shapes_are_distinct(self) -> None:
        # 通道存在的意义就是「可区分」——两个状态同形等于没做。
        shapes = [shape_for(label) for label in EmotionLabel]
        assert len(set(shapes)) == len(shapes)

    def test_each_label_maps_to_expected_shape(self) -> None:
        assert shape_for(EmotionLabel.FOCUSED) == "round"
        assert shape_for(EmotionLabel.CONFUSED) == "triangle"
        assert shape_for(EmotionLabel.DISTRACTED) == "diamond"
        assert shape_for(EmotionLabel.UNKNOWN) == "flat"


class TestShapeForKey:
    def test_string_keys(self) -> None:
        assert shape_for_key("focused") == "round"
        assert shape_for_key("confused") == "triangle"
        assert shape_for_key("distracted") == "diamond"
        assert shape_for_key("unknown") == "flat"

    def test_unknown_string_falls_back(self) -> None:
        # 线路上的脏值不该把前端画崩（与 color_for_key 的兜底一致）。
        assert shape_for_key("not-a-label") == "flat"
