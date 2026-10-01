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

v7 蓝白圆角（2026-09-30）
------------------------
整体从「中性灰 + 四色块」改为**蓝白两带 + 大圆角**：

1. **颜色改由状态色承担，不再让状态色去当文字色**。原来的实现把状态色同时用在
   竖条和状态名文字上（``text_color = state_color``），这**逼着四个状态色都必须
   是文字级深色**，把它们全挤进同一条明度带里。现在拆开：竖条与形状符号（图形，
   按 3:1 验）用状态色，状态名文字用 ``INK_2``。四个状态色因此有了色相与明度的
   自由度，可以按「一眼可分」去选，而不必迁就文字对比度。
2. **圆角靠 Canvas 样条画**（见 :mod:`app.client.rounded`），卡片、面板、按钮、
   占比条统统带圆角。Tk 的样条在 16~28px 半径下最准，所以这套视觉正好落在它的
   舒适区 —— 大圆角不是将就，是扬长。
3. **状态卡改用 Canvas 画**而不是 ``Frame`` + ``Label``：圆角、圆头竖条、斜线纹理
   裁切都需要在同一个坐标系里作画，用控件拼不出来（``Frame`` 没有圆角属性，
   贴边斜线也没法裁）。代价是文字要自己 ``create_text`` 居中，收益是整格可控。

学生端与教师端的结构差异（A2 / §04.2）在这里**只体现为「建不建面板」** ——
判定「学生不该看到汇总」的是服务端（``hub.build_payload`` 对学生不下发
``summary`` 键），这里只是不建组件。
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable, Mapping
from typing import Any

from app.client import rounded
from app.client.bubblewin import (
    BubbleWindow,
    ExitHotkey,
    build_context_menu,
    try_enable_transparency,
)
from app.client.theme import (
    BG,
    BRAND,
    BRAND_SUBTLE,
    CARD_BG,
    FONT_BODY,
    FONT_BOLD,
    FONT_CAPTION,
    FONT_LABEL,
    FONT_MICRO,
    FONT_SMALL,
    FONT_TITLE,
    FONT_TITLE_LG,
    HAIRLINE,
    HAIRLINE_STRONG,
    HEADER_BG,
    HIDDEN_BG,
    HIDDEN_FG,
    HIDDEN_HATCH,
    INK,
    INK_2,
    INK_HI,
    MICRO,
    MINIMIZED_MARGIN,
    MINIMIZED_SIZE,
    OFFLINE_BG,
    PANEL_BG,
    RADIUS_LG,
    SCALE_STANDARD,
    SEGMENT_BAR_HEIGHT,
    SHAPE_GLYPHS,
    SPACING_LG,
    SPACING_MD,
    SPACING_XL,
    STALE_DOT,
    TRANSPARENT_KEY,
)
from app.envelope import ROLE_STUDENT, ROLE_TEACHER
from app.present import (
    DIM_COLOR,
    LABEL_ORDER,
    LABEL_TEXT,
    CardGeometry,
    card_geometry,
    changed_participants,
    color_for_key,
    color_for_key_count,
    columns_at,
    columns_for,
    diag_lines,
    freshness_key,
    offline,
    scale_for_width,
    shape_for_key,
)

__all__ = ["CELL_HEIGHT", "CELL_WIDTH", "ClientWindow"]

#: 快照来源：返回 ``app.hub.build_payload`` 那一套 payload。
SnapshotProvider = Callable[[], Mapping[str, Any]]

#: 「对同学隐藏」开关的上报通道（写回服务端；可见性裁剪在服务端，客户端改不了）。
HiddenReporter = Callable[[str, bool], None]

#: 状态卡尺寸（标准档）。权威值在 :mod:`app.present.scale` 的几何表里，
#: 这里保留模块级常量只为兼容旧引用（``__all__`` 里导出过）。
CELL_WIDTH = 98
CELL_HEIGHT = 80

#: 教师端汇总面板宽度（标准档）。同样以 ``present.scale`` 为准。
PANEL_WIDTH = 268

#: 汇总条形图的最大宽度（像素）。
BAR_MAX_WIDTH = 108

#: 顶栏高度。设计稿 §04.2：44px 够放标题 + 课堂号 chip + 控制按钮，
#: 且与状态卡的高比（80px）拉开层级。
HEADER_HEIGHT = 44

#: 底部图例条高度。
LEGEND_HEIGHT = 30

#: 学生端的开关文案 —— 这是**硬要求**（R5）：不把真实可见范围写进文案，
#: 就构成「误导性的隐私承诺」（学生以为老师也看不到）。
HIDE_SWITCH_TEXT = "仅对同学隐藏（教师仍可见）"

