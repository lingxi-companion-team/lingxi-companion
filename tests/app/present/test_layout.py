"""``app.present.layout`` 的测试：三栏分档、收缩顺序与边界。"""

from __future__ import annotations

import pytest

from app.present.layout import (
    INSPECTOR_FULL,
    INSPECTOR_HIDDEN,
    INSPECTOR_NARROW,
    NAV_FULL,
    NAV_HIDDEN,
    NAV_ICONS,
    layout_for_width,
)


def test_wide_screen_keeps_both_side_columns() -> None:
    layout = layout_for_width(1440)
    assert layout.nav_mode == NAV_FULL
    assert layout.inspector_mode == INSPECTOR_FULL
    assert layout.nav_visible and layout.inspector_visible
    assert layout.main_width == 1440 - layout.nav_width - layout.inspector_width


def test_thresholds_are_inclusive() -> None:
    """恰好等于阈值归**更宽**那一档（``>=`` 语义）。"""
    assert layout_for_width(1280).inspector_mode == INSPECTOR_FULL
    assert layout_for_width(1279).inspector_mode == INSPECTOR_NARROW
    assert layout_for_width(1120).nav_mode == NAV_FULL
    assert layout_for_width(1119).nav_mode == NAV_ICONS
    assert layout_for_width(960).inspector_mode == INSPECTOR_NARROW
    assert layout_for_width(959).inspector_mode == INSPECTOR_HIDDEN
    assert layout_for_width(800).nav_mode == NAV_ICONS
    assert layout_for_width(799).nav_mode == NAV_HIDDEN


def test_inspector_is_shed_before_nav() -> None:
    """收缩顺序：右面板先收，左导航后收（设计稿 §七 决策）。"""
    at_1120 = layout_for_width(1120)
    at_960 = layout_for_width(960)
    # 1120 档：右面板先变窄，左导航仍是完整
    assert at_1120.inspector_mode == INSPECTOR_NARROW
    assert at_1120.nav_mode == NAV_FULL
    # 960 档：右面板仍窄、左导航才降为图标条
    assert at_960.inspector_mode == INSPECTOR_NARROW
    assert at_960.nav_mode == NAV_ICONS


def test_narrow_screen_hides_both_and_gives_width_to_main() -> None:
    layout = layout_for_width(600)
    assert layout.nav_mode == NAV_HIDDEN
    assert layout.inspector_mode == INSPECTOR_HIDDEN
    assert not layout.nav_visible
    assert not layout.inspector_visible
    assert layout.main_width == 600


def test_main_width_never_negative() -> None:
    """极窄窗口下中栏宽度夹到 0 以上，不出现负数。"""
    assert layout_for_width(1).main_width == 1
    assert layout_for_width(0).main_width == 0


@pytest.mark.parametrize("bad", [None, "wide", object()])
def test_invalid_input_falls_back_to_most_conservative(bad: object) -> None:
    """非法输入不抛错，按最保守档（左右全隐藏，宽度全给中栏）。"""
    layout = layout_for_width(bad)  # type: ignore[arg-type]
    assert layout.nav_mode == NAV_HIDDEN
    assert layout.inspector_mode == INSPECTOR_HIDDEN


def test_negative_width_is_treated_as_zero() -> None:
    layout = layout_for_width(-100)
    assert layout.main_width == 0
    assert layout.nav_mode == NAV_HIDDEN


def test_columns_always_fill_the_window() -> None:
    """三栏宽度之和恒等于窗口宽度（``main`` 是剩余量，不出现空隙或溢出）。"""
    for width in (1600, 1280, 1120, 1024, 960, 800, 640):
        layout = layout_for_width(width)
        assert layout.nav_width + layout.inspector_width + layout.main_width == width


def test_main_width_grows_within_a_band() -> None:
    """**同一档内**中栏随窗口变宽而变宽（跨档时收掉的面板会把宽度还给中栏，故不跨档比）。"""
    wide = [layout_for_width(w).main_width for w in (1280, 1400, 1600)]
    assert wide == sorted(wide)
    tight = [layout_for_width(w).main_width for w in (800, 880, 950)]
    assert tight == sorted(tight)


def test_shedding_a_panel_gives_width_back_to_main() -> None:
    """右面板一收，中栏立刻变宽 —— 这正是「把宽度让给主体」的意图。"""
    with_inspector = layout_for_width(960).main_width
    without_inspector = layout_for_width(959).main_width
    assert without_inspector > with_inspector
