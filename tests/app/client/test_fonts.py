"""字体族的**机器化守卫**：钉住「字体族里不能出现逗号」这条踩过坑的规则。

为什么要有这个模块
------------------
2026-10-01 实测发现一个真 bug：``theme.FONT_FAMILY`` 曾被写成
``"HarmonyOS Sans SC, Noto Sans SC, Microsoft YaHei UI"``，注释还断言
「Tk 的 family 支持逗号分隔的候选列表，会取第一个可用的」。

**那个断言是错的。** Tk 把整串当作**一个**族名去匹配，匹配不到就静默退回默认字体。
实测（``tkinter.font.Font.actual("family")``）::

    请求 'HarmonyOS Sans SC, Noto Sans SC, Microsoft YaHei UI' -> 实际 '宋体'
    请求 'HarmonyOS Sans SC'                                   -> 实际 'HarmonyOS Sans SC'

也就是说，界面上每一个字都掉进了宋体 —— 宋体在小字号下笔画发虚，这正是
「字体显得模糊」的直接原因之一。

这种 bug 的危险之处在于**它不会让任何既有测试变红**：逗号串在语法上完全合法，
``font.Font(family=...)`` 也不会抛错。所以必须专门钉一条测试：只要 ``FONT_FAMILY``
或 ``FONT_FAMILY_MONO`` 里出现逗号，这里立刻红。

本模块**不需要 Tk root**（``theme`` 是纯数据模块），CI 里跑得到。
"""

from __future__ import annotations

import pytest

from app.client import theme

__all__ = []

#: 所有「族名 + 字号 (+ 样式)」形式的字体元组常量名。新增字体常量时同步加进来，
#: 下面的参数化测试会把它们逐个过一遍。
FONT_TUPLE_NAMES = (
    "FONT_TITLE_LG",
    "FONT_TITLE",
    "FONT_LABEL",
    "FONT_BOLD",
    "FONT_BODY",
    "FONT_SMALL",
    "FONT_CAPTION",
    "FONT_MICRO",
)


def _font_tuples() -> list[tuple[str, tuple[object, ...]]]:
    return [(name, getattr(theme, name)) for name in FONT_TUPLE_NAMES]


@pytest.mark.parametrize("name", ["FONT_FAMILY", "FONT_FAMILY_MONO"])
def test_family_has_no_comma(name: str) -> None:
    """族名里不得有逗号 —— 否则 Tk 会整串匹配失败、静默掉进宋体。"""
    value = getattr(theme, name)
    assert isinstance(value, str), f"{name} 应当是字符串"
    assert "," not in value, (
        f"{name}={value!r} 含逗号。Tk 不支持逗号分隔的回退链 —— 整串会被当作一个"
        "族名，匹配失败后静默退回默认字体（实测为宋体）。请改成单一名族。"
    )


@pytest.mark.parametrize("name", ["FONT_FAMILY", "FONT_FAMILY_MONO"])
def test_family_is_not_empty_or_whitespace(name: str) -> None:
    value = getattr(theme, name)
    assert value.strip() == value and value.strip(), f"{name} 不能为空或带首尾空白"


def test_candidates_is_tuple_of_clean_names() -> None:
    """候选表是给人工参考的，但每个候选本身也必须是干净的**单一名族**。"""
    assert isinstance(theme.FONT_CANDIDATES, tuple)
    assert theme.FONT_CANDIDATES, "候选表不应为空"
    for candidate in theme.FONT_CANDIDATES:
        assert isinstance(candidate, str) and candidate.strip()
        assert "," not in candidate, f"候选 {candidate!r} 含逗号 —— 候选必须是单一名族"


def test_active_families_are_within_candidates_or_platform_native() -> None:
    """当前生效的两个族名必须是「候选表里的」或「平台自带必然存在的」。

    ``Microsoft YaHei UI`` / ``PingFang SC`` 同时在候选表里；Windows 上的
    ``Consolas`` 不在候选表（它是等宽族、单独一列）—— 所以这里只断言
    **正文字族**落在候选表内，等宽族只要求非空且无逗号。
    """
    assert theme.FONT_FAMILY in theme.FONT_CANDIDATES, (
        f"正文字族 {theme.FONT_FAMILY!r} 不在候选表内 —— 若有意更换，请同步更新"
        " FONT_CANDIDATES，让「偏好顺序」与「实际生效」保持一致。"
    )


@pytest.mark.parametrize("name,font", _font_tuples())
def test_font_tuple_shape(name: str, font: tuple[object, ...]) -> None:
    """每个字体常量都是 ``(family, size[, style])`` 且 family 是**无逗号**的字符串。"""
    assert isinstance(font, tuple), f"{name} 应当是元组"
    assert len(font) >= 2, f"{name} 至少要有 family 与 size"
    family, size = font[0], font[1]
    assert isinstance(family, str) and family.strip(), f"{name}[0] 应当是族名字符串"
    assert "," not in family, f"{name} 的族名 {family!r} 含逗号（Tk 回退链无效）"
    assert isinstance(size, int) and size > 0, f"{name} 的字号应当是正整数"


@pytest.mark.parametrize("name,font", _font_tuples())
def test_font_tuple_uses_a_declared_family(name: str, font: tuple[object, ...]) -> None:
    """字体元组的族名必须来自本模块声明的两个族之一，防止手写散落的族名。"""
    assert font[0] in (theme.FONT_FAMILY, theme.FONT_FAMILY_MONO), (
        f"{name} 用了未声明的族名 {font[0]!r}；请改用 FONT_FAMILY / FONT_FAMILY_MONO"
    )


def test_mono_family_is_used_by_micro_numeric_style() -> None:
    """``FONT_MICRO`` 是给「会抖动的数字列」用的，必须是等宽族。

    汇总面板的人数/百分比每次轮询都会重画，族名一变宽变窄整列就会左右抖。
    """
    assert theme.FONT_MICRO[0] == theme.FONT_FAMILY_MONO
