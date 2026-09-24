"""桌面客户端主窗口（Tkinter 外壳）。

D3：**一个窗口，两种状态**
--------------------------
展开态与最小化态是**同一个** ``Toplevel``，切换时改几何尺寸与重绘（``overrideredirect``
随之开关），不做「两个窗口互相隐藏」。好处是状态天然一致 —— 置顶属性、拖动位置、
当前角色、连接会话都是同一份，不存在两个窗口不同步的问题（设计稿 §04.3.1）。

职责边界（这是它整体能被覆盖率 ``omit`` 的前提）
------------------------------------------------
本模块只做三件事：**读快照 → 调 ``app.present`` 的纯函数 → 用 Tkinter 画出来**。
任何判定规则（谁占格、什么颜色、汇总怎么算、谁能看见谁）都在 :mod:`app.present` 里，
且各有测试。把规则写到这里 = 既测不到（CI 跑在无显示环境）又违反设计稿 §3.2。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from typing import Any

from app.client.bubblewin import (
    BubbleWindow,
    ExitHotkey,
    build_context_menu,
    try_enable_transparency,
)
from app.client.theme import (
    ACCENT,
    BG,
    CARD_BG,
    FONT_BODY,
    FONT_BOLD,
    FONT_SMALL,
    FONT_TITLE,
    HEADER_BG,
    HIDDEN_BG,
    HIDDEN_FG,
    MINIMIZED_MARGIN,
    MINIMIZED_SIZE,
    TEXT,
    TEXT_MUTED,
    TRANSPARENT_KEY,
)
from app.envelope import ROLE_STUDENT, ROLE_TEACHER
from app.present import DIM_COLOR, LABEL_ORDER, LABEL_TEXT, color_for_key, columns_for

__all__ = ["CELL_HEIGHT", "CELL_WIDTH", "ClientWindow"]

#: 快照来源：返回 ``app.hub.build_payload`` 那一套 payload。
SnapshotProvider = Callable[[], Mapping[str, Any]]

#: 「对同学隐藏」开关的上报通道（写回服务端；可见性裁剪在服务端，客户端改不了）。
HiddenReporter = Callable[[str, bool], None]

#: 宫格单格的尺寸（含 3px 内边距后约 80×72）。
CELL_WIDTH = 74
CELL_HEIGHT = 64

#: 教师端汇总面板宽度。
PANEL_WIDTH = 236

#: 汇总条形图的最大宽度（像素）。
BAR_MAX_WIDTH = 108

#: 学生端的开关文案 —— 这是**硬要求**（R5）：不把真实可见范围写进文案，
#: 就构成「误导性的隐私承诺」（学生以为老师也看不到）。
HIDE_SWITCH_TEXT = "仅对同学隐藏（教师仍可见）"


class ClientWindow:
    """一个参与者的桌面窗口。

    Args:
        master: 根 ``Tk``（本类自己建 ``Toplevel`` —— D3 要求主界面是独立窗口）。
        provider: 取快照的回调。传 ``hub.snapshot_for`` 的偏函数或一个 HTTP 拉取函数。
        viewer_id: 自己的 ``participant_id``（取快照与上报开关都要带）。
        role: ``student`` / ``teacher``；决定要不要汇总面板与隐藏开关。
        poll_ms: 轮询间隔（毫秒）。最小 200，避免写错 ``0`` 把界面卡死。
        hidden_reporter: 隐藏开关的上报回调；``None`` 时开关只改本地布尔值。
    """

    def __init__(
        self,
        master: tk.Misc,
        provider: SnapshotProvider,
        *,
        viewer_id: str,
        role: str = ROLE_STUDENT,
        poll_ms: int = 1200,
        hidden_reporter: HiddenReporter | None = None,
    ) -> None:
        self._master = master
        self._provider = provider
        self._viewer_id = viewer_id
        self._role = role
        self._poll_ms = max(200, int(poll_ms))
        self._hidden_reporter = hidden_reporter

        # ── 唯一的运行时状态（卫星窗口不持有任何状态，见 bubblewin 的说明）──
        self._payload: Mapping[str, Any] = {}
        self._minimized = False
        self._transparent = False
        self._after_id: str | None = None
        self._menu: tk.Menu | None = None
        self._drag_offset = (0, 0)
        self._icon_pos: tuple[int, int] | None = None
        self._expanded_geometry: str | None = None
        self._autosize_pending = True

        self.win = tk.Toplevel(master)
        self.win.title("灵犀学伴 · 课堂共享宫格")
        self.win.configure(bg=BG)
        self.win.protocol("WM_DELETE_WINDOW", self.quit_app)

        self._status = tk.StringVar(master=master, value="连接中…")
        self._hidden_var = tk.BooleanVar(master=master, value=False)

        self._build_expanded()
        self._build_minimized_surface()

        self._bubble = BubbleWindow(self.win)
        self._hotkey = ExitHotkey(self.win, self.quit_app)
        # 用 ``bind_all``：右键要能在**任何**子控件上生效（Tk 的事件不冒泡到 Toplevel），
        # 而本应用只有一个窗口，全局绑定不会误伤别的界面。
        self.win.bind_all("<Button-3>", self._popup_menu)

        self._expanded_frame.pack(fill="both", expand=True)
        self.refresh()
        self._schedule_poll()

    # ── 搭界面 ────────────────────────────────────────────────────────────

    def _build_expanded(self) -> None:
        self._expanded_frame = tk.Frame(self.win, bg=BG)

        header = tk.Frame(self._expanded_frame, bg=HEADER_BG, padx=12, pady=8)
        header.pack(fill="x")
        tk.Label(
            header, text="灵犀学伴 · 课堂共享宫格", bg=HEADER_BG, fg=TEXT, font=FONT_TITLE
        ).pack(side="left")
        tk.Label(
            header, textvariable=self._status, bg=HEADER_BG, fg=TEXT_MUTED, font=FONT_SMALL
        ).pack(side="left", padx=10)
        tk.Button(header, text="最小化", command=self.toggle_minimized, width=8).pack(side="right")
        if self._role != ROLE_TEACHER:
            tk.Checkbutton(
                header,
                text=HIDE_SWITCH_TEXT,
                variable=self._hidden_var,
                command=self._on_hidden_toggled,
                bg=HEADER_BG,
                fg=TEXT,
                activebackground=HEADER_BG,
                selectcolor=CARD_BG,
                font=FONT_BODY,
            ).pack(side="right", padx=10)

        body = tk.Frame(self._expanded_frame, bg=BG, padx=12, pady=10)
        body.pack(fill="both", expand=True)
        self._grid_frame = tk.Frame(body, bg=BG)
        self._grid_frame.pack(side="left", anchor="n")
        self._panel_frame = tk.Frame(body, bg=CARD_BG, width=PANEL_WIDTH)

        # 图例：把三种容易误读的记号写在界面上，省得用户猜。
        tk.Label(
            self._expanded_frame,
            text=(
                "● 维持中（本帧未达确认票数，沿用上一稳定状态）"
                "　·　灰格 = 该同学已隐藏状态　·　白格 = 本帧无判定"
            ),
            bg=BG,
            fg=TEXT_MUTED,
            font=FONT_SMALL,
        ).pack(anchor="w", padx=14, pady=(0, 8))

    def _build_minimized_surface(self) -> None:
        self._icon = tk.Canvas(
            self.win,
            width=MINIMIZED_SIZE,
            height=MINIMIZED_SIZE,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self._icon.bind("<ButtonPress-1>", self._on_drag_start)
        self._icon.bind("<B1-Motion>", self._on_drag_move)

    # ── 取数与轮询 ────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """取一次快照并重绘。传输失败只改状态栏，不抛出去 —— 界面要能一直活着。"""
        try:
            payload = dict(self._provider())
        except Exception as exc:
            # 传输/服务端的任何异常都不该让窗口崩掉 —— 状态栏提示，下一轮再试。
            self._status.set(f"离线（{type(exc).__name__}）")
            return
        self._payload = payload
        self._sync_hidden_switch()
        self._status.set(f"{self._viewer_id} · {self._role_label()}")
        self._render()

    def _schedule_poll(self) -> None:
        self._after_id = self.win.after(self._poll_ms, self._poll)

    def _poll(self) -> None:
        self.refresh()
        self._schedule_poll()

    def _role_label(self) -> str:
        return "教师" if self._role == ROLE_TEACHER else "学生"

    def _sync_hidden_switch(self) -> None:
        """按服务端的权威值回填开关 —— 否则多端同时操作时，界面会与事实不一致。"""
        for cell in self._payload.get("grid") or []:
            if cell.get("participant_id") == self._viewer_id:
                self._hidden_var.set(bool(cell.get("hidden")))
                return

    def _on_hidden_toggled(self) -> None:
        hidden = bool(self._hidden_var.get())
        if self._hidden_reporter is not None:
            try:
                self._hidden_reporter(self._viewer_id, hidden)
            except Exception as exc:
                # 上报失败要保持可见：否则用户以为已经隐藏成功，实际仍是公开的。
                self._status.set(f"上报失败（{type(exc).__name__}）")
                return
        self.refresh()

    # ── 展开态 ⇄ 最小化态（D3）────────────────────────────────────────────

    def toggle_minimized(self) -> None:
        self.set_minimized(not self._minimized)

    def set_minimized(self, minimized: bool) -> None:
        """切换两种形态。**同一个窗口**，只是几何尺寸与装饰变了。"""
        if minimized == self._minimized:
            return
        self._minimized = minimized
        if minimized:
            self._expanded_geometry = self.win.winfo_geometry()
            self._expanded_frame.pack_forget()
            self._icon.pack()
            # ``overrideredirect(True)`` 去掉系统边框 → 窗口从任务栏消失，也没法常规拖动，
            # 所以拖动要自己实现、退出通道必须有（设计稿 §05.2 / §05.3）。
            self.win.overrideredirect(True)
            self._set_topmost(True)
            pos_x, pos_y = self._minimized_position()
            self.win.geometry(f"{MINIMIZED_SIZE}x{MINIMIZED_SIZE}+{pos_x}+{pos_y}")
        else:
            self.win.overrideredirect(False)
            self._set_topmost(False)
            self._icon.pack_forget()
            self._expanded_frame.pack(fill="both", expand=True)
            if self._expanded_geometry:
                self.win.geometry(self._expanded_geometry)
        self._apply_transparency(minimized)
        self._render()

    def _set_topmost(self, enabled: bool) -> None:
        try:
            self.win.attributes("-topmost", enabled)
        except tk.TclError:
            # 个别平台/Tk 版本不支持该属性；置顶失败不影响主流程。
            pass

    def _apply_transparency(self, enabled: bool) -> None:
        """最小化态尝试抠掉图标外圈的底色。

        ``-transparentcolor`` **仅 Windows 可用**（设计稿 §05.2 的坑 1），且失败可能是
        **静默**的，所以交给 :func:`try_enable_transparency` 回读确认；不成就退回实心方底 ——
        实心小圆本身就够「不挡画面」了，不必依赖真透明。
        """
        if not enabled:
            self._transparent = False
            try:
                self.win.attributes("-transparentcolor", "")
            except tk.TclError:
                pass
            return
        self.win.update_idletasks()
        self._transparent = try_enable_transparency(self.win)

    def _minimized_position(self) -> tuple[int, int]:
        """最小化后的落点：默认右下角，用户拖过就沿用拖动后的坐标（A5 不被重置）。"""
        if self._icon_pos is not None:
            return self._icon_pos
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        return (
            screen_w - MINIMIZED_SIZE - MINIMIZED_MARGIN,
            screen_h - MINIMIZED_SIZE - MINIMIZED_MARGIN * 4,
        )

    # ── 拖动（``overrideredirect`` 后必须自己实现）──────────────────────────

    def _on_drag_start(self, event: Any) -> None:
        self._drag_offset = (
            event.x_root - self.win.winfo_x(),
            event.y_root - self.win.winfo_y(),
        )

    def _on_drag_move(self, event: Any) -> None:
        pos_x = event.x_root - self._drag_offset[0]
        pos_y = event.y_root - self._drag_offset[1]
        self.win.geometry(f"+{pos_x}+{pos_y}")
        self._icon_pos = (pos_x, pos_y)
        self._bubble.reposition(pos_x, pos_y, MINIMIZED_SIZE)

    # ── 右键菜单与退出（R2）───────────────────────────────────────────────

    def _popup_menu(self, event: Any) -> None:
        is_teacher = self._role == ROLE_TEACHER
        self._menu = build_context_menu(
            self.win,
            minimized=self._minimized,
            hidden_var=None if is_teacher else self._hidden_var,
            on_toggle_minimize=self.toggle_minimized,
            on_toggle_hidden=self._on_hidden_toggled,
            on_exit=self.quit_app,
        )
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def quit_app(self) -> None:
        """退出：停轮询与热键 → 拆卫星窗口 → 销毁主窗口 → 结束 mainloop。"""
        if self._after_id is not None:
            try:
                self.win.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._hotkey.cancel()
        try:
            self._bubble.destroy()
        except tk.TclError:
            pass
        self.win.destroy()
        self._master.quit()

    # ── 绘制 ──────────────────────────────────────────────────────────────

    def _render(self) -> None:
        summary = self._payload.get("summary") or {}
        self._bubble.update_content(
            self._payload.get("bubble") or [],
            members_by_label=summary.get("members_by_label"),
            online_count=summary.get("online_count"),
            stale=self._own_stale(),
        )
        if self._minimized:
            self._render_icon()
            self._bubble.show()
            self._bubble.reposition(self.win.winfo_x(), self.win.winfo_y(), MINIMIZED_SIZE)
            return
        self._bubble.hide()
        self._render_expanded()

    def _own_stale(self) -> bool:
        """本人这一帧是不是「维持中」（沿用上一稳定状态）。"""
        components = self._payload.get("bubble") or []
        return bool(components[0].get("stale", False)) if components else False

    def _render_expanded(self) -> None:
        for child in self._grid_frame.winfo_children():
            child.destroy()
        cells = list(self._payload.get("grid") or [])
        columns = columns_for(len(cells))
        for index, cell in enumerate(cells):
            self._make_cell(cell).grid(row=index // columns, column=index % columns, padx=3, pady=3)

        if self._role == ROLE_TEACHER:
            self._render_summary()
        else:
            # A2：学生端连汇总**组件**都不建 —— 服务端也没下发 ``summary``。
            self._panel_frame.pack_forget()

        if self._autosize_pending:
            self._autosize_pending = False
            self.win.geometry("")

    def _make_cell(self, cell: Mapping[str, Any]) -> tk.Frame:
        """一格 = 一个参与者。三种样子：有状态 / 已隐藏 / 本帧无结果。"""
        state = cell.get("state")
        hidden = bool(cell.get("hidden"))
        participant_id = str(cell.get("participant_id", "?"))
        stale = False

        if isinstance(state, Mapping) and state.get("label"):
            key = str(state["label"])
            color = color_for_key(key)
            sub_text = LABEL_TEXT.get(key, key)
            stale = bool(state.get("stale"))
            fg = "#ffffff"
        elif hidden:
            # §10.1 d2：仍占格、但不显示状态色，中性文案「已隐藏」（不醒目 —— R6）。
            color, sub_text, fg = HIDDEN_BG, "已隐藏", HIDDEN_FG
        else:
            # 本帧还没形成判定（融合层拒判 / 平滑层票数不足）。底色不带灰调，
            # 与「已隐藏」区分开：一个是「没有可展示的东西」，一个是「有但不给你看」。
            color, sub_text, fg = BG, "无结果", TEXT_MUTED

        frame = tk.Frame(self._grid_frame, bg=color, width=CELL_WIDTH, height=CELL_HEIGHT)
        frame.pack_propagate(False)
        inner = tk.Frame(frame, bg=color)
        inner.pack(expand=True)
        tk.Label(inner, text=participant_id, bg=color, fg=fg, font=FONT_BOLD).pack()
        tk.Label(inner, text=sub_text, bg=color, fg=fg, font=FONT_SMALL).pack()
        if stale:
            # 「维持中」用小圆点角标，不换色 —— 契约层明确要求它**不是**新状态。
            tk.Label(frame, text="●", bg=color, fg="#ffffff", font=FONT_SMALL).place(
                x=CELL_WIDTH - 13, y=1
            )
        return frame

    def _render_summary(self) -> None:
        """教师端汇总面板：4 行状态分布 + 占比条（D1 分量 / D2 分母）。"""
        for child in self._panel_frame.winfo_children():
            child.destroy()
        summary = self._payload.get("summary") or {}
        by_label = summary.get("by_label") or {}
        ratio = summary.get("ratio") or {}
        online = int(summary.get("online_count", 0))

        tk.Label(self._panel_frame, text="课堂汇总", bg=CARD_BG, fg=TEXT, font=FONT_TITLE).pack(
            anchor="w", padx=10, pady=(8, 0)
        )
        tk.Label(
            self._panel_frame, text=f"在线 {online} 人", bg=CARD_BG, fg=TEXT_MUTED, font=FONT_SMALL
        ).pack(anchor="w", padx=10, pady=(2, 8))

        for key in LABEL_ORDER:
            count = int(by_label.get(key, 0))
            share = float(ratio.get(key, 0.0))
            color = color_for_key(key) if count else DIM_COLOR
            row = tk.Frame(self._panel_frame, bg=CARD_BG)
            row.pack(fill="x", padx=10, pady=2)
            tk.Label(
                row,
                text=LABEL_TEXT.get(key, key),
                bg=CARD_BG,
                fg=color,
                font=FONT_BOLD,
                width=4,
                anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=f"{count}", bg=CARD_BG, fg=TEXT, font=FONT_BOLD, width=3, anchor="e"
            ).pack(side="left")
            bar = tk.Canvas(row, width=BAR_MAX_WIDTH, height=10, bg=BG, highlightthickness=0, bd=0)
            bar.pack(side="left", padx=6)
            bar.create_rectangle(
                0, 0, max(1, int(BAR_MAX_WIDTH * share)), 10, fill=color, outline=""
            )
            tk.Label(
                row,
                text=f"{share * 100:.0f}%",
                bg=CARD_BG,
                fg=TEXT_MUTED,
                font=FONT_SMALL,
                width=4,
                anchor="e",
            ).pack(side="left")
        tk.Label(
            self._panel_frame,
            text="（明细名单见最小化气泡：单击分量展开）",
            bg=CARD_BG,
            fg=TEXT_MUTED,
            font=FONT_SMALL,
        ).pack(anchor="w", padx=10, pady=(8, 8))
        self._panel_frame.pack(side="left", anchor="n", padx=(14, 0))

    def _render_icon(self) -> None:
        """最小化态：56×56 的圆 + 「灵」字（背景透明不可用时退回实心方底）。"""
        size = MINIMIZED_SIZE
        color = self._icon_color()
        self._icon.configure(bg=TRANSPARENT_KEY if self._transparent else BG)
        self._icon.delete("all")
        self._icon.create_oval(1, 1, size - 1, size - 1, fill=color, outline="#ffffff", width=2)
        self._icon.create_text(
            size / 2, size / 2, text="灵", fill="#ffffff", font=("Microsoft YaHei UI", 13, "bold")
        )
        if self._own_stale():
            self._icon.create_oval(size - 14, 4, size - 7, 11, fill="#ffffff", outline="")

    def _icon_color(self) -> str:
        if self._role == ROLE_TEACHER:
            return ACCENT
        components = self._payload.get("bubble") or []
        return str(components[0].get("color", ACCENT)) if components else HIDDEN_FG

    # ── 冒烟自检（供 ``python -m app.client --selftest``）──────────────────

    def selftest(self) -> dict[str, Any]:
        """跑一遍两种形态的绘制并返回统计。

        存在的理由：GUI 代码在 CI 里跑不到，本地又只有「肉眼看一眼」这一种验证方式。
        有了它，至少能机器化地确认「构建 → 绘制 → 切形态 → 再绘制」这条路径不会抛错。
        """
        self.refresh()
        self.win.update()
        stats: dict[str, Any] = {
            "viewer": self._viewer_id,
            "role": self._role,
            "grid_cells": len(self._grid_frame.winfo_children()),
            "bubble_components": len(self._payload.get("bubble") or []),
            "has_summary": "summary" in self._payload,
            "hidden_switch_visible": self._role != ROLE_TEACHER,
            "transparent_supported": False,
            "global_hotkey": self._hotkey.is_global,
        }
        self.set_minimized(True)
        self.win.update()
        stats["icon_items"] = len(self._icon.find_all())
        stats["transparent_supported"] = self._transparent
        self.set_minimized(False)
        self.win.update()
        stats["grid_cells_after_restore"] = len(self._grid_frame.winfo_children())
        return stats
