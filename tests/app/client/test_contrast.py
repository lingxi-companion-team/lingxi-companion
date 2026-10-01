"""配色可达性的**机器化守卫**：把「对比度达标」变成一条会自动红的测试。

为什么要有这个模块
------------------
2026-09-30 的 v7 重设计换掉了全部四个状态色，理由是旧值实测不达标
（``confused`` 只有 2.52:1，``unknown`` 2.50:1）。但「换一次色」本身不产生护栏 ——
下次有人觉得某个蓝更好看、随手改一个 hex，同样的问题会静默复发，而且**没有任何
门禁会拦住它**：颜色不对不会让任何既有测试变红。

所以这里复算 WCAG 2.1 相对亮度与对比度，对**每一个承载信息的色值 × 每一个它可能
落上去的底色**组合断言门槛。改色改到不达标，这里立刻红。

这条守卫在 2026-10-01 的 v7 三栏仪表盘里**真的拦下了东西**：设计稿（Finserv 蓝白风）
的六个关键令牌里有五个不达标 —— 状态色 ``#4361EE``/``#7C5CE0``/``#64748B`` 作为文字色
压画布分别只有 4.34/4.07/4.12，外壳 ``#F0F6FF`` 会让表面层次掉到 1.086。
最终**表面三档保持原值、状态色压暗 2~9%、品牌色换 Finserv 主色**，
详见 ``不推送/技术方案/v7-实施决策记录-2026-10-01.md``。

门槛取值的依据
--------------
- 正文/状态名文字：**4.5:1**（WCAG 1.4.3 AA 正文级）。
- 图形元素（占比条、状态条这类「靠形状与颜色传达信息」的东西）：**3:1**
  （WCAG 1.4.11 非文本对比）。
- ``INK_3`` 是**例外且刻意**：它只画分隔线，不承载信息，所以不断言 4.5 ——
  但仍然断言它别浅到看不见（≥1.2），否则等于没画。

本模块**不引入任何第三方依赖**（无 PIL、无 colour 库）：WCAG 的公式只有六行，
自带比装一个包更省，也符合项目「只声明 numpy + pytest」的依赖约束。
"""

from __future__ import annotations

import re

import pytest

from app.client import theme
from app.present.colors import CLOSED_COLOR, DIM_COLOR, STATE_COLORS
from common.perception_types import EmotionLabel

__all__ = []

#: 三个底色：卡片白、画布蓝、框架面（顶栏/面板）。
CARD = "#ffffff"
CANVAS = theme.BG
PANEL = theme.PANEL_BG

#: 承载信息色要过的门槛（文字级）。
TEXT_MIN = 4.5
#: 图形元素要过的门槛（非文本对比）。
GRAPHIC_MIN = 3.0

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _channel(value: int) -> float:
    """把 0-255 的通道值转成线性光强（WCAG 2.1 的定义）。"""
    srgb = value / 255.0
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    """相对亮度。输入形如 ``#0052d9``。"""
    assert _HEX_RE.match(color), f"{color!r} 不是 #rrggbb"
    raw = color.lstrip("#")
    red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    """两色的对比度（1.0 ~ 21.0）。"""
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# ── 公式自身的哨兵用例：确认实现的不是「一个总是返回大数的函数」──────────────


def test_formula_matches_known_wcag_reference_values() -> None:
    """拿 WCAG 规范里的已知值校准公式，避免测试本身写错还一路绿灯。

    - 黑白 21:1（理论极值）
    - 同色 1:1
    - ``#767676`` 压白 ≈ 4.54（业界常引的「刚好过 AA 的灰」）
    """
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.001)
    assert contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.02)


# ── 状态色：四个 × 三个底色，全部要过 4.5 ────────────────────────────────


@pytest.mark.parametrize("label", list(EmotionLabel))
@pytest.mark.parametrize(
    ("background", "name"),
    [(CARD, "卡片白"), (CANVAS, "画布蓝"), (PANEL, "面板蓝")],
)
def test_state_color_is_readable_on_every_background(
    label: EmotionLabel, background: str, name: str
) -> None:
    """状态名文字会出现在卡片上（宫格），也可能落在画布/面板上（汇总与图例）。

    旧值在这里会红三条：``confused`` / ``distracted`` / ``unknown``。
    """
    color = STATE_COLORS[label]
    ratio = contrast(color, background)
    assert ratio >= TEXT_MIN, f"{label.value} {color} 压{name} 只有 {ratio:.2f}:1"


