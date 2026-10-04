"""右配置面板：**筛选真生效**（决策 ④）+ 装饰性的 Tab。

范围边界（实施方案 §八 第 4 项）
--------------------------------
- **筛选真生效**：状态单选 + 编号搜索框 → 每次变化回调 ``on_change(label_filter, query)``，
  由上层重新调用 ``present.roster_rows`` 并重画表格/宫格。
- **其余为装饰**：顶部「查询 / 图表 / 标注」三个 Tab 只画出「查询」是当前页，
  点击不切页（那是后续项）。

为什么这里用**原生控件**而不是 Canvas 自绘
------------------------------------------
面板里的交互件（单选、输入框）要真的能用：光标、键盘输入、焦点、无障碍。Canvas 自绘
这些等于重造一个输入控件，成本高且易错。所以只有 Tab 条是 Canvas（它纯装饰），
其余交给 Tk 原生控件 —— 这也是方案 §2 对「少量原生控件」的界定。

面板为什么是 Canvas 而不是 Frame（2026-10-01）
----------------------------------------------
Tk 的 ``Frame`` 画不出圆角，于是这块右面板此前是**方角**的 —— 而左导航、中栏两张卡、
学生端汇总面板全是圆角，唯独它是直角，一眼就看得出「漏了」。

做法：本控件改成 ``tk.Canvas``，在 ``<Configure>`` 时画一块 ``RADIUS_LG`` 的圆角底，
再把一个**内缩 8px** 的 ``PANEL_BG`` 子 Frame（``self._body``）用 ``create_window``
嵌进去承载原生控件。内缩量 8 ≥ ``RADIUS_LG·(1−1/√2) ≈ 5.86``，所以子 Frame 的直角
落在圆角弧**之内**，不会戳出去；而两者同为 ``PANEL_BG``，接缝不可见 ——
最终看到的就只是一块圆角面板。

判定（筛哪些行、非法键怎么办）**不在**本模块 —— 在 ``present.roster``，有测试。
这里只负责「收集用户选了哪个键、输入了什么词」并回调出去。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from app.client import rounded
from app.client.theme import (
    BG,
    BRAND,
    BRAND_SUBTLE,
    CARD_BG,
    FONT_CAPTION,
    FONT_LABEL,
    FONT_SMALL,
    HAIRLINE,
    INK,
    INK_2,
    INK_3,
    MICRO,
    PANEL_BG,
    RADIUS_LG,
    SPACING_LG,
    SPACING_MD,
    SPACING_SM,
    SPACING_XL,
)
from app.present.summary import CLOSED_KEY, CLOSED_TEXT, LABEL_ORDER, LABEL_TEXT

__all__ = ["ALL_FILTER", "InspectorPanel"]

#: 「不筛」的哨兵键（与 ``present.roster.roster_rows`` 的约定一致）。
ALL_FILTER = "all"

#: 顶部 Tab（只有第一个是真页面，其余装饰）。
_TABS = ("查询", "图表", "标注")

#: 内层承载区相对圆角面板的内缩（像素）。必须 ≥ ``RADIUS_LG·(1−1/√2)``，
#: 否则内层 Frame 的直角会戳出圆角弧（见模块 docstring）。
_PANEL_PAD = 8


class InspectorPanel(tk.Canvas):
    """右面板。构造后即是一块可用的筛选器，且自带圆角面板底。

    Args:
        master: 父容器。
        on_change: 筛选变化时的回调 ``(label_filter, query)``。
            ``label_filter`` 为 :data:`ALL_FILTER` 表示不筛。
        width: 面板宽度（像素）。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_change: Callable[[str, str], None],
        width: int = 320,
    ) -> None:
        # 画布底色 = 画布色：圆角之外的那点区域要露出画布，圆角才看得出来。
        super().__init__(master, bg=BG, width=width, highlightthickness=0, bd=0)
        self._on_change = on_change
        # 内层承载区：原生控件都 pack 到它上面（而不是画布本身）。
        self._body = tk.Frame(self, bg=PANEL_BG)
        self._body.pack_propagate(False)
        self._body_window = self.create_window(
            _PANEL_PAD, _PANEL_PAD, window=self._body, anchor="nw"
        )
        self._panel_size: tuple[int, int] = (0, 0)
        self.bind("<Configure>", self._on_configure)

        self._label_var = tk.StringVar(master=self, value=ALL_FILTER)
        self._query_var = tk.StringVar(master=self)
        self._query_var.trace_add("write", lambda *_: self._emit())

        self._build_tabs()
        self._build_filter()
        self._build_search()
        self._build_reset()

    # ── 圆角面板底 ────────────────────────────────────────────────────────

    def _on_configure(self, event: tk.Event) -> None:
        """按当前尺寸重画圆角底并同步内层承载区大小。"""
        if event.widget is not self:
            return
        width, height = int(event.width), int(event.height)
        if (width, height) == self._panel_size:
            return
        self._panel_size = (width, height)
        self.delete("panel")
        rounded.draw_rounded_rect(self, 0, 0, width, height, RADIUS_LG, fill=PANEL_BG, tags="panel")
        # 圆角底必须在窗口项**之下**，否则会盖住原生控件。
        self.tag_lower("panel")
        self.coords(self._body_window, _PANEL_PAD, _PANEL_PAD)
        self.itemconfigure(
            self._body_window,
            width=max(1, width - _PANEL_PAD * 2),
            height=max(1, height - _PANEL_PAD * 2),
        )

    # ── 对外 ──────────────────────────────────────────────────────────────

    def current_filter(self) -> tuple[str, str]:
        """当前的 ``(label_filter, query)``。"""
        return self._label_var.get(), self._query_var.get()

    def reset(self) -> None:
        """清空筛选（供「重置」按钮与外部调用）。"""
        self._label_var.set(ALL_FILTER)
        self._query_var.set("")

    def _emit(self) -> None:
        self._on_change(self._label_var.get(), self._query_var.get())

    # ── 搭界面 ────────────────────────────────────────────────────────────

    def _build_tabs(self) -> None:
        """顶部 Tab 条 —— 用 Canvas 画（纯装饰，点击不切页）。"""
        strip = tk.Canvas(self._body, height=40, bg=PANEL_BG, highlightthickness=0, bd=0)
        strip.pack(fill="x", padx=SPACING_XL, pady=(SPACING_XL, SPACING_MD))
        strip.bind("<Configure>", lambda event: self._draw_tabs(strip, event.width))
        self._tab_canvas = strip

    def _draw_tabs(self, canvas: tk.Canvas, width: int) -> None:
        canvas.delete("all")
        canvas.create_line(0, 39, width, 39, fill=HAIRLINE)
        cursor = 0.0
        for index, name in enumerate(_TABS):
            is_active = index == 0
            text_id = canvas.create_text(
                cursor + 2,
                18,
                text=name,
                anchor="w",
                fill=BRAND if is_active else INK_3,
                font=FONT_LABEL if is_active else FONT_SMALL,
            )
            bbox = canvas.bbox(text_id)
            text_w = (bbox[2] - bbox[0]) if bbox else 30
            if is_active:
                canvas.create_line(cursor + 2, 36, cursor + text_w + 2, 36, fill=BRAND, width=2)
            cursor += text_w + 22

    def _build_filter(self) -> None:
        """状态筛选：一组单选。键与 ``present`` 完全一致，不在这里硬编码字符串。"""
        tk.Label(
            self._body, text="按状态筛选", bg=PANEL_BG, fg=INK, font=FONT_LABEL, anchor="w"
        ).pack(fill="x", padx=SPACING_XL, pady=(SPACING_LG, SPACING_SM))

        options: list[tuple[str, str]] = [(ALL_FILTER, "全部")]
        options += [(key, LABEL_TEXT[key]) for key in LABEL_ORDER]
        options.append((CLOSED_KEY, CLOSED_TEXT))

        for key, text in options:
            tk.Radiobutton(
                self._body,
                text=text,
                value=key,
                variable=self._label_var,
                command=self._emit,
                bg=PANEL_BG,
                fg=INK_2,
                activebackground=PANEL_BG,
                activeforeground=BRAND,
                selectcolor=CARD_BG,
                highlightthickness=0,
                bd=0,
                anchor="w",
                font=FONT_SMALL,
                cursor="hand2",
            ).pack(fill="x", padx=SPACING_XL - 4)

    def _build_search(self) -> None:
        tk.Label(
            self._body, text="按编号搜索", bg=PANEL_BG, fg=INK, font=FONT_LABEL, anchor="w"
        ).pack(fill="x", padx=SPACING_XL, pady=(SPACING_LG, SPACING_SM))
        entry = tk.Entry(
            self._body,
            textvariable=self._query_var,
            bg=CARD_BG,
            fg=INK,
            insertbackground=BRAND,
            relief="flat",
            highlightthickness=1,
            highlightbackground=HAIRLINE,
            highlightcolor=BRAND,
            font=FONT_SMALL,
        )
        entry.pack(fill="x", padx=SPACING_XL, ipady=5)

    def _build_reset(self) -> None:
        tk.Button(
            self._body,
            text="重置筛选",
            command=self.reset,
            relief="flat",
            bg=BRAND_SUBTLE,
            fg=INK,
            activebackground=BRAND,
            activeforeground=CARD_BG,
            highlightthickness=0,
            bd=0,
            font=FONT_CAPTION,
            cursor="hand2",
        ).pack(fill="x", padx=SPACING_XL, pady=(SPACING_LG, SPACING_MD))

        tk.Label(
            self._body,
            text="（Tab 与指标配置为占位）",
            bg=PANEL_BG,
            fg=MICRO,
            font=FONT_CAPTION,
            anchor="w",
        ).pack(fill="x", padx=SPACING_XL, pady=(SPACING_MD, 0))
