"""``app.present.skin`` 的可达性守卫。

与 ``tests/app/client/test_contrast.py`` 同源、同一套门槛、同一套公式 ——
刻意**不 import** 那边的实现：两套皮肤是两套设计，各自的守卫也该各自独立，
共用一份 helper 只会让「改了这边、那边悄悄跟着变」变得可能。

门槛（与 ``test_contrast.py`` 一致）
------------------------------------
- 文字（承载信息）：**4.5:1**（WCAG 1.4.3 AA）
- 图形元素（描边、色条）：**3:1**（WCAG 1.4.11）
- 纯装饰（虚线边框、分隔线）：不按 3:1 验，但断言**别浅到看不见**（≥1.8）——
  「没画」与「画了看不见」在视觉上是同一件事。

这个模块还顺手验一件事：**四个状态 ink 压在 v8 的新表面上仍然达标**。
状态色本身由 ``app/present/colors.py`` 与 ``test_colors.py`` 守着，
但「它压在哪个底上」是皮肤的职责 —— 换了画布色而没回头验状态色，是很容易漏的一步。
"""

from __future__ import annotations

import re

import pytest

from app.present import skin
from app.present.colors import (
    CLOSED_COLOR,
    DIM_COLOR,
    STATE_COLORS,
    STATE_FILLS,
    on_fill,
)
from common.perception_types import EmotionLabel

__all__ = []

#: 三个底色：卡片白、画布、框架面（顶栏/侧栏）。
CARD = skin.CARD_BG
CANVAS = skin.BG
PANEL = skin.PANEL_BG

TEXT_MIN = 4.5
GRAPHIC_MIN = 3.0
DECOR_MIN = 1.8

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _channel(value: int) -> float:
    srgb = value / 255.0
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    assert _HEX_RE.match(color), f"{color!r} 不是 #rrggbb"
    raw = color.lstrip("#")
    red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(first: str, second: str) -> float:
    """两色的 WCAG 对比度（1.0 ~ 21.0）。"""
    a, b = _luminance(first), _luminance(second)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def _hue(color: str) -> float:
    """色相角（0~360）。用来判「两个色是不是同一个色相」。"""
    raw = color.lstrip("#")
    red, green, blue = (int(raw[index : index + 2], 16) / 255.0 for index in (0, 2, 4))
    high, low = max(red, green, blue), min(red, green, blue)
    delta = high - low
    if delta == 0:
        return 0.0
    if high == red:
        hue = ((green - blue) / delta) % 6
    elif high == green:
        hue = (blue - red) / delta + 2
    else:
        hue = (red - green) / delta + 4
    return hue * 60.0


def _chroma(color: str) -> float:
    """彩度（max - min）。用来判「这个色有没有颜色，还是灰的」。"""
    raw = color.lstrip("#")
    red, green, blue = (int(raw[index : index + 2], 16) / 255.0 for index in (0, 2, 4))
    return max(red, green, blue) - min(red, green, blue)


# ── 表面层次 ────────────────────────────────────────────────────────────


def test_every_surface_is_a_hex_colour() -> None:
    for name in ("BG", "CARD_BG", "PANEL_BG", "HEADER_BG", "SHADOW", "HAIRLINE"):
        assert _HEX_RE.match(getattr(skin, name)), name


def test_card_lifts_off_the_canvas() -> None:
    """一屏卡片要靠这条差「一块块」立起来；投影仪把 8% 压成 0 是常事。"""
    assert contrast(CARD, CANVAS) >= 1.12


def test_the_canvas_is_not_pure_white() -> None:
    assert CANVAS != "#ffffff"


def test_the_panel_is_lighter_than_the_canvas_but_darker_than_the_card() -> None:
    """三档层次：白卡 > 面板 > 画布（亮度）。"""
    assert _luminance(CARD) > _luminance(PANEL) > _luminance(CANVAS)


def test_the_panel_is_distinguishable_from_the_card() -> None:
    assert contrast(CARD, PANEL) >= 1.05


def test_the_header_is_the_panel_surface() -> None:
    """顶栏与侧栏同值，让「顶栏 + 侧栏」读起来是一个连续的框架面。"""
    assert skin.HEADER_BG == skin.PANEL_BG


def test_the_shadow_is_a_thin_edge_not_a_black_block() -> None:
    """阴影要读成「卡片下缘的一点厚度」，太深就成了硬边黑块。"""
    ratio = contrast(skin.SHADOW, CANVAS)
    assert 1.08 <= ratio <= 1.35, ratio


# ── 文字 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("surface", [CARD, CANVAS, PANEL])
@pytest.mark.parametrize("name", ["INK_HI", "INK", "INK_2", "MICRO"])
def test_informative_text_clears_aa_on_every_surface(name: str, surface: str) -> None:
    ratio = contrast(getattr(skin, name), surface)
    assert ratio >= TEXT_MIN, f"{name} 压 {surface} 只有 {ratio:.2f}:1"


def test_ink_3_is_line_only_and_still_visible() -> None:
    """``INK_3`` 刻意只画线不承载信息，所以不按 4.5 验 —— 但仍要看得见。"""
    assert contrast(skin.INK_3, CARD) >= 1.2
    assert contrast(skin.INK_3, CARD) < TEXT_MIN, "它已经不该被当成文字色用了"