def test_regression_the_old_palette_would_have_failed() -> None:
    """把「旧值确实不达标」钉成事实，防止有人凭印象把旧色改回来。

    这条测试的价值不在于现在过，而在于**它记录了为什么要换**：
    如果哪天有人 revert 配色，``test_state_color_is_readable_on_every_background``
    会红，而这条会告诉他旧值本来就不行。
    """
    old_colors = {
        "focused": "#3b82c4",
        "confused": "#d09a2c",
        "distracted": "#c0584f",
        "unknown": "#9aa5b1",
    }
    for name, old in old_colors.items():
        assert contrast(old, CARD) < TEXT_MIN, f"旧值 {name} 竟然达标了，注释与结论需复核"


def test_dim_color_is_a_visible_graphic_but_lighter_than_unknown() -> None:
    """``DIM_COLOR`` 两头受夹：要看得见（≥3），又要比 ``UNKNOWN`` 浅（不混同）。"""
    for background, name in ((CARD, "卡片白"), (CANVAS, "画布蓝"), (PANEL, "面板蓝")):
        ratio = contrast(DIM_COLOR, background)
        assert ratio >= GRAPHIC_MIN, f"DIM_COLOR 压{name} 只有 {ratio:.2f}:1，条看不见"
    assert _luminance(DIM_COLOR) > _luminance(STATE_COLORS[EmotionLabel.UNKNOWN])
    # 「无人」与「未形成判定」得看得出是两种灰
    assert contrast(DIM_COLOR, STATE_COLORS[EmotionLabel.UNKNOWN]) >= 1.3


def test_closed_color_is_readable_text_and_darker_than_unknown() -> None:
    """``CLOSED_COLOR``（第 5 态「已关闭感知」）承载**文字**，按 4.5 验三个底色。

    它与两个灰的分工不同，这里一并钉住，避免三者被随手改成同一个值：

    - ``UNKNOWN`` ``#5b6a7f``：系统本帧未形成判定 —— **情感标签**，在 ``STATE_COLORS`` 里；
    - ``CLOSED_COLOR`` ``#55606e``：用户主动关掉采集 —— **不是情感**，故单列常量；
    - ``DIM_COLOR`` ``#7d8b9a``：「该状态当前无人」的**图形**置灰（按 3:1 验）。

    关闭是「确定的、非情感的状态」，所以它比 ``UNKNOWN`` 更深更实，而不是更淡。
    """
    for background, name in ((CARD, "卡片白"), (CANVAS, "画布蓝"), (PANEL, "面板蓝")):
        ratio = contrast(CLOSED_COLOR, background)
        assert ratio >= TEXT_MIN, f"closed {CLOSED_COLOR} 压{name} 只有 {ratio:.2f}:1"
    # 比「未形成判定」更深 —— 语义不同，取值也该分得开
    assert _luminance(CLOSED_COLOR) < _luminance(STATE_COLORS[EmotionLabel.UNKNOWN])
    assert contrast(CLOSED_COLOR, STATE_COLORS[EmotionLabel.UNKNOWN]) >= 1.15


# ── 文字令牌：按各自的实际用途断言 ────────────────────────────────────────


@pytest.mark.parametrize(
    "token_name",
    ["INK_HI", "INK", "INK_2", "MICRO"],
)
@pytest.mark.parametrize(
    ("background", "name"),
    [(CARD, "卡片白"), (CANVAS, "画布蓝"), (PANEL, "面板蓝")],
)
def test_informative_text_tokens_pass_aa(token_name: str, background: str, name: str) -> None:
    """这四个令牌**承载信息**，必须 ≥4.5:1，三个底色都要过。"""
    color = getattr(theme, token_name)
    ratio = contrast(color, background)
    assert ratio >= TEXT_MIN, f"{token_name} {color} 压{name} 只有 {ratio:.2f}:1"


def test_ink_3_is_deliberately_line_only_and_still_visible() -> None:
    """``INK_3`` 只画分隔线 —— 不要求 4.5，但不能浅到看不见。

    它是设计稿里被明确降级的一档：曾经用来写「无结果」的文字（2.6:1，读不出来），
    v7 把那些文字改回 ``INK_2``，``INK_3`` 只剩画线的用途。
    """
    ink_3 = theme.INK_3
    assert contrast(ink_3, CARD) < TEXT_MIN, "若它够 4.5 就该升格为文字色，注释需同步"
    for background, name in ((CARD, "卡片白"), (CANVAS, "画布蓝")):
        assert contrast(ink_3, background) >= 1.2, f"INK_3 压{name} 几乎看不见"


