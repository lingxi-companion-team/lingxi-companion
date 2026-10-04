"""``app.present.colors`` 的测试：四色映射 + 0 人置灰 + 防守分支。

v8 追加了 **fill / on_fill** 两条通道的测试。ink 与 fill 的分工见
``app/present/colors.py`` 的模块 docstring：ink 承载文字（4.5:1），
fill 只承载色相（3:1 + 描边）。这里钉住三件事：

- 两个 dict **同键同序**（漏一个状态就会红，而不是等到界面上少个颜色）；
- ``fill_for_key`` 的兜底与 ``color_for_key`` 一致（否则同一个非法键在
  两个通道里会返回不同族的颜色）；
- ``on_fill`` 选出的前景色确实压得住（真正算对比度，不是看它返回了非空串）。
"""

from __future__ import annotations

from typing import cast

import pytest

from app.present.colors import (
    DIM_COLOR,
    ON_DARK_TEXT,
    ON_LIGHT_TEXT,
    STATE_COLORS,
    STATE_FILLS,
    color_for,
    color_for_count,
    color_for_key,
    fill_for,
    fill_for_key,
    is_dim,
    on_fill,
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


# ── v8：fill 通道 ─────────────────────────────────────────────────────────


def test_fills_cover_exactly_the_same_labels_as_inks() -> None:
    """两个通道必须**同键**：漏一个状态就等于「这个状态没有颜色标签」。

    这条比「fill 有几个键」更重要 —— 它防的是「加了第 5 个状态只补了 ink」。
    """
    assert set(STATE_FILLS) == set(STATE_COLORS)


@pytest.mark.parametrize("label", list(EmotionLabel))
def test_fill_for_matches_the_table(label: EmotionLabel) -> None:
    assert fill_for(label) == STATE_FILLS[label]


@pytest.mark.parametrize("label", list(EmotionLabel))
def test_fill_for_key_accepts_wire_format_strings(label: EmotionLabel) -> None:
    assert fill_for_key(label.value) == STATE_FILLS[label]


def test_fill_for_key_falls_back_the_same_way_as_color_for_key() -> None:
    """同一个非法键，两个通道必须落到**同一个**兜底色。

    若一个退 ``DIM_COLOR``、另一个抛错，界面上就会出现「文字有颜色、底色没了」
    的半残状态 —— 这类不一致比直接崩还难查。
    """
    assert fill_for_key("not-a-label") == DIM_COLOR
    assert fill_for_key("not-a-label") == color_for_key("not-a-label")


def test_fill_for_falls_back_instead_of_raising() -> None:
    bogus = cast(EmotionLabel, "not-a-label")
    assert fill_for(bogus) == DIM_COLOR


def test_ink_and_fill_are_different_values_per_state() -> None:
    """ink 与 fill 分工不同（一个给文字、一个给色相），不该是同一个值。

    真出现相等，说明有人把「压暗到 4.5 的色」直接拿去当标签底了 —— 那时
    ``on_fill`` 会选白字而对比度只有 5 左右，色相也丢了饱和度。
    """
    for label in EmotionLabel:
        assert STATE_COLORS[label] != STATE_FILLS[label]


# ── v8：on_fill ───────────────────────────────────────────────────────────


def _contrast(foreground: str, background: str) -> float:
    """本模块自带的对比度实现 —— **刻意不 import 测试工具**。

    ``tests/app/present/test_skin.py`` 覆盖的是 v8 皮肤，present 层的测试
    不该反向依赖它（层级会乱）。公式只有六行，各写一份比跨层 import 更省事，
    且这里要断的是「同一个结果」，两处独立实现反而能互相印证。
    """

    def luminance(color: str) -> float:
        raw = color.lstrip("#")
        linear = []
        for index in (0, 2, 4):
            srgb = int(raw[index : index + 2], 16) / 255.0
            linear.append(srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4)
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first, second = luminance(foreground), luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


@pytest.mark.parametrize("label", list(EmotionLabel))
def test_on_fill_returns_a_readable_foreground(label: EmotionLabel) -> None:
    """四个 fill 上的前景色都必须 ≥4.5:1 —— 这是「颜色标签」能用的前提。

    黄 fill（``#f2c200``）是这条测试的主要目标：它亮度高，只有深字压得住；
    红 fill（``#dc3545``）相反，只有白字压得住。所以 ``on_fill`` 必须**两个方向**
    都会选，写成「恒返回白色」会在这里红两条。
    """
    fill = STATE_FILLS[label]
    foreground = on_fill(fill)
    ratio = _contrast(foreground, fill)
    assert ratio >= 4.5, f"{label.value} fill {fill} 上 {foreground} 只有 {ratio:.2f}:1"


def test_on_fill_picks_both_directions_across_the_palette() -> None:
    """跨四个 fill，白/深两种前景**都出现过** —— 防止 ``on_fill`` 退化成常量函数。"""
    chosen = {on_fill(fill) for fill in STATE_FILLS.values()}
    assert chosen == {ON_LIGHT_TEXT, ON_DARK_TEXT}


def test_on_fill_only_ever_returns_one_of_the_two_candidates() -> None:
    """返回值必须来自受控候选集，否则「4.5 达标」的断言就失去意义。"""
    for fill in STATE_FILLS.values():
        assert on_fill(fill) in (ON_LIGHT_TEXT, ON_DARK_TEXT)


def test_on_fill_white_on_white_and_dark_on_black() -> None:
    """两个极端值的方向必须对：白底给深字、黑底给白字。"""
    assert on_fill("#ffffff") == ON_DARK_TEXT
    assert on_fill("#000000") == ON_LIGHT_TEXT


def test_on_fill_does_not_claim_a_mid_grey_is_safe() -> None:
    """``on_fill`` 只保证「两害相权取其轻」，**不保证**达标 —— 钉住这个边界。

    中灰 ``#808080`` 两个候选都只有 3.9 左右。这条测试的意义是说明：
    达标的责任在 **fill 的取值**（由 ``test_contrast.py`` 验），不在 ``on_fill``。
    若哪天有人给中灰 fill 配 ``on_fill`` 就以为万事大吉，这条会提醒他方向错了。
    """
    assert _contrast(on_fill("#808080"), "#808080") < 4.5