def test_the_ink_ladder_is_monotonic() -> None:
    """四级文字的深浅顺序必须单调，否则「次级」会比「正文」还重。"""
    ladder = [skin.INK_HI, skin.INK, skin.INK_2, skin.MICRO, skin.INK_3]
    luminances = [_luminance(color) for color in ladder]
    assert luminances == sorted(luminances), "文字层级不是单调变浅的"


# ── 品牌 / 交互 ─────────────────────────────────────────────────────────


def test_white_text_on_the_brand_clears_aa() -> None:
    assert contrast("#ffffff", skin.BRAND) >= TEXT_MIN


def test_brand_hover_is_darker_than_brand() -> None:
    """亮色主题下悬停要**变深** —— 变浅会让白字掉到 4.05:1，不达标。"""
    assert _luminance(skin.BRAND_HOVER) < _luminance(skin.BRAND)
    assert contrast("#ffffff", skin.BRAND_HOVER) >= TEXT_MIN


def test_brand_pressed_is_darker_still() -> None:
    assert _luminance(skin.BRAND_ACTIVE) < _luminance(skin.BRAND_HOVER)
    assert contrast("#ffffff", skin.BRAND_ACTIVE) >= TEXT_MIN


def test_the_disabled_brand_takes_dark_text_not_white() -> None:
    """浅蓝底配白字只有 1.8 左右 —— 禁用态只能配深色文字。"""
    assert contrast(skin.INK, skin.BRAND_DISABLED) >= TEXT_MIN
    assert contrast("#ffffff", skin.BRAND_DISABLED) < TEXT_MIN


def test_brand_subtle_is_darker_than_the_canvas() -> None:
    """选中态在亮色主题下要变深，否则「选中了」看不出来。"""
    assert _luminance(skin.BRAND_SUBTLE) < _luminance(CANVAS)


# ── 「已隐藏」格 ────────────────────────────────────────────────────────


def test_hidden_cell_text_clears_aa_on_its_own_background() -> None:
    assert contrast(skin.HIDDEN_FG, skin.HIDDEN_BG) >= TEXT_MIN


def test_hidden_background_recedes_into_the_canvas() -> None:
    """「已隐藏」要比画布**浅**一档 —— 它是「退到背景里」，不是「凹进去」。

    刻意不用红/警示色：公开开关是**权利**不是过失，做成醒目提示等于对选择不公开的人施压。
    """
    assert _luminance(skin.HIDDEN_BG) > _luminance(CANVAS)


def test_the_hidden_background_is_not_a_warning_colour() -> None:
    """它必须是中性色：彩度压得很低，且色相不落在红区。"""
    assert _chroma(skin.HIDDEN_BG) < 0.15
    assert not (340 <= _hue(skin.HIDDEN_BG) or _hue(skin.HIDDEN_BG) <= 20)


# ── 「未接入」态 ────────────────────────────────────────────────────────


def test_unwired_text_clears_aa_on_its_own_background() -> None:
    assert contrast(skin.UNWIRED_FG, skin.UNWIRED_BG) >= TEXT_MIN
    assert contrast(skin.UNWIRED_FG, CARD) >= TEXT_MIN


def test_unwired_background_reads_as_another_surface_not_a_stain() -> None:
    """压白卡的差要与「白卡 vs 画布」同一量级 —— 读起来是另一种表面，不是污渍。"""
    ratio = contrast(skin.UNWIRED_BG, CARD)
    assert 1.10 <= ratio <= 1.30, ratio


def test_unwired_background_is_neutral_not_warm() -> None:
    """未接入不是错误、也不是警告 —— 所以它必须是中性石板，不能是暖黄或浅红。"""
    assert _chroma(skin.UNWIRED_BG) < 0.12
    hue = _hue(skin.UNWIRED_BG)
    assert 180 <= hue <= 260, f"未接入底色的色相 {hue:.0f}° 不在中性蓝灰区间"


def test_the_unwired_dashed_border_is_visible_but_not_heavy() -> None:
    """虚线边框是**纯装饰**：信息由标签与文字承载，边框只负责把这块划出来。"""
    assert contrast(skin.UNWIRED_BORDER, CARD) >= DECOR_MIN
    assert contrast(skin.UNWIRED_BORDER, CARD) < GRAPHIC_MIN, "再深就成了实线边框"


def test_the_unwired_border_is_darker_than_the_unwired_background() -> None:
    assert _luminance(skin.UNWIRED_BORDER) < _luminance(skin.UNWIRED_BG)


# ── 状态小标签 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fg", "bg"),
    [
        (skin.UNWIRED_TAG_FG, skin.UNWIRED_TAG_BG),
        (skin.OK_TAG_FG, skin.OK_TAG_BG),
        (skin.RO_TAG_FG, skin.RO_TAG_BG),
        (skin.WARN_TAG_FG, skin.WARN_TAG_BG),
    ],
)
def test_tag_text_clears_aa_on_its_own_background(fg: str, bg: str) -> None:
    ratio = contrast(fg, bg)
    assert ratio >= TEXT_MIN, f"{fg} 压 {bg} 只有 {ratio:.2f}:1"


