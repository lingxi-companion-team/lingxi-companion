"""桌面客户端的外观常量（纯数据，不调用任何 Tkinter API）。

为什么单独一个模块
------------------
``window.py`` 与 ``bubblewin.py`` 需要同一套色板与字号；放在其中任一个里都会造成循环导入。

与 :mod:`app.present.colors` 的分工
-----------------------------------
**状态色**（``focused`` / ``confused`` / ``distracted`` / ``unknown``）的权威来源是
:mod:`app.present.colors` —— 那里是纯逻辑、有测试。本模块只管**界面底色、边框、字号**
这类与业务无关的「皮肤」常量，不重复定义任何状态色。
"""

from __future__ import annotations

__all__ = [
    "ACCENT",
    "BG",
    "BORDER",
    "CARD_BG",
    "FONT_BOLD",
    "FONT_BODY",
    "FONT_FAMILY",
    "FONT_SMALL",
    "FONT_TITLE",
    "HEADER_BG",
    "HIDDEN_BG",
    "HIDDEN_FG",
    "MINIMIZED_SIZE",
    "MINIMIZED_MARGIN",
    "PANEL_BG",
    "TEXT",
    "TEXT_MUTED",
    "TRANSPARENT_KEY",
]

# ── 色板（与设计稿的 CSS 变量对齐，取其亮色版本：它是覆盖在课堂画面上的卡片）──
BG = "#f7f8fa"
CARD_BG = "#ffffff"
HEADER_BG = "#f1f3f7"
PANEL_BG = "#fbfcfe"
BORDER = "#e2e6ee"
TEXT = "#1c2333"
TEXT_MUTED = "#7a8699"
ACCENT = "#3b6fd4"

#: 「已隐藏」格子的中性样式（设计稿 §10.1 d2：仍占格、但不显示状态色）。
#:
#: 刻意不用红色/警示色 —— R6 明确要求「不要做成醒目提示」，否则一直隐藏的同学反而更扎眼。
HIDDEN_BG = "#eef1f5"
HIDDEN_FG = "#7a8699"

# ── 最小化态几何 ──
#: 最小化后主窗口的边长（设计稿 §04.3.1：约 56×56）。
MINIMIZED_SIZE = 56
#: 最小化时离屏幕右下角的边距。
MINIMIZED_MARGIN = 36

#: 透明色键：Windows 的 ``-transparentcolor`` 会把该颜色的像素变为全透明，
#: 从而让主窗口看起来只剩一个圆。
#:
#: 取值刻意挑一个不可能出现在界面里的洋红：若用白/黑，正常像素会被一起抠掉。
TRANSPARENT_KEY = "#ff00fe"

# ── 字体 ──
#: 中文字体首选「微软雅黑 UI」；其它平台上 Tk 找不到该字体会自行回退，不会报错。
FONT_FAMILY = "Microsoft YaHei UI"
FONT_TITLE = (FONT_FAMILY, 11, "bold")
FONT_BOLD = (FONT_FAMILY, 9, "bold")
FONT_BODY = (FONT_FAMILY, 9)
FONT_SMALL = (FONT_FAMILY, 8)
