"""悬浮层：气泡卫星窗口 + 右键菜单 + 全局退出热键。

为什么气泡是**独立卫星窗口**而不是画进主窗口
--------------------------------------------
圆形图标要做透明背景只能靠 ``-transparentcolor``，而它**仅 Windows 可用**。
若把气泡画进主窗口，窗口矩形必然包含气泡那块区域，在非 Windows 上就会露出一个
包住「气泡 + 圆」的方块（设计稿 §04.3.1）。拆出去之后主窗口最小化态只占约 56×56，
才真的是「一个圆形图标」。

卫星窗口**不持有任何业务状态** —— 内容由主窗口调 :mod:`app.present` 算好后经
:meth:`BubbleWindow.update_content` 灌进来。所以 D3 的「展开态与最小化态状态一致」
不受影响。

本模块同时承载「悬浮层」的另两件事：右键菜单与全局退出热键。它们都只在最小化态
（无边框 + 置顶）下才是刚需 —— 那种窗口没有标题栏、也不在任务栏里，**没有退出通道
就只能去任务管理器杀进程**（R2）。
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.client.theme import (
    BORDER,
    CARD_BG,
    FONT_BODY,
    FONT_BOLD,
    FONT_SMALL,
    HIDDEN_FG,
    MINIMIZED_SIZE,
    PANEL_BG,
    TEXT,
    TEXT_MUTED,
    TRANSPARENT_KEY,
)
from app.present.summary import LABEL_TEXT

__all__ = ["BubbleWindow", "ExitHotkey", "build_context_menu", "try_enable_transparency"]

#: 「状态 → 该状态的成员名单」。教师气泡的分量点击后展开它。
MemberMap = Mapping[str, Sequence[str]]

#: 一个气泡分量（线路格式，来自 ``app.present.bubble``）。
Component = Mapping[str, Any]


def try_enable_transparency(window: tk.Wm) -> bool:
    """尽力打开 ``-transparentcolor``；不支持时返回 ``False``（调用方降级为实心底）。

    参数类型是 ``tk.Wm`` 而不是 ``tk.Misc``：``attributes`` 定义在「窗口管理器」那一层
    （``Tk`` / ``Toplevel``），``Misc`` 上没有这个方法。

    这个属性**仅 Windows 可用**（设计稿 §05.2 的坑 1）。所以这里不能只捕获异常：
    在部分平台上它会被**静默忽略**（既不抛错也不生效），只捕获异常会把「静默忽略」
    误判成成功，于是界面上出现一块洋红色的方块。故**设完再回读一次**确认。
    """
    try:
        window.attributes("-transparentcolor", TRANSPARENT_KEY)
        applied = str(window.attributes("-transparentcolor"))
    except tk.TclError:
        return False
    return applied.lower().lstrip("#") == TRANSPARENT_KEY.lower().lstrip("#")


class ExitHotkey:
    """全局退出热键（默认 ``Ctrl+Alt+Q``）。

    无边框 + 置顶的窗口没有标题栏也不在任务栏里，必须有一条**随时可用**的退出路径（R2）。
    实现分两层：

    1. **窗口内**加速键 —— ``bind_all("<Control-Alt-q>")``，窗口有焦点时必生效；
    2. **系统级**热键 —— 仅在 Windows 上，用 ``ctypes`` 轮询 ``GetAsyncKeyState``。
       之所以不用 ``RegisterHotKey``：那要求另开一个线程跑消息泵，为了一个退出键不值得。

    Attributes:
        is_global: 是否拿到了真正的系统级热键。``False`` 时窗口失焦后热键无效 ——
            此时右键菜单是唯一保底通道（所以它才是**必做**项，热键属增强）。
    """

    #: 轮询间隔。人手按键不会短于这个量级，再密只是白烧 CPU。
    POLL_MS = 120

    _VK_CONTROL = 0x11
    _VK_MENU = 0x12  # Alt
    _VK_Q = 0x51
    _DOWN = 0x8000

    def __init__(self, root: tk.Misc, callback: Callable[[], None]) -> None:
        self._root = root
        self._callback = callback
        self._after_id: str | None = None
        self._user32 = self._load_user32()
        self.is_global = self._user32 is not None
        root.bind_all("<Control-Alt-q>", self._fire, add="+")
        if self.is_global:
            self._schedule()

    @staticmethod
    def _load_user32() -> Any:
        """取 ``user32`` 句柄；非 Windows 或取不到时返回 ``None``。

        ``ctypes.windll`` **只在 Windows 上存在**，而类型检查与 CI 都跑在 Linux 上；
        直接写 ``ctypes.windll`` 会让 mypy 报 attr-defined（它按运行平台判定），
        所以用 ``getattr`` 探测而不是静态引用。
        """
        if not sys.platform.startswith("win"):
            return None
        windll = getattr(ctypes, "windll", None)
        return None if windll is None else getattr(windll, "user32", None)

    def _fire(self, _event: Any = None) -> None:
        self._callback()

    def _schedule(self) -> None:
        self._after_id = self._root.after(self.POLL_MS, self._poll)

    def _poll(self) -> None:
        if self._pressed():
            self._fire()
            return
        self._schedule()

    def _pressed(self) -> bool:
        # 只有 is_global 为真时才会被调度到这里，user32 必然存在。
        assert self._user32 is not None
        return bool(
            self._user32.GetAsyncKeyState(self._VK_CONTROL) & self._DOWN
            and self._user32.GetAsyncKeyState(self._VK_MENU) & self._DOWN
            and self._user32.GetAsyncKeyState(self._VK_Q) & self._DOWN
        )

    def cancel(self) -> None:
        """停止轮询（退出前调用，避免 ``after`` 回调在窗口销毁后触发）。"""
        if self._after_id is None:
            return
        try:
            self._root.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None


def build_context_menu(
    master: tk.Misc,
    *,
    minimized: bool,
    hidden_var: tk.BooleanVar | None,
    on_toggle_minimize: Callable[[], None],
    on_toggle_hidden: Callable[[], None],
    on_exit: Callable[[], None],
) -> tk.Menu:
    """构造右键上下文菜单（R2 的**必做**退出通道）。

    ``hidden_var`` 为 ``None`` 时不出现隐藏条目 —— 教师端没有这个开关（D5）。
    文案按 R5 定为「**仅对同学隐藏（教师仍可见）**」：把真实可见范围直接写进菜单，
    而不是叫「隐藏我的状态」让用户误以为老师也看不到。

    每次弹出时**重建**菜单，而不是建一次改标签：这样「最小化 / 展开」的文案永远与
    当前状态一致，不会出现两份需要同步的状态。
    """
    menu = tk.Menu(master, tearoff=False)
    menu.add_command(
        label="展开窗口" if minimized else "最小化",
        command=on_toggle_minimize,
    )
    if hidden_var is not None:
        menu.add_checkbutton(
            label="仅对同学隐藏（教师仍可见）",
            variable=hidden_var,
            command=on_toggle_hidden,
        )
    menu.add_separator()
    menu.add_command(label="退出", command=on_exit)
    return menu


class BubbleWindow:
    """图标上方的信息气泡（卫星 ``Toplevel``）。

    形态与内容（设计稿 §04.3）：学生 = **1 个分量**（本人状态 + 置信度）；
    教师 = **4 个分量**（每状态一个，人数为 0 也占位置灰）。每个分量可单击展开该状态的
    名单浮层 —— **默认折叠**，因为教师可能正在投屏（R4）。
    """

    def __init__(self, master: tk.Misc) -> None:
        self._win = tk.Toplevel(master)
        self._win.withdraw()
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=BORDER)

        # 外层 Frame 留 1px 作为视觉边框（``overrideredirect`` 后没有系统边框可用）。
        self._body = tk.Frame(self._win, bg=CARD_BG, padx=10, pady=5)
        self._body.pack(padx=1, pady=1, fill="both", expand=True)

        self._row = tk.Frame(self._body, bg=CARD_BG)
        self._row.pack()
        self._foot = tk.Label(self._body, bg=CARD_BG, fg=TEXT_MUTED, font=FONT_SMALL)

        self._members: MemberMap = {}
        self._stale = False
        self._detail: tk.Toplevel | None = None
        self._open_key: str | None = None

    # ── 内容 ──────────────────────────────────────────────────────────────

    def update_content(
        self,
        components: Sequence[Component],
        *,
        members_by_label: MemberMap | None = None,
        online_count: int | None = None,
        stale: bool = False,
    ) -> None:
        """重绘气泡。``components`` 直接来自 ``payload["bubble"]``（已被服务端算好）。"""
        self._members = dict(members_by_label or {})
        self._stale = stale
        # 名单浮层的内容可能已过期（人数变了），一律先收起来。
        self._close_detail()

        for child in self._row.winfo_children():
            child.destroy()
        for index, component in enumerate(components):
            if index:
                tk.Label(self._row, text="·", bg=CARD_BG, fg=BORDER, font=FONT_BODY).pack(
                    side="left", padx=3
                )
            self._make_chip(component).pack(side="left")

        if online_count is None:
            self._foot.pack_forget()
        else:
            # D2：分母只显示**当前在线人数**，不写成 ``28/30`` 这种分数样式。
            self._foot.configure(text=f"在线 {online_count}")
            self._foot.pack(pady=(3, 0))

    def _chip_text(self, component: Component) -> str:
        key = str(component.get("label", ""))
        name = LABEL_TEXT.get(key, key)
        if "confidence" in component:
            text = f"{name} {float(component.get('confidence', 0.0)):.2f}"
            return f"{text} · 维持中" if self._stale else text
        return f"{name} {int(component.get('count', 0))}"

    def _make_chip(self, component: Component) -> tk.Label:
        key = str(component.get("label", ""))
        clickable = bool(self._members.get(key))
        chip = tk.Label(
            self._row,
            text=self._chip_text(component),
            bg=CARD_BG,
            fg=str(component.get("color", HIDDEN_FG)),
            font=FONT_BOLD,
            padx=3,
            cursor="hand2" if clickable else "",
        )
        if clickable:
            chip.bind("<Button-1>", self._click_handler(key))
        return chip

    def _click_handler(self, key: str) -> Callable[[Any], None]:
        """造一个「点了就展开/收起该状态名单」的回调。

        用闭包而不是 ``lambda``：``bind`` 的重载让 mypy 推不出 lambda 的参数类型
        （``Cannot infer type of lambda``），而显式写一个带注解的嵌套函数就没有歧义。
        """

        def _on_click(_event: Any) -> None:
            self._toggle_detail(key)

        return _on_click

    # ── 名单浮层（D1：分量单击展开；默认折叠 —— R4）───────────────────────

    def _toggle_detail(self, key: str) -> None:
        if self._open_key == key:
            self._close_detail()
            return
        self._close_detail()
        members = list(self._members.get(key, []))
        if not members:
            return
        self._open_key = key

        win = tk.Toplevel(self._win)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BORDER)
        body = tk.Frame(win, bg=PANEL_BG, padx=10, pady=6)
        body.pack(padx=1, pady=1, fill="both", expand=True)
        tk.Label(
            body,
            text=f"{LABEL_TEXT.get(key, key)} · {len(members)} 人",
            bg=PANEL_BG,
            fg=TEXT,
            font=FONT_BOLD,
        ).pack(anchor="w")
        for member_id in members:
            tk.Label(body, text=member_id, bg=PANEL_BG, fg=TEXT_MUTED, font=FONT_SMALL).pack(
                anchor="w"
            )

        def _close(_event: Any) -> None:
            self._close_detail()

        # 点浮层任意处即收起。子控件不会把事件冒泡给父窗口，所以逐个绑定。
        for widget in (win, body, *body.winfo_children()):
            widget.bind("<Button-1>", _close)

        self._detail = win
        self._place_detail()

    def _place_detail(self) -> None:
        if self._detail is None:
            return
        self._detail.update_idletasks()
        below = self._win.winfo_y() + self._bubble_height()
        self._detail.geometry(f"+{self._win.winfo_x()}+{below}")

    def _close_detail(self) -> None:
        self._open_key = None
        if self._detail is None:
            return
        try:
            self._detail.destroy()
        except tk.TclError:
            pass
        self._detail = None

    # ── 摆放与显隐 ────────────────────────────────────────────────────────

    def _bubble_height(self) -> int:
        self._win.update_idletasks()
        return max(self._win.winfo_reqheight(), 1)

    def reposition(self, icon_x: int, icon_y: int, icon_size: int = MINIMIZED_SIZE) -> None:
        """把气泡摆到图标**正上方并水平居中**（图标拖动时也要跟着走）。"""
        self._win.update_idletasks()
        width = max(self._win.winfo_reqwidth(), 1)
        x = icon_x + icon_size // 2 - width // 2
        y = icon_y - self._bubble_height() - 6
        self._win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._place_detail()

    def show(self) -> None:
        self._win.deiconify()
        self._win.attributes("-topmost", True)

    def hide(self) -> None:
        self._close_detail()
        self._win.withdraw()

    def destroy(self) -> None:
        self._close_detail()
        self._win.destroy()