def test_brand_primary_carries_white_text() -> None:
    """主按钮是「品牌蓝底 + 白字」，所以白字压品牌蓝也要 ≥4.5。"""
    assert contrast("#ffffff", theme.BRAND) >= TEXT_MIN
    # 悬停/按下要比静止更显著（Vercel 指南：交互态需提升对比）
    assert contrast("#ffffff", theme.BRAND_HOVER) > contrast("#ffffff", theme.BRAND)
    assert contrast("#ffffff", theme.BRAND_ACTIVE) > contrast("#ffffff", theme.BRAND_HOVER)


def test_disabled_brand_does_not_pretend_to_carry_white_text() -> None:
    """禁用态的浅品牌蓝压白字远不够 —— 所以它**只能**配深色文字，这里钉住这个事实。"""
    assert contrast("#ffffff", theme.BRAND_DISABLED) < TEXT_MIN
    assert contrast(theme.INK, theme.BRAND_DISABLED) >= TEXT_MIN


# ── hairline 与特殊态 ────────────────────────────────────────────────────


@pytest.mark.parametrize("token_name", ["HAIRLINE", "HAIRLINE_STRONG"])
def test_hairlines_are_perceptible(token_name: str) -> None:
    """发丝线不承载信息，但不能淡到看不见；带蓝调是为了压在蓝底上不发脏。"""
    color = getattr(theme, token_name)
    assert contrast(color, CARD) >= 1.2
    assert contrast(color, CANVAS) >= 1.1


def test_shadow_is_a_soft_edge_not_a_black_slab() -> None:
    """偏移暗块阴影（决策 ⑥）两头受夹：要看得见，又不能变成硬边黑块。

    它是 Tk 无阴影 API 时的近似画法（``rounded.draw_card_shadow``）：
    下界保证「卡片下缘有一点厚度」看得出，上界防止它抢过卡片本身的层次
    —— 阴影一旦比「白卡 vs 画布」（1.156）还重，就会读成一块糊上去的灰。
    """
    ratio = contrast(theme.SHADOW, CANVAS)
    assert ratio >= 1.06, f"阴影压画布只有 {ratio:.3f}，等于没画"
    assert ratio <= 1.25, f"阴影压画布达 {ratio:.3f}，太重，会变成硬边黑块"


def test_surface_hierarchy_is_readable() -> None:
    """三档表面（白卡 / 蓝画布 / 浅框架面）两两都要看得出差别。

    这是被一次设计评审逼出来的：原本画布只比白卡浅 8.5%，28 张卡的边界全靠
    1px 发丝线撑着 —— 投影仪把那点亮度差压成 0 之后，宫格就糊成一片浅底，
    而那恰是「已避免糊成一片」的说法最该被检验的地方。现在白卡对画布 15.6%，
    即使投影仪折损也留有余量。

    面板**不能**压得太深：面板里要写状态名与百分比，底色一深那些文字就掉到
    4.5 以下（实测 ``#dfe9f9`` 会让 distracted 掉到 4.48）。所以它是「浅于画布、
    又不同于白卡」的中间值 —— 这条断言同时守住「看得出层次」与「文字仍达标」。
    """
    assert contrast(CARD, CANVAS) >= 1.12, "白卡与画布差得太少，28 张卡会糊成一片"
    assert contrast(CARD, PANEL) >= 1.05
    assert contrast(CANVAS, PANEL) >= 1.03, "画布与面板要能看出是两层"


def test_hidden_cell_text_is_readable_but_not_alarming() -> None:
    """已隐藏格：文字要读得清，但**不能**用警示红（R6 要求不做醒目提示）。"""
    ratio = contrast(theme.INK_2, theme.HIDDEN_BG)
    assert ratio >= TEXT_MIN, f"「已隐藏」文字压隐藏底只有 {ratio:.2f}:1"
    # 隐藏底是蓝灰，不是红/橙：红通道不该占主导
    raw = theme.HIDDEN_BG.lstrip("#")
    red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
    assert blue >= red and blue >= green


def test_offline_badge_uses_warm_amber_not_red() -> None:
    """断连提示是暖黄不是红：断线是常态，弹红警报会让教师以为系统坏了。"""
    ratio = contrast(theme.OFFLINE_FG, theme.OFFLINE_BG)
    assert ratio >= TEXT_MIN, f"离线文字只有 {ratio:.2f}:1"
    raw = theme.OFFLINE_FG.lstrip("#")
    red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
    assert red > blue and green > blue, "离线色应偏暖（红绿 > 蓝），不是警示红"


def test_online_dot_clears_the_graphic_threshold() -> None:
    """在线点是小圆点，图形元素，按 3:1 验。"""
    assert contrast(theme.ONLINE_DOT, CARD) >= GRAPHIC_MIN
    assert contrast(theme.ONLINE_DOT, CANVAS) >= GRAPHIC_MIN