@pytest.mark.parametrize(
    "name",
    ["UNWIRED_TAG_BG", "OK_TAG_BG", "RO_TAG_BG", "WARN_TAG_BG"],
)
def test_tag_backgrounds_are_lighter_than_the_text_on_them(name: str) -> None:
    assert _luminance(getattr(skin, name)) > _luminance(skin.INK_2)


def test_the_ok_tag_is_green_and_the_unwired_tag_is_not() -> None:
    """「已接通」与「未接入」是相反的含义，不能只靠一个字的差别区分。

    判据不用对比度（两个浅底亮度相近，比不出东西），改用**色相 + 彩度**：
    绿标签的色相落在 90~170° 且明显有色，未接入标签则接近中性。
    """
    assert 90 <= _hue(skin.OK_TAG_BG) <= 170
    assert _chroma(skin.OK_TAG_BG) > _chroma(skin.UNWIRED_TAG_BG)


def test_the_warn_tag_is_warm() -> None:
    """受规则约束的提示是暖黄 —— 与绿（已接通）、蓝灰（只读）在色相上分得开。"""
    hue = _hue(skin.WARN_TAG_BG)
    assert 25 <= hue <= 70, hue


def test_the_four_tag_backgrounds_are_mutually_distinguishable() -> None:
    """四个标签底两两之间必须要么色相差 ≥ 20°、要么彩度差 ≥ 0.1。

    它们**永远出现在同一张卡上**（管理台里一条挨一条），所以混同是真实风险；
    而亮度这一维在这里用不上（四个浅底的亮度差很小）。
    """
    tags = {
        "unwired": skin.UNWIRED_TAG_BG,
        "ok": skin.OK_TAG_BG,
        "readonly": skin.RO_TAG_BG,
        "warn": skin.WARN_TAG_BG,
    }
    names = list(tags)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            a, b = tags[first], tags[second]
            hue_gap = abs(_hue(a) - _hue(b))
            hue_gap = min(hue_gap, 360 - hue_gap)
            chroma_gap = abs(_chroma(a) - _chroma(b))
            assert hue_gap >= 20 or chroma_gap >= 0.1, (
                f"{first} 与 {second} 分不开：色相差 {hue_gap:.0f}°、彩度差 {chroma_gap:.2f}"
            )


# ── 状态色压在 v8 的新表面上 ────────────────────────────────────────────


@pytest.mark.parametrize("surface", [CARD, CANVAS, PANEL])
@pytest.mark.parametrize("label", list(STATE_COLORS))
def test_state_ink_still_clears_aa_on_the_v8_surfaces(label: EmotionLabel, surface: str) -> None:
    """状态色由 ``test_colors.py`` 守着，但「压在哪个底上」是皮肤的职责。

    换了画布色却没回头验状态色，是很容易漏的一步 —— 这条把它焊住。
    """
    ratio = contrast(STATE_COLORS[label], surface)
    assert ratio >= TEXT_MIN, f"{label.value} 压 {surface} 只有 {ratio:.2f}:1"


@pytest.mark.parametrize("label", list(STATE_FILLS))
def test_state_fill_is_perceivable_on_a_white_card(label: EmotionLabel) -> None:
    """色标签的填充：要么自己过 3:1，要么靠 ink 描边把边界顶到 3:1。"""
    fill = STATE_FILLS[label]
    assert max(contrast(fill, CARD), contrast(STATE_COLORS[label], CARD)) >= GRAPHIC_MIN


@pytest.mark.parametrize("label", list(STATE_FILLS))
def test_state_fill_carries_a_readable_foreground(label: EmotionLabel) -> None:
    assert contrast(on_fill(STATE_FILLS[label]), STATE_FILLS[label]) >= TEXT_MIN


def test_the_offline_and_closed_colours_survive_the_v8_surfaces() -> None:
    """``CLOSED_COLOR`` 是文字色（按 4.5 验），``DIM_COLOR`` 只画条（按 3:1 验）。"""
    assert contrast(CLOSED_COLOR, CARD) >= TEXT_MIN
    assert contrast(CLOSED_COLOR, CANVAS) >= TEXT_MIN
    assert contrast(DIM_COLOR, CANVAS) >= GRAPHIC_MIN


# ── 几何令牌 ────────────────────────────────────────────────────────────


def test_spacings_are_positive_and_monotonic() -> None:
    values = [skin.SPACING_XS, skin.SPACING_SM, skin.SPACING_MD, skin.SPACING_LG, skin.SPACING_XL]
    assert values == sorted(values)
    assert all(value > 0 for value in values)


def test_radii_are_positive_and_monotonic() -> None:
    values = [
        skin.RADIUS_XS,
        skin.RADIUS_SM,
        skin.RADIUS_MD,
        skin.RADIUS_LG,
        skin.RADIUS_XL,
        skin.RADIUS_FULL,
    ]
    assert values == sorted(values)
    assert skin.RADIUS_FULL >= 100, "FULL 要大到任何短边都会被夹成胶囊/正圆"