#: 卡片斜线纹理的步长（像素）。设计稿 §04.2「已隐藏」：45° 斜线，间距够疏才像
#: 纹理而不是「划掉」。
_HATCH_STEP = 9


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
        # Step3：变化高亮（变化的格加粗竖条 200ms 后恢复）与断网降级（全格降饱和）。
        # 两者都只影响画法，判定（谁变了/断没断）分别在 present.diff 与 refresh 的
        # 异常分支里 —— 这里只存「当前要画成什么样」的结果。
        self._changed_ids: set[str] = set()
        self._offline = False
        # Step4 诊断：最近一次成功刷新的耗时（毫秒）；None = 还没成功过 / 上次失败。
        self._last_refresh_ms: float | None = None

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
        # Step3 窗口缩放：只在「跨档」时重画（scale 值变了才 render），拖动窗口
        # 边缘时逐像素变化不会触发连续重排 —— 分档缩放的好处就在这。
        self._last_scale = 1.0
        self.win.bind("<Configure>", self._on_resize)
        # 图例画出来的实际宽度；``_build_legend`` 每次重排都会刷新它。
        self._legend_width = 1

        self._expanded_frame.pack(fill="both", expand=True)
        self.refresh()
        self._schedule_poll()

    # ── 搭界面 ────────────────────────────────────────────────────────────

    def _build_expanded(self) -> None:
        """搭出骨架。所有会随数据变化的内容都在 ``_render_*`` 里重画。

        顶栏与底栏用 ``tk.Frame``（它们不需要圆角，且顶栏要放原生 Button —— 原生
        按钮有系统外观，硬塞进 Canvas 反而更丑）。真正需要圆角的卡片、面板、
        占比条都在 Canvas 上画。
        """
        self._expanded_frame = tk.Frame(self.win, bg=BG)

        # ── 顶栏 ──
        # 左右**分成两个容器**，不再往同一条 pack 链上堆。
        # 起因（实机截图）：学生端有「仅对同学隐藏（教师仍可见）」这一长句复选框，
        # R5 要求文案不得简写，于是它与标题、状态、最小化四者挤在同一行 44px 里。
        # Tk 的 pack 不换行，左右两侧请求宽度相加大于窗口时就相互重叠 —— 标题被
        # 复选框盖住。现在左边只放品牌与身份，右边只放操作，两者互不侵占：
        # 左边用 ``fill="x", expand=True`` 吃掉剩余空间，右边的操作区按自身宽度
        # 贴右（``side="right"`` 先 pack，保证它优先拿到位置）。
        header = tk.Frame(self._expanded_frame, bg=HEADER_BG, height=HEADER_HEIGHT)
        header.pack(fill="x")
        header.pack_propagate(False)

        # 操作区先 pack（pack 的先后即优先级）：它必须完整可见，不能被标题挤掉。
        actions = tk.Frame(header, bg=HEADER_BG)
        actions.pack(side="right", padx=(SPACING_MD, SPACING_XL))
        tk.Button(
            actions,
            text="最小化",
            command=self.toggle_minimized,
            width=7,
            relief="flat",
            bg=HEADER_BG,
            fg=INK,
            activebackground=BRAND_SUBTLE,
            activeforeground=INK,
            highlightthickness=0,
            bd=0,
            font=FONT_BODY,
            cursor="hand2",
        ).pack(side="right")
        if self._role != ROLE_TEACHER:
            # R5：文案不得简写 —— 不写清可见范围就是误导性的隐私承诺。
            tk.Checkbutton(
                actions,
                text=HIDE_SWITCH_TEXT,
                variable=self._hidden_var,
                command=self._on_hidden_toggled,
                bg=HEADER_BG,
                fg=INK,
                activebackground=HEADER_BG,
                activeforeground=INK,
                selectcolor=CARD_BG,
                highlightthickness=0,
                bd=0,
                font=FONT_BODY,
                cursor="hand2",
            ).pack(side="right", padx=(0, SPACING_LG))

        # 身份区：品牌竖条 + 标题 + 状态，占据剩下的宽度。
        brand = tk.Frame(header, bg=HEADER_BG)
        brand.pack(side="left", fill="x", expand=True)
        # 左侧品牌竖条：4px 宽 16px 高的圆头蓝条（设计稿 §04.2）。
        # 用 Canvas 画而非 Frame —— 只有 Canvas 能画圆头。
        accent_bar = tk.Canvas(brand, width=4, height=16, bg=HEADER_BG, highlightthickness=0, bd=0)
        accent_bar.pack(side="left", padx=(SPACING_XL, SPACING_MD))
        accent_bar.create_line(2, 0, 2, 16, fill=BRAND, width=4, capstyle="round")
        # 标题与状态用同一条竖直中线对齐，读起来是「一个标题 + 一行副信息」，
        # 而不是三个并列的独立控件。状态另外降一号字并与标题拉开，避免与标题
        # 争抢视觉重量（Operate 模式：身份信息不是内容，标题才是）。
        title_group = tk.Frame(brand, bg=HEADER_BG)
        title_group.pack(side="left")
        tk.Label(
            title_group,
            text="灵犀学伴 · 课堂共享宫格",
            bg=HEADER_BG,
            fg=INK_HI,
            font=FONT_TITLE_LG,
        ).pack(side="left")
        self._status_label = tk.Label(
            title_group, textvariable=self._status, bg=HEADER_BG, fg=INK_2, font=FONT_SMALL
        )
        self._status_label.pack(side="left", padx=(SPACING_MD, 0))

        # ── 主体：宫格 + 汇总面板 ──
        # 宫格与图例同处一列并上下相邻：图例是宫格的读图说明，必须**紧跟宫格**。
        # 早先把图例直接 pack 到 ``_expanded_frame`` 底部（fill="x" + expand 的兄弟），
        # 窗口一高就把图例甩到窗口最下沿、中间留出一大片空白，读起来图例像是在讲
        # 别的东西。现在把这一列交给 ``stack``，图例随宫格自然下沉。
        body = tk.Frame(self._expanded_frame, bg=BG, padx=SPACING_XL, pady=SPACING_XL)
        body.pack(fill="both", expand=True)
        stack = tk.Frame(body, bg=BG)
        stack.pack(side="left", anchor="n")
        self._grid_frame = tk.Frame(stack, bg=BG)
        self._grid_frame.pack(anchor="nw")
        # 面板本身带圆角，所以用 Canvas 承载；内容由 ``_render_summary`` 画上去。
        self._panel_canvas: tk.Canvas | None = None

        # ── 底栏图例（紧跟宫格，不撑满宽度）──
        legend = tk.Frame(stack, bg=BG, height=LEGEND_HEIGHT)
        legend.pack(anchor="nw", pady=(SPACING_LG, 0))
        legend.pack_propagate(False)
        self._legend_frame = legend
        self._build_legend()

    def _build_legend(self) -> None:
        """图例：把四个状态的形状 + 名称、以及「维持中 / 已隐藏 / 无结果」讲清楚。

        以前是一行长文字（``● 维持中（本帧未达确认票数…）　·　灰格 = …``），
        现在改成**每项一个胶囊**：形状符号与状态色同时在，读起来是「样本」而不是
        「说明书」。形状是颜色之外的第二编码通道（灰度打印/色觉缺陷都还分得出）。

        宽度自适应（晚于首版补上）：图例横排五项在紧凑档会超出窗口右缘，把
        「已隐藏 斜线格：该同学…」直接切掉 —— 截图里就是这么断的，而且断在一句话
        中间比不显示更糟。现在按可用宽度**从右往左丢**：先丢三条说明的补充句，
        再整条丢掉「无结果 / 已隐藏」，最后只剩四个状态胶囊。四个状态胶囊本身
        是必读项，任何宽度下都保留。
        """
        for child in self._legend_frame.winfo_children():
            child.destroy()
        canvas = tk.Canvas(
            self._legend_frame, bg=BG, height=LEGEND_HEIGHT, highlightthickness=0, bd=0
        )
        canvas.pack(anchor="w")
        mid = LEGEND_HEIGHT / 2 - 2

        # 先量出四个状态胶囊的总宽（必留），据此决定右侧说明能放多少。
        pills: list[tuple[str, str, str, int]] = []
        probe = canvas.create_text(-9999, mid, text="", anchor="w", font=FONT_CAPTION)
        for key in LABEL_ORDER:
            label = LABEL_TEXT.get(key, key)
            glyph = SHAPE_GLYPHS.get(shape_for_key(key), "─")
            text = f"{glyph} {label}"
            canvas.itemconfigure(probe, text=text)
            _x0, _, _x1, _ = canvas.bbox(probe) or (0, 0, 60, 0)
            pills.append((key, label, text, _x1 - _x0))
        canvas.delete(probe)

        pill_span = sum(w + 24 for _, _, _, w in pills) + 28  # 胶囊内边距 + 分隔线
        # 说明按「重要 -> 次要」排序；宽度不够时从尾部整条丢弃。
        notes = (
            ("已隐藏", "斜线格：该同学已隐藏状态"),
            ("无结果", "空卡：本帧未形成判定"),
            ("●", "维持中：沿用上一稳定状态"),
        )
        avail = max(0, self._legend_frame.winfo_width() or 0)
        if avail <= 1:  # 首次渲染窗口还没映射，给一个保守预算，下一次重排会纠正
            avail = pill_span + 260

        drawn_notes: list[tuple[str, int]] = []
        probe2 = canvas.create_text(-9999, mid, text="", anchor="w", font=FONT_CAPTION)
        budget = avail - pill_span
        for sample, desc in notes:
            text = f"{sample} {desc}"
            canvas.itemconfigure(probe2, text=text)
            _x0, _, _x1, _ = canvas.bbox(probe2) or (0, 0, 120, 0)
            need = (_x1 - _x0) + 18
            if need > budget:
                continue  # 这条放不下，继续试更短的下一条
            budget -= need
            drawn_notes.append((text, _x1 - _x0))
        canvas.delete(probe2)

        x = 0.0
        for key, _label, text, _w in pills:
            color = color_for_key(key)
            text_id = canvas.create_text(
                x + 6, mid, text=text, anchor="w", fill=color, font=FONT_CAPTION
            )
            bx0, _, bx1, _ = canvas.bbox(text_id) or (x, 0, x + 40, 0)
            # 胶囊底：状态色压到 12% 左右在白底上的近似 —— 直接用一个极浅的品牌蓝，
            # 不与状态色混（混出来的色无法预先验对比度）。
            rounded.draw_pill(canvas, bx0 - 6, mid - 9, bx1 + 6, mid + 9, fill=BRAND_SUBTLE)
            canvas.tag_raise(text_id)
            x = bx1 + 16
        if drawn_notes:
            canvas.create_line(x, mid - 8, x, mid + 8, fill=HAIRLINE_STRONG, width=1)
            x += 14
            for text, _w in drawn_notes:
                text_id = canvas.create_text(
                    x, mid, text=text, anchor="w", fill=MICRO, font=FONT_CAPTION
                )
                _x0, _, x1, _ = canvas.bbox(text_id) or (x, 0, x + 100, 0)
                x = x1 + 18
        # 让 Frame 的请求宽度跟随实际画出来的内容，图例不会再把窗口撑宽。
        self._legend_width = int(x) if x > 0 else 1
        canvas.configure(width=self._legend_width)

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
        """取一次快照并重绘。传输失败只改状态栏 + 全格降饱和，不抛出去 —— 界面要能一直活着。"""
        try:
            t0 = time.perf_counter()
            payload = dict(self._provider())
        except Exception as exc:
            # 传输/服务端的任何异常都不该让窗口崩掉 —— 状态栏提示，下一轮再试。
            # 文案收拢在 ``present.offline``（改文案只动一处）；底色换 OFFLINE_BG 让
            # 「断网」在视觉上可感知，但不用警示红（R6：断联是常态不是警报）。
            # 宫格整体降饱和（_offline=True）：数据不再新鲜，视觉「褪」掉一档。
            self._offline = True
            self._last_refresh_ms = None
            self._status.set(offline(exc))
            self._status_label.configure(bg=OFFLINE_BG)
            self._render()
            return
        self._last_refresh_ms = (time.perf_counter() - t0) * 1000.0
        # 更新 payload **之前**算变化格（旧 grid 与新 grid 的签名差）——判定在
        # present.diff（已测），这里只存结果集合供 _make_cell 加粗竖条。
        old_grid = self._payload.get("grid", [])
        new_grid = payload.get("grid", [])
        self._changed_ids = changed_participants(old_grid, new_grid)
        self._payload = payload
        self._offline = False
        self._sync_hidden_switch()
        self._status_label.configure(bg=HEADER_BG)
        self._status.set(f"{self._viewer_id} · {self._role_label()}")
        self._render()
        if self._changed_ids:
            # 高亮只亮一拍（200ms 后清空重画），别让它常亮 —— 那是「有过变化」，
            # 不是「正在变化」。200ms 与轮询周期解耦：poll_ms 再长，高亮也只闪一下。
            self.win.after(200, self._clear_highlight)

    def _clear_highlight(self) -> None:
        self._changed_ids = set()
        self._render()

    def _on_resize(self, event: tk.Event) -> None:
        """窗口尺寸变化时，跨档才重画。

        ``<Configure>`` 有两个坑，两个都要挡：

        1. **它不只属于主窗口**：子控件每次改尺寸/位置也会发 ``<Configure>``，
           Tk 的 bindtag 会把这些事件一并送进本回调（``event.widget`` 是那个
           子控件而不是顶层窗口）。不挡的话，一次绘制过程中产生的子控件事件
           会再触发一次绘制 —— 也就是**在事件派发栈里销毁并重建控件**，Tk 会
           踩到已经释放的对象，直接**原生崩溃**（实测 Windows 上 `mainloop`
           内 0xC0000005 访问违例；`--selftest` 因为根本不进事件循环，完全
           测不出来）。
        2. **只在跨档时重画**：``<Configure>`` 在拖动窗口边缘时**每个像素**都
           触发，但分档缩放意味着绝大多数事件里 ``scale_for_width`` 的返回值
           不变 —— 只有跨档那一下值得重排。
        """
        if self._minimized or event.widget is not self.win:
            return
        scale = scale_for_width(event.width)
        if scale != self._last_scale:
            self._last_scale = scale
            self._render_expanded()
        else:
            # 同档内宽度也会变，而图例是按可用宽度取舍说明项的 —— 不重画就会出现
            # 「窗口变窄了，图例仍按旧宽度排」然后被右缘切掉。
            self._build_legend()

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
        # 先决定**这一帧要用哪一档**，再按那一档算列数。
        #
        # 关键顺序问题：``winfo_width()`` 在窗口尚未映射时返回 1（首次渲染必然如此），
        # 那时既不能按 1px 去选档，也不能按 1px 算列数 —— 旧实现在这里直接令
        # ``available`` 变成负数，``columns_at`` 夹到 1 列，于是首次渲染永远画成
        # 28 行长龙，直到用户手动拖窗口才恢复。这里改成：宽度不可信时按**上一档**
        # （首次为标准档）估算，并给一个「装得下宫格 + 面板」的最小宽度下限。
        measured = self.win.winfo_width()
        if measured > 1:
            scale = scale_for_width(measured)
        else:
            scale = SCALE_STANDARD
        geo = card_geometry(scale)
        self._last_scale = scale

        # 内容所需的最小宽度：宫格（按方阵算）+ 面板 + 全部留白。
        columns_ideal = columns_for(len(cells))
        grid_min = columns_ideal * (geo.width + geo.gap) + SPACING_XL * 2
        if self._role == ROLE_TEACHER:
            grid_min += geo.panel_width + SPACING_XL
        # ``max`` 而非直接 ``geometry(...)``：用户把窗口拖大过这个下界时不该被缩回来。
        if measured <= 1:
            content_width = grid_min
        else:
            content_width = max(measured, grid_min)
        self.win.minsize(grid_min, 1)

        available = content_width - SPACING_XL * 2
        if self._role == ROLE_TEACHER:
            available -= geo.panel_width + SPACING_XL
        columns = min(columns_for(len(cells)), columns_at(available, gap=geo.gap))
        for index, cell in enumerate(cells):
            widget = self._make_cell(cell, geo)
            widget.grid(
                row=index // columns,
                column=index % columns,
                padx=geo.gap // 2,
                pady=geo.gap // 2,
            )

        if self._role == ROLE_TEACHER:
            self._render_summary(geo)
        # A2：学生端连汇总**组件**都不建 —— 服务端也没下发 ``summary``。

        if self._autosize_pending:
            self._autosize_pending = False
            # 首次显式给宽高，而不是 ``geometry("")`` 让 Tk 自己量 ——
            # 自动量出来的是「所有控件自然尺寸之和」，在 28 人 + 面板时会宽到
            # 超出屏幕，反而不如按最小下界给。
            self.win.geometry(f"{content_width}x{self._content_height(cells, columns, geo)}")

    def _content_height(self, cells: list[Any], columns: int, geo: CardGeometry) -> int:
        """首次显示时该给多高：宫格按行数算，再补上顶栏与底栏图例。

        ``chrome`` 的四项必须与 ``_build_expanded`` 里实际 pack 的控件一一对应，
        否则「算出来的高度」与「画出来的高度」不一致，底栏会被挤掉（这正是本次
        改版踩到的坑：图例框只有 30px，但 ``body`` 是 ``expand=True`` 先把空间
        吃光，图例就被压到窗口外看不见）。逐项核对过：

        - ``HEADER_HEIGHT`` 44 —— 顶栏 ``pack(fill="x")``；
        - ``SPACING_XL * 2`` 24 —— 主体 ``pady=SPACING_XL`` 上下各一；
        - ``LEGEND_HEIGHT`` 30 —— 底栏 ``height=LEGEND_HEIGHT``；
        - ``SPACING_LG`` 8 —— 底栏 ``pady=(0, SPACING_LG)`` 的下边距。
        """
        rows = max(1, (len(cells) + columns - 1) // columns)
        # 每格 grid 的 pady=gap//2 两侧都要算，所以是 (height + gap)。
        grid_h = rows * (geo.height + geo.gap)
        chrome = HEADER_HEIGHT + SPACING_XL * 2 + LEGEND_HEIGHT + SPACING_LG
        # 教师端的面板可能比宫格高（尤其展开诊断），取两者之大。
        if self._role == ROLE_TEACHER:
            panel_h = self._panel_canvas_height()
            grid_h = max(grid_h, panel_h)
        # 屏幕高度兜底：小屏笔记本上宁可让底栏被挤，也不要窗口高到超出屏幕
        # （超出后标题栏都点不到，用户没法拖动）。留 120px 给任务栏与窗口边框。
        screen_cap = max(320, self.win.winfo_screenheight() - 120)
        return int(min(grid_h + chrome, screen_cap))

    def _panel_canvas_height(self) -> int:
        """面板当前需要的高度（与 :meth:`_render_summary` 同一套算式）。"""
        diag_expanded = getattr(self, "_diag_expanded", False)
        if not diag_expanded:
            return 96 + 4 * 30 + 44 + 22
        lines = diag_lines(
            self._payload,
            viewer=self._viewer_id,
            now_ts=time.time(),
            refresh_ms=self._last_refresh_ms,
        )
        return 96 + 4 * 30 + 44 + (18 * len(lines) + 22)

    def _make_cell(self, cell: Mapping[str, Any], geo: CardGeometry) -> tk.Widget:
        """一格 = 一个参与者，整格用 Canvas 画。

        为什么从 ``Frame``+``Label`` 改成 Canvas：圆角、圆头竖条、**裁切过的**
        斜线纹理这三样都需要在同一个坐标系里作画。用控件拼不出来 —— ``Frame``
        没有圆角属性，``Canvas`` 贴边斜线也没法裁（旧实现就是硬画的，斜线会戳出
        卡片边界）。

        三种形态的**判据**（都由服务端与 present 决定，这里只读）：

        - 有 ``state`` 且 ``label`` 非空 → 状态卡（状态色竖条 + 形状 + 状态名）；
        - ``hidden`` → 斜线纹理格（§10.1 d2：**仍占格**，因为 ``occupies_cell``
          为真；这是「有，但不给你看」，不是「没有」）；
        - 其余 → 空卡（本帧未形成判定，与 hidden 的纹理区分开）。

        「状态」这件事被**三重编码**，任何单一通道失效都还读得出来：

        1. **形状符号**（● ▲ ◆ ─）—— 灰度打印、色觉缺陷、投影仪偏色都不受影响；
        2. **状态色**（竖条 + 中文名）—— 四值都经 ``test_contrast`` 守住 ≥4.5:1；
        3. **中文名**（专注/困惑/分神/未知）—— 语言通道，前两者全废也还认得。

        编号用 ``INK`` 系、随新鲜度衰减（fresh/aging/stale 三档都 ≥4.5:1）；
        状态名用状态色，**不**随新鲜度衰减 —— 褪色的是「这条信息有多新」，
        不是「这是什么状态」，混在一起会让 stale 格看起来像换了状态。
        """
        state = cell.get("state")
        hidden = bool(cell.get("hidden"))
        participant_id = str(cell.get("participant_id", "?"))
        width, height = geo.width, geo.height
        radius = geo.radius

        canvas = tk.Canvas(
            self._grid_frame,
            width=width,
            height=height,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )

        if isinstance(state, Mapping) and state.get("label"):
            key = str(state["label"])
            state_color = color_for_key(key)
            sub_text = LABEL_TEXT.get(key, key)
            stale = bool(state.get("stale"))
            level = freshness_key(time.time(), state)  # fresh / aging / stale
            glyph = SHAPE_GLYPHS.get(shape_for_key(key), "─")
            # 断网降级：颜色主通道整体褪掉，只留「有这么一格」的轮廓。
            bar_color = DIM_COLOR if self._offline else state_color
            # 状态名**用状态色**（不是在 docstring 里说的 INK_2）：
            # 这是颜色之外的第二编码通道里的「颜色」那一半，去掉它之后就只剩形状
            # 能分了 —— 而形状符号只有 8pt，远看几乎认不出。可达性靠三重冗余保障：
            # 状态色本身压白卡 ≥4.5:1（test_contrast 守着），形状符号再兜一层，
            # 中文名最后兜一层。旧实现的问题是「只用状态色」，不是「用了状态色」。
            name_color = DIM_COLOR if self._offline else state_color
            # 编号（次要信息）按新鲜度衰减：fresh=INK / aging=INK_2 / stale=MICRO。
            id_color = INK if level == "fresh" else INK_2 if level == "aging" else MICRO
            changed = participant_id in self._changed_ids and not self._offline

            self._draw_card(canvas, width, height, radius)
            # 左侧状态条：圆头、压在圆角之内（否则端帽会戳出卡片）。
            # 宽度 5px：3px 在圆头端帽下半径只有 1.5px，样条基本画不出圆角
            # （旧实现就是这样，端头看起来像被削平）。5px 才有可见的圆头。
            bar_w = 6 if changed else 5
            inset = 5.0
            rounded.draw_pill(
                canvas,
                inset,
                inset + 6,
                inset + bar_w,
                height - inset - 6,
                fill=bar_color,
            )
            text_left = inset + bar_w + 10
            canvas.create_text(
                text_left,
                height * 0.36,
                text=participant_id,
                anchor="w",
                fill=id_color,
                font=FONT_LABEL,
            )
            canvas.create_text(
                text_left,
                height * 0.68,
                text=f"{glyph} {sub_text}",
                anchor="w",
                fill=name_color,
                font=FONT_BOLD,
            )
            if stale:
                # 「维持中」用小圆点角标，不换色 —— 契约层明确要求它**不是**新状态。
                # 位置贴右上圆角：旧实现放在 (width−radius*0.55, radius*0.55)，
                # 在 98×80 的卡上正好落到圆角外沿，看起来像粘在边框上。
                dot_r = 2.5
                cx = width - 12.0
                cy = 12.0
                canvas.create_oval(
                    cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r, fill=STALE_DOT, outline=""
                )
            return canvas

        if hidden:
            # §10.1 d2：仍占格、但不显示状态色（R6：不做醒目提示）。
            self._draw_card(canvas, width, height, radius, fill=HIDDEN_BG)
            self._draw_hatch(canvas, width, height, radius)
            canvas.create_text(
                width / 2,
                height * 0.40,
                text=participant_id,
                fill=HIDDEN_FG,
                font=FONT_LABEL,
            )
            canvas.create_text(
                width / 2,
                height * 0.68,
                text="已隐藏",
                fill=HIDDEN_FG,
                font=FONT_CAPTION,
            )
            return canvas

        # 本帧还没形成判定（融合层拒判 / 平滑层票数不足）。空卡 + 中性文案，
        # 与 hidden 的斜线纹理区分开：一个是「没有可展示的东西」，一个是「有但不给你看」。
        self._draw_card(canvas, width, height, radius)
        canvas.create_text(width / 2, height * 0.40, text=participant_id, fill=INK, font=FONT_LABEL)
        canvas.create_text(width / 2, height * 0.68, text="无结果", fill=INK_2, font=FONT_CAPTION)
        return canvas

    def _draw_card(
        self,
        canvas: tk.Canvas,
        width: int,
        height: int,
        radius: float,
        *,
        fill: str = CARD_BG,
    ) -> None:
        """画卡片底：圆角 + 一圈极浅的轮廓。

        原本是「白卡 + 1px 灰蓝描边」。实机看下来那圈线把卡片读成了「盒子」——
        白底、圆角、等宽描边、整齐排列，正是任何同类页面都会产出的默认观感
        （frontend-design 点名的 SaaS 卡片套件 tell 之一）。按「出门前摘掉一件
        配饰」，摘掉的就是这圈线：卡片与画布靠**明度差**分开（``BG`` 是淡蓝灰、
        ``CARD_BG`` 是纯白，对比 1.16:1），比一圈线更安静也更不容易显得廉价。

        只保留一圈比填充深一档的**同色系**轮廓，且仅在卡片有状态色竖条时才有
        意义 —— 它现在的作用是给圆角一个收口，而不是给卡片加边框。描边仍旧用
        **双层**而不是 ``outline=``：Tk 的样条描边会把「重复控制点」也描出来，
        圆角处会显得比直边粗一点；先画稍大的轮廓色圆角矩形、再在上面画填充色，
        就得到一圈均匀的 1px 边。
        """
        rounded.draw_rounded_rect(
            canvas, 0.5, 0.5, width - 0.5, height - 0.5, radius, fill=HAIRLINE
        )
        rounded.draw_rounded_rect(
            canvas, 1.5, 1.5, width - 1.5, height - 1.5, max(1.0, radius - 1), fill=fill
        )

    def _draw_hatch(self, canvas: tk.Canvas, width: int, height: int, radius: float) -> None:
        """45° 斜线纹理，**裁在圆角内**。

        裁切在 Tk 上比看上去麻烦：``Canvas`` 没有通用的裁剪区域，而圆角又只能靠
        ``create_polygon(smooth=True)`` 画。硬画斜线的后果是线头戳出圆角（旧实现
        就是这样，斜线从卡片方角一直伸到外面，圆角显得像贴上去的）。

        可行的办法是**限制线段端点**：每条 45° 线都算出它与「内缩 radius 后的矩形」
        的交点，只画那一段。四角于是留白 —— 但那正好是圆角所在，看不出来。
        代价是纹理覆盖率略低于满铺，视觉上完全够用（纹理只需传达「有内容」）。
        """
        pad = min(radius, min(width, height) / 2 - 2)
        inner_w = width - pad * 2
        inner_h = height - pad * 2
        for offset in range(-int(inner_h), int(inner_w), _HATCH_STEP):
            x0 = pad + offset
            y0 = pad + inner_h
            x1 = pad + offset + inner_h
            y1 = pad
            # 45° 线的端点在 x 方向夹紧后，y 也顺带夹住了（斜率是 ±1）。
            if x0 < pad:
                y0 -= pad - x0
                x0 = pad
            if x1 > pad + inner_w:
                y1 += x1 - (pad + inner_w)
                x1 = pad + inner_w
            canvas.create_line(x0, y0, x1, y1, fill=HIDDEN_HATCH, width=1)

    def _render_summary(self, geo: CardGeometry) -> None:
        """教师端汇总面板：4 行状态分布 + 圆头占比条（D1 分量 / D2 分母）。

        面板整块画在一张 Canvas 上（圆角容器 + 圆头占比条都要样条），所以这里
        不是「搭控件」而是「按坐标画」。坐标从一个游标 ``y`` 往下推，改版时只动
        这一处的数字。
        """
        summary = self._payload.get("summary") or {}
        by_label = summary.get("by_label") or {}
        ratio = summary.get("ratio") or {}
        online = int(summary.get("online_count", 0))

        panel_w = geo.panel_width
        # 面板高度按内容算，不留大块空白：标题 + 在线行 + 4 行 + 提示 + 诊断。
        diag_expanded = getattr(self, "_diag_expanded", False)
        diag_count = 0
        diag_texts: list[str] = []
        if diag_expanded:
            diag_texts = diag_lines(
                self._payload,
                viewer=self._viewer_id,
                now_ts=time.time(),
                refresh_ms=self._last_refresh_ms,
            )
            diag_count = len(diag_texts)
        panel_h = 96 + 4 * 30 + 44 + (18 * diag_count + 22 if diag_expanded else 22)

        if self._panel_canvas is None or self._panel_is_stale(panel_w, panel_h):
            if self._panel_canvas is not None:
                self._panel_canvas.destroy()
            self._panel_canvas = tk.Canvas(
                self._grid_frame.master,
                width=panel_w,
                height=panel_h,
                bg=BG,
                highlightthickness=0,
                bd=0,
            )
            self._panel_canvas.pack(side="left", anchor="n", padx=(SPACING_XL, 0))
        canvas = self._panel_canvas
        canvas.configure(width=panel_w, height=panel_h)
        canvas.delete("all")
        # 面板底：圆角 20px（RADIUS_LG）。这是界面上最大的一块圆角，除气泡外。
        rounded.draw_rounded_rect(canvas, 0, 0, panel_w, panel_h, RADIUS_LG, fill=HAIRLINE_STRONG)
        rounded.draw_rounded_rect(
            canvas, 1, 1, panel_w - 1, panel_h - 1, RADIUS_LG - 1, fill=PANEL_BG
        )

        pad = 16
        y = 18.0
        canvas.create_text(pad, y, text="课堂汇总", anchor="w", fill=INK_HI, font=FONT_TITLE)
        y += 20
        canvas.create_text(
            pad, y, text=f"在线 {online} 人", anchor="w", fill=INK_2, font=FONT_SMALL
        )
        y += 20

        # 占比条几何：名称 46 / 计数 34 / 条自适应 / 百分比 42。
        # 条宽**必须按面板宽反算**而不是用常量：``BAR_MAX_WIDTH=108`` 在 248px 的
        # 紧凑档面板上会让「108 + 42 + 16(右留白)」越过右边框 —— 实测紧凑档下
        # 百分比数字被画到了面板外。这里把条的可用宽度夹住，任何档位都不溢出。
        name_w, count_w, pct_w = 46, 34, 42
        bar_x = pad + name_w + count_w + 8
        bar_max = max(24, panel_w - pad - bar_x - pct_w - 8)
        bar_h = SEGMENT_BAR_HEIGHT
        for key in LABEL_ORDER:
            count = int(by_label.get(key, 0))
            share = float(ratio.get(key, 0.0))
            # 0 人的分量画成置灰占位 —— 但**仍然画一条**，这样四行的条长可对比；
            # 「有没有」与「有但为 0」在颜色上区分（规则在 present.colors，已测）。
            color = color_for_key_count(key, count)
            mid = y + 8
            canvas.create_text(
                pad, mid, text=LABEL_TEXT.get(key, key), anchor="w", fill=color, font=FONT_BOLD
            )
            canvas.create_text(
                pad + name_w + count_w,
                mid,
                text=str(count),
                anchor="e",
                fill=INK,
                font=FONT_MICRO,
            )
            rounded.draw_progress_bar(
                canvas,
                bar_x,
                mid - bar_h / 2,
                bar_x + bar_max,
                mid + bar_h / 2,
                share,
                track=HAIRLINE,
                fill=color,
                height=bar_h,
            )
            canvas.create_text(
                bar_x + bar_max + 8,
                mid,
                text=f"{share * 100:.0f}%",
                anchor="w",
                fill=INK_2,
                font=FONT_MICRO,
            )
            y += 30

        y += 6
        canvas.create_text(
            pad,
            y,
            text="（明细名单见最小化气泡：单击分量展开）",
            anchor="w",
            fill=MICRO,
            font=FONT_CAPTION,
            width=panel_w - pad * 2,
        )
        y += 22

        # 诊断折叠区：默认收起。数据源是纯函数 ``diag_lines``（已测），这里只管画。
        arrow = "▾" if diag_expanded else "▸"
        canvas.create_text(pad, y, text=f"{arrow} 诊断", anchor="w", fill=MICRO, font=FONT_CAPTION)
        # 点击热区：整行都能点（不只是那三个字），省得用户瞄不准。
        # 用透明填充的矩形 + ``fill=""``：Tk 里 ``fill=""`` 是「不填充」，
        # 但**仍然参与命中测试** —— 这比只给文字绑事件宽容得多。
        canvas.create_rectangle(
            0, y - 10, panel_w, y + 10, outline="", fill="", width=0, tags="diag_hit"
        )
        canvas.tag_bind("diag_hit", "<Button-1>", lambda _e: self._toggle_diag())
        # 手型光标只在**这一条热区**上出现，不是整块面板：面板上还有别的东西
        # （占比条、名单提示），整块给手型会让人以为到处都能点。
        canvas.tag_bind("diag_hit", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("diag_hit", "<Leave>", lambda _e: canvas.configure(cursor=""))
        y += 18
        if diag_expanded:
            for line in diag_texts:
                canvas.create_text(pad + 8, y, text=line, anchor="w", fill=MICRO, font=FONT_MICRO)
                y += 18

    def _panel_is_stale(self, width: int, height: int) -> bool:
        """面板 Canvas 尺寸变了才重建（否则每次刷新都重建一遍控件，闪）。"""
        canvas = self._panel_canvas
        if canvas is None:
            return True
        try:
            return int(canvas.cget("width")) != width or int(canvas.cget("height")) != height
        except (tk.TclError, ValueError):
            return True

    def _toggle_diag(self) -> None:
        self._diag_expanded = not getattr(self, "_diag_expanded", False)
        self._render()

    def _render_icon(self) -> None:
        """最小化态：56×56 的圆 + 「灵」字（背景透明不可用时退回实心方底）。

        字体走 ``theme`` 而不是硬编码 —— 旧实现这里写死了 ``"Microsoft YaHei UI"``，
        绕过了主题的回退链（教室机器没这个字体时，只有这一个字会掉回默认字体）。
        """
        size = MINIMIZED_SIZE
        color = self._icon_color()
        self._icon.configure(bg=TRANSPARENT_KEY if self._transparent else BG)
        self._icon.delete("all")
        # 白色外圈让圆从任何底色上「浮」起来（透明不可用时它压在蓝画布上）。
        self._icon.create_oval(0, 0, size, size, fill=CARD_BG, outline="")
        self._icon.create_oval(1, 1, size - 1, size - 1, fill=color, outline="")
        self._icon.create_text(
            size / 2, size / 2, text="灵", fill=CARD_BG, font=(FONT_TITLE[0], 15, "bold")
        )
        if self._own_stale():
            dot_r = 3.5
            cx, cy = size - 12, 12
            self._icon.create_oval(
                cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r, fill=STALE_DOT, outline=CARD_BG
            )

    def _icon_color(self) -> str:
        if self._role == ROLE_TEACHER:
            return BRAND
        components = self._payload.get("bubble") or []
        return str(components[0].get("color", BRAND)) if components else HIDDEN_FG

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
