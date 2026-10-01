"""三栏仪表盘视图：把侧栏 / KPI / 图表 / 表格 / 右面板组装成一块可复用视图。

职责边界
--------
本模块**只做布局与绘制编排**，不产生任何业务判定：

- 「谁占格、什么颜色、汇总怎么算、筛哪些行、KPI 有哪几张、三栏怎么收」
  全部在 ``app/present/``（有测试、CI 跑得到）；
- 这里做的是「把上一步拿到的数据摆到坐标上」，以及**画法层面的命中测试**
  （鼠标在第几行、点了哪个导航项）。

因此它整体位于 ``app/client/``（覆盖率 omit），与 ``window.py`` 同一取舍。

三栏布局
--------
::

    ┌────────┬──────────────────────────────┬──────────┐
    │ 侧栏    │  KPI 卡片行                   │  右面板   │
    │ 208px  │  ┌──────────────────────────┐ │  320px   │
    │        │  │ 分布（环形 + 堆叠 + 趋势） │ │  筛选     │
    │        │  ├──────────────────────────┤ │          │
    │        │  │ 成员明细表                │ │          │
    │        │  └──────────────────────────┘ │          │
    └────────┴──────────────────────────────┴──────────┘

宽度分配与「窄屏先收右面板、再收左导航」由 :func:`app.present.layout.layout_for_width`
决定 —— 本模块只是照着 ``Layout`` 摆。
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Mapping, Sequence
from typing import Any

from app.client import charts, rounded
from app.client import kpi as kpi_draw
from app.client import sidebar as sidebar_draw
from app.client import table as table_draw
from app.client.inspector import InspectorPanel
from app.client.theme import (
    BG,
    BRAND,
    CARD_BG,
    FONT_CAPTION,
    FONT_LABEL,
    FONT_SMALL,
    FONT_TITLE,
    HAIRLINE,
    INK,
    INK_2,
    MICRO,
    RADIUS_LG,
    SHADOW,
    SPACING_XL,
)
from app.present.layout import NAV_ICONS, Layout, layout_for_width
from app.present.roster import roster_rows
from app.present.summary import LABEL_ORDER
from app.present.trend import TrendBuffer

__all__ = ["DashboardView", "NAV_LABELS"]

#: 导航一级项（决策 ③：只有第 0 项是真页面，其余占位）。
NAV_LABELS: tuple[str, ...] = ("实时看板", "趋势分析", "名单明细", "设置")

#: 列间距。
_GUTTER = SPACING_XL
#: 画布内边距。
_PAD = SPACING_XL
#: 分布卡片高度。
_DIST_CARD_HEIGHT = 196


class DashboardView(tk.Frame):
    """三栏仪表盘。调用 :meth:`render` 传入 payload 即可重绘。

    Args:
        master: 父容器。
        viewer_id: 自己的编号（显示在标题区）。
    """

    def __init__(self, master: tk.Misc, *, viewer_id: str) -> None:
        super().__init__(master, bg=BG)
        self._viewer_id = viewer_id
        self._payload: Mapping[str, Any] = {}
        self._layout: Layout | None = None
        self._main_width = 0
        self._nav_index = 0
        self._hover_nav: int | None = None
        self._hover_row: int | None = None
        self._note = ""
        self._trend = TrendBuffer()

        self._sidebar_canvas = tk.Canvas(
            self, bg=BG, highlightthickness=0, bd=0
        )
        self._main_canvas = tk.Canvas(
            self, bg=BG, highlightthickness=0, bd=0
        )
        self._inspector = InspectorPanel(self, on_change=self._on_filter_change)
        self._inspector.pack_forget()

        self._sidebar_canvas.bind("<Button-1>", self._on_nav_click)
        self._sidebar_canvas.bind("<Motion>", self._on_nav_motion)
        self._sidebar_canvas.bind("<Leave>", self._on_nav_leave)
        self._main_canvas.bind("<Motion>", self._on_main_motion)
        self._main_canvas.bind("<Leave>", self._on_main_leave)
        # 三栏的档位阈值（1280/1120/960/800，见 present.layout）与窗口缩放的
        # 卡片档阈值（720/1120，见 present.scale）**不是同一套**，所以本视图必须
        # 按**自身宽度**重绘，不能只等窗口的跨档回调。
        #
        # 两道守卫照搬 ``window._on_resize`` 的教训（子控件 Configure 会经 bindtag
        # 送到祖先 → 在事件派发栈里重建控件 → Windows 原生崩溃 0xC0000005）：
        # ① 只认自己的 Configure；② 尺寸没真变就不重绘。
        self._last_size: tuple[int, int] = (0, 0)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        size = (int(event.width), int(event.height))
        if size == self._last_size:
            return
        self._last_size = size
        self.render()

    # ── 对外 ──────────────────────────────────────────────────────────────

    def update_payload(self, payload: Mapping[str, Any]) -> None:
        """接收一帧新数据并重绘。会顺带把汇总压入趋势缓冲。

        同一个 payload 对象重复调用**只重绘、不再压趋势** —— 窗口跨档重排时
        ``window._render_expanded`` 会带着同一份 payload 再调一次，若不挡，
        趋势缓冲会被同一次采样灌进去两遍，折线出现假的「台阶」。
        """
        if payload is self._payload and self._payload:
            self.render()
            return
        self._payload = payload
        summary = payload.get("summary")
        if isinstance(summary, Mapping):
            self._trend.push(_summary_from_payload(summary))
        self.render()

    def render(self) -> None:
        """按当前尺寸与数据重绘三栏。"""
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        layout = layout_for_width(width)
        self._layout = layout
        self._place_columns(layout, width, height)
        self._draw_sidebar(layout, height)
        self._draw_main(layout, height)
        self._draw_inspector(layout, height)

    # ── 布局 ──────────────────────────────────────────────────────────────

    def _place_columns(self, layout: Layout, width: int, height: int) -> None:
        """按 ``Layout`` 用 ``place`` 精确定位三栏（比 pack 可预测）。"""
        x = 0
        if layout.nav_visible:
            self._sidebar_canvas.place(x=x, y=0, width=layout.nav_width, height=height)
            x += layout.nav_width + _GUTTER
        else:
            self._sidebar_canvas.place_forget()
        reserved = _GUTTER + layout.inspector_width if layout.inspector_visible else 0
        self._main_width = max(240, width - x - reserved)
        self._main_canvas.place(x=x, y=0, width=self._main_width, height=height)
        if layout.inspector_visible:
            self._inspector.configure(width=layout.inspector_width)
            self._inspector.place(
                x=width - layout.inspector_width,
                y=0,
                width=layout.inspector_width,
                height=height,
            )
        else:
            self._inspector.place_forget()

    # ── 侧栏 ──────────────────────────────────────────────────────────────

    def _draw_sidebar(self, layout: Layout, height: int) -> None:
        if not layout.nav_visible:
            return
        canvas = self._sidebar_canvas
        canvas.delete("all")
        sidebar_draw.draw_sidebar(
            canvas,
            NAV_LABELS,
            0,
            0,
            layout.nav_width,
            height,
            active_index=self._nav_index,
            hover_index=self._hover_nav,
            mode=(
                sidebar_draw.NAV_MODE_ICONS
                if layout.nav_mode == NAV_ICONS
                else sidebar_draw.NAV_MODE_FULL
            ),
        )

    def _on_nav_click(self, event: tk.Event) -> None:
        layout = self._layout
        if layout is None or not layout.nav_visible:
            return
        index = sidebar_draw.nav_hit_index(NAV_LABELS, 0, self.winfo_height(), event.y)
        if index is None or index == self._nav_index:
            return
        # 决策 ③：其余项是占位 —— 不切页，给一句明确提示。
        self._note = f"「{NAV_LABELS[index]}」尚未实现（当前只有「实时看板」可用）"
        self._draw_main(layout, self.winfo_height())

    def _on_nav_motion(self, event: tk.Event) -> None:
        layout = self._layout
        if layout is None or not layout.nav_visible:
            return
        index = sidebar_draw.nav_hit_index(NAV_LABELS, 0, self.winfo_height(), event.y)
        if index != self._hover_nav:
            self._hover_nav = index
            self._draw_sidebar(layout, self.winfo_height())

    def _on_nav_leave(self, _event: tk.Event) -> None:
        if self._hover_nav is not None:
            self._hover_nav = None
            layout = self._layout
            if layout is not None:
                self._draw_sidebar(layout, self.winfo_height())

    # ── 中栏 ──────────────────────────────────────────────────────────────

    def _draw_main(self, layout: Layout, height: int) -> None:
        canvas = self._main_canvas
        canvas.delete("all")
        width = self._main_width

        summary = self._payload.get("summary")
        cards = kpi_draw_kpi_cards(summary)

        y = _PAD
        # 标题行
        canvas.create_text(
            _PAD, y + 8, text="实时看板", anchor="w", fill=INK, font=FONT_TITLE
        )
        online = int(summary.get("online_count", 0)) if isinstance(summary, Mapping) else 0
        canvas.create_text(
            _PAD + 80,
            y + 9,
            text=f"在线 {online} 人 · {self._viewer_id}",
            anchor="w",
            fill=MICRO,
            font=FONT_SMALL,
        )
        y += 30

        # KPI 卡片行
        kpi_draw.draw_kpi_row(canvas, cards, _PAD, y, width - _PAD * 2)
        y += kpi_draw.KPI_CARD_HEIGHT + _GUTTER

        # 分布卡片：环形 + 图例 + 趋势
        dist_h = _DIST_CARD_HEIGHT
        self._draw_dist_card(canvas, cards, _PAD, y, width - _PAD * 2, dist_h, summary)
        y += dist_h + _GUTTER

        # 表格
        table_h = max(120, height - y - _PAD)
        self._draw_table(canvas, _PAD, y, width - _PAD * 2, table_h)

        if self._note:
            canvas.create_text(
                width - _PAD,
                _PAD + 9,
                text=self._note,
                anchor="e",
                fill=BRAND,
                font=FONT_CAPTION,
            )

    def _draw_dist_card(
        self,
        canvas: tk.Canvas,
        cards: Sequence[Any],
        x0: float,
        y0: float,
        width: float,
        height: float,
        summary: Any,
    ) -> None:
        x1, y1 = x0 + width, y0 + height
        rounded.draw_card_shadow(canvas, x0, y0, x1, y1, RADIUS_LG, color=SHADOW)
        rounded.draw_rounded_rect(canvas, x0, y0, x1, y1, RADIUS_LG, fill=CARD_BG)
        canvas.create_text(
            x0 + _PAD, y0 + 22, text="状态分布", anchor="w", fill=INK, font=FONT_LABEL
        )

        donut_cx = x0 + 96
        donut_cy = y0 + height / 2 + 8
        segments = [(card.ratio, card.color) for card in cards]
        charts.draw_donut(
            canvas, donut_cx, donut_cy, 62, 20, segments, track=HAIRLINE
        )
        online = int(summary.get("online_count", 0)) if isinstance(summary, Mapping) else 0
        canvas.create_text(
            donut_cx, donut_cy - 6, text=str(online), fill=INK, font=FONT_TITLE
        )
        canvas.create_text(
            donut_cx, donut_cy + 14, text="在线", fill=MICRO, font=FONT_CAPTION
        )

        # 图例：分量名 + 人数
        legend_x = x0 + 200
        legend_y = y0 + 56
        for card in cards:
            canvas.create_oval(
                legend_x, legend_y - 4, legend_x + 8, legend_y + 4, fill=card.color, outline=""
            )
            canvas.create_text(
                legend_x + 14,
                legend_y,
                text=f"{card.label}  {card.count}",
                anchor="w",
                fill=INK_2,
                font=FONT_SMALL,
            )
            legend_y += 22

        # 趋势折线（占卡片右半的下半）
        trend_x0 = x0 + 360
        if width > 460:
            values = [
                sum(sample.by_label.values()) + sample.closed_count
                for sample in self._trend.samples()
            ]
            canvas.create_text(
                trend_x0, y0 + 22, text="在线趋势", anchor="w", fill=MICRO, font=FONT_CAPTION
            )
            charts.draw_sparkline(
                canvas,
                trend_x0,
                y0 + 44,
                x1 - _PAD,
                y1 - 24,
                values,
                color=BRAND,
                baseline=HAIRLINE,
            )

    def _draw_table(
        self, canvas: tk.Canvas, x0: float, y0: float, width: float, height: float
    ) -> None:
        rows = self._filtered_rows()
        table_draw.draw_table(
            canvas, rows, x0, y0, width, height, hover_index=self._hover_row
        )

    def _filtered_rows(self) -> list[Any]:
        """按右面板当前的筛选条件取行 —— 规则全在 ``present.roster``。"""
        grid = self._payload.get("grid") or []
        participants = _participants_from_payload(grid)
        label_filter, query = self._inspector.current_filter()
        return roster_rows(
            participants,
            now_ts=time.time(),
            label_filter=label_filter,
            query=query,
        )

    # ── 中栏交互 ──────────────────────────────────────────────────────────

    def _table_top(self, height: int) -> float:
        return _PAD + 30 + kpi_draw.KPI_CARD_HEIGHT + _GUTTER + _DIST_CARD_HEIGHT + _GUTTER

    def _on_main_motion(self, event: tk.Event) -> None:
        top = self._table_top(self.winfo_height()) + table_draw.HEADER_HEIGHT
        index = int((event.y - top) // table_draw.ROW_HEIGHT) if event.y >= top else None
        if index is not None and index < 0:
            index = None
        if index != self._hover_row:
            self._hover_row = index
            layout = self._layout
            if layout is not None:
                self._draw_main(layout, self.winfo_height())

    def _on_main_leave(self, _event: tk.Event) -> None:
        if self._hover_row is not None:
            self._hover_row = None
            layout = self._layout
            if layout is not None:
                self._draw_main(layout, self.winfo_height())

    # ── 右面板 ────────────────────────────────────────────────────────────

    def _draw_inspector(self, layout: Layout, height: int) -> None:
        if not layout.inspector_visible:
            return
        self._inspector.configure(width=layout.inspector_width, height=height)

    def _on_filter_change(self, _label_filter: str, _query: str) -> None:
        """筛选变化 → 重画中栏（表格立即反映）。"""
        layout = self._layout
        if layout is not None:
            self._draw_main(layout, self.winfo_height())


# ── payload → present 对象的小桥（纯转换，无判定）──────────────────────────


def kpi_draw_kpi_cards(summary: Any) -> list[Any]:
    """把 payload 里的 ``summary`` 字典摊成 KPI 卡片（规则在 ``present.kpi``）。"""
    from app.present.kpi import kpi_cards

    return kpi_cards(_summary_from_payload(summary if isinstance(summary, Mapping) else {}))


def _summary_from_payload(raw: Mapping[str, Any]) -> Any:
    """把线路格式的 ``summary`` 还原成 :class:`~app.present.summary.Summary`。

    ``Summary`` 的构造需要 ``by_label`` 等字段；缺字段时按空补 —— 展示层不为一条
    不完整数据崩掉（同 ``color_for_key`` 的兜底约定）。
    """
    from app.present.summary import Summary

    by_label = {key: int((raw.get("by_label") or {}).get(key, 0)) for key in LABEL_ORDER}
    ratio = {key: float((raw.get("ratio") or {}).get(key, 0.0)) for key in LABEL_ORDER}
    members = {key: list((raw.get("members_by_label") or {}).get(key, [])) for key in LABEL_ORDER}
    return Summary(
        by_label=by_label,
        ratio=ratio,
        online_count=int(raw.get("online_count", 0)),
        members_by_label=members,
        closed_count=int(raw.get("closed_count", 0)),
    )


def _participants_from_payload(grid: Sequence[Any]) -> list[Any]:
    """线路格式的 ``grid`` → :class:`~app.envelope.ParticipantState` 列表。

    坏行**跳过**而不是整帧失败：一条脏数据不该让整张表空掉。
    """
    from app.envelope import ParticipantState

    result: list[Any] = []
    for cell in grid:
        if not isinstance(cell, Mapping):
            continue
        try:
            result.append(ParticipantState.from_dict(cell))
        except (ValueError, TypeError, KeyError):
            continue
    return result
