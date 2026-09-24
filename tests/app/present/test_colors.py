"""``app.present.colors`` 的测试：四色映射 + 0 人置灰 + 防守分支。"""

from __future__ import annotations

from typing import cast

import pytest

from app.present.colors import (
    DIM_COLOR,
    STATE_COLORS,
    color_for,
    color_for_count,
    color_for_key,
    is_dim,
)
from common.perception_types import EMOTION_LABELS, EmotionLabel


@pytest.mark.parametrize("label", list(EmotionLabel))
def test_every_label_has_a_color(label: EmotionLabel) -> None:
    """``EmotionLabel`` 是封闭枚举，4 个成员必须**全部**有登记色。"""
    assert label in STATE_COLORS
    assert color_for(label) == STATE_COLORS[label]


def test_state_colors_covers_exactly_the_enum() -> None:
    assert set(STATE_COLORS) == set(EmotionLabel)


def test_three_real_emotions_plus_unknown_are_all_present() -> None:
    """``EMOTION_LABELS`` 只有 3 个真情感标签，``UNKNOWN`` 是哨兵 —— 展示层 4 色。"""
    assert len(EMOTION_LABELS) == 3
    assert len(STATE_COLORS) == 4


def test_dim_color_differs_from_unknown_color() -> None:
    """两者都是灰但语义不同（「无人」vs「未形成判定」），同色会让教师误读。"""
    assert DIM_COLOR != color_for(EmotionLabel.UNKNOWN)


def test_unknown_label_falls_back_instead_of_raising() -> None:
    """防守分支：非法标签退回置灰色，展示层不因一条异常数据整体崩掉。"""
    bogus = cast(EmotionLabel, "not-a-label")
    assert color_for(bogus) == DIM_COLOR


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, True), (-1, True), (1, False), (30, False)],
)
def test_is_dim_on_boundaries(count: int, expected: bool) -> None:
    assert is_dim(count) is expected


def test_zero_count_component_is_greyed_out() -> None:
    assert color_for_count(EmotionLabel.FOCUSED, 0) == DIM_COLOR


def test_nonzero_count_component_keeps_the_state_color() -> None:
    assert color_for_count(EmotionLabel.FOCUSED, 3) == STATE_COLORS[EmotionLabel.FOCUSED]


def test_color_for_key_accepts_wire_format_strings() -> None:
    """客户端拿到的 ``label`` 是字符串（payload 直接 JSON 序列化），必须能直接用。"""
    for label in EmotionLabel:
        assert color_for_key(label.value) == STATE_COLORS[label]


def test_color_for_key_falls_back_for_unknown_key() -> None:
    assert color_for_key("not-a-label") == DIM_COLOR
