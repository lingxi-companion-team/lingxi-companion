"""v8 统一单页 —— **一套页面，不分教师端 / 学生端**。

设计依据
--------
需求文档（通用网课场景版）§模块二.1::

    所有用户通过同一链接/邀请码进入房间，页面基础布局完全一致
    ……管理功能仅对发起人可见

所以本页只有**一个**布局。角色（``payload["initiator"]``）只控制一个布尔值：
右侧「发起人管理台」可不可见。此外没有任何 ``if is_teacher`` 式的分支 ——
连「能不能看见别人的状态」都不由角色决定，那是房间公开规则（§模块三.2）的事。

页面分区
--------
::

    ┌──────────────────────────────────────────────────────────────┐
    │ 房间头部：名称 / 主题 / 邀请码 / 四档人数 / 当前公开规则      │
    ├────────────┬───────────────────────────────┬─────────────────┤
    │ 我的状态    │ 房间状态概览（5 张 KPI 卡）    │ 发起人管理台     │
    │ 状态+置信度 │ 房间状态趋势（本页内存）       │ （仅发起人可见） │
    │ 公开开关 ✅ │ 参与者宫格（可筛选 / 排序）    │                 │
    │ 感知开关 ⛔ │                               │                 │
    └────────────┴───────────────────────────────┴─────────────────┘

✅ = 真的接通（``POST /hidden``）；⛔ = 明确留「未接入」态，**不填假数据**。

为什么这一层「薄」
------------------
``flet`` 不在 CI 的依赖清单里，所以本模块在 CI 里一行都跑不到（覆盖率已 omit）。
因此这里**不许有任何判定规则**：筛哪个档、怎么排序、谁该看到管理台、
什么颜色代表什么状态、被掩去的人该显示什么字，全部来自 :mod:`app.present`
（纯函数、有测试）。本模块只做三件事：读 payload、调用 present 层、把结果画成控件。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from app.demo import HttpSnapshot
from app.envelope import ParticipantState
from app.present import console, skin
from app.present.colors import (
    CLOSED_COLOR,
    DIM_COLOR,
    color_for_key,
    fill_for_key,
    on_fill,
)
from app.present.console import ConsoleItem, ConsoleSection
from app.present.filters import (
    BUCKET_ALL,
    SORT_ID,
    filter_counts,
    filter_options,
    normalize_filter,
    normalize_sort,
    sort_options,
    sort_rows,
)
from app.present.kpi import KpiCard, kpi_cards
from app.present.roster import RosterRow, roster_rows
from app.present.scale import card_geometry, scale_for_width
from app.present.shape import shape_for_key
from app.present.summary import CLOSED_KEY, LABEL_ORDER, LABEL_TEXT, Summary
from app.present.trend import TrendBuffer
from app.room import POLICY_MANDATORY, POLICY_OPTIONAL, POLICY_TEXT

__all__ = ["POLL_SECONDS", "TREND_WINDOW", "VIEWER_LIMIT", "UnifiedPage"]

#: 轮询周期（秒）。与 ``app.client`` 的节奏一致：更快只会让画面抖，
#: 更慢则「实时统计」这句话就不成立了。
POLL_SECONDS = 1.2

#: 趋势缓冲的窗口（帧数）。60 帧 × 1.2s ≈ 72 秒 —— 够看出「这一两分钟」的走向。
TREND_WINDOW = 60

#: 形状名 → 可绘制字符。形状名本身是 present 层的业务概念（有测试），
#: 用哪个字符画是画法选择，所以映射留在这里（与 ``app.client.theme`` 同款）。
SHAPE_GLYPHS: dict[str, str] = {
    "round": "●",
    "triangle": "▲",
    "diamond": "◆",
    "flat": "─",
}

#: 观看者切换器最多列几个人（**演示用**控件，不是产品功能）。
VIEWER_LIMIT = 8

#: 页面还没拿到宽度时的兜底（Flet 首帧 ``page.width`` 可能是 ``None``）。
DEFAULT_WIDTH = 1440


# ── 小工具：把 present 层的值画成控件 ──────────────────────────────────


def _text(
    value: str,
    *,
    size: int = 13,
    color: str = skin.INK,
    weight: ft.FontWeight | None = None,
    selectable: bool = False,
    max_lines: int | None = None,
) -> ft.Text:
    return ft.Text(
        value,
        size=size,
        color=color,
        weight=weight,
        selectable=selectable,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS if max_lines else None,
    )


def _tag(text: str, *, fg: str, bg: str) -> ft.Container:
    """一个极小的胶囊标签（「已接通」/「未接入」这类）。"""
    return ft.Container(
        content=_text(text, size=11, color=fg, weight=ft.FontWeight.BOLD),
        bgcolor=bg,
        border_radius=skin.RADIUS_XS,
        padding=ft.padding.symmetric(3, 8),
    )


def _card(
    content: ft.Control,
    *,
    padding: int = 20,
    radius: int = skin.RADIUS_LG,
    bgcolor: str = skin.CARD_BG,
) -> ft.Container:
    """统一的卡片外壳：白底、发丝线边框、偏移暗块阴影。

    阴影取「画布压深一档」的浅蓝灰（``skin.SHADOW``）—— 它读起来是卡片下缘的
    一点厚度，而不是一块糊上去的灰。卡片半径只有 ``LG`` 一档：同层级的卡
    必须同半径，否则摞在一起会「一深一浅」。
    """
    return ft.Container(
        content=content,
        bgcolor=bgcolor,
        border_radius=radius,
        padding=padding,
        border=ft.border.all(1, skin.HAIRLINE),
        shadow=ft.BoxShadow(
            blur_radius=10,
            spread_radius=0,
            color=skin.SHADOW,
            offset=ft.Offset(0, 2),
        ),
    )


def _section_title(title: str, hint: str = "") -> ft.Control:
    return ft.Column(
        [
            _text(title, size=15, color=skin.INK_HI, weight=ft.FontWeight.BOLD),
            *([_text(hint, size=11, color=skin.MICRO)] if hint else []),
        ],
        spacing=2,
    )


def _divider() -> ft.Container:
    return ft.Container(height=1, bgcolor=skin.HAIRLINE)


def _stat(label: str, value: str, *, color: str = skin.INK_HI, hint: str = "") -> ft.Control:
    """头部的一个统计小格（标签 + 大数字）。"""
    box: ft.Control = ft.Container(
        content=ft.Column(
            [
                _text(label, size=11, color=skin.MICRO),
                _text(value, size=20, color=color, weight=ft.FontWeight.BOLD),
            ],
            spacing=0,
        ),
        padding=ft.padding.symmetric(6, 12),
        border_radius=skin.RADIUS_SM,
        bgcolor=skin.CARD_BG,
    )
    return ft.Container(content=box, tooltip=hint or None)


def _label_text(key: str) -> str:
    """线路键 → 中文名。已关闭者不在 ``LABEL_TEXT`` 里，单独兜一下。"""
    if key == CLOSED_KEY:
        return "已关闭感知"
    return LABEL_TEXT.get(key, key)


def _series_color(key: str) -> str:
    return CLOSED_COLOR if key == CLOSED_KEY else color_for_key(key)


def _legend_dot(color: str, text: str) -> ft.Control:
    return ft.Row(
        [
            ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
            _text(text, size=11, color=skin.MICRO),
        ],
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


class UnifiedPage:
    """一套页面。构造一次，之后每次轮询只替换动态区块的内容。

    线程模型：一个后台线程轮询 ``GET /snapshot``，每次拿到新 payload 就重建动态区块
    并 ``page.update()``；用户点击（筛选 / 排序 / 开关）走 Flet 的主线程回调。
    两条路径都会改 ``self._payload`` 与控件树，所以用一把 ``RLock`` 串起来 ——
    否则「点击正好撞上轮询」会偶发地渲染出半新半旧的一帧。
    """

    def __init__(
        self,
        page: ft.Page,
        client: HttpSnapshot,
        *,
        viewer_id: str,
        viewers: Sequence[str],
        base_url: str,
    ) -> None:
        self.page = page
        self._client = client
        self._base_url = base_url
        self._viewer_id = viewer_id
        self._viewers = list(viewers)

        #: 页面级视图状态（由工具条上的胶囊回写）。
        self._filter_key = BUCKET_ALL
        self._sort_key = SORT_ID

        self._payload: dict[str, Any] | None = None
        self._error: str | None = None
        self._trend = TrendBuffer(TREND_WINDOW)
        self._alive = threading.Event()
        self._alive.set()
        self._lock = threading.RLock()

        #: 每次轮询都会重建内容的区块。
        self._demo_bar = ft.Container()
        self._header = ft.Container()
        self._personal = ft.Container()
        self._overview = ft.Container()
        self._trend_box = ft.Container()
        self._toolbar = ft.Container()
        self._grid = ft.Container()
        self._admin = ft.Container()
        self._footer = ft.Container()

    # ── 构建 ────────────────────────────────────────────────────────────

    def build(self) -> None:
        page = self.page
        page.title = "灵犀学伴 · 学习房间"
        page.bgcolor = skin.BG
        page.padding = ft.padding.symmetric(20, 20)
        page.scroll = ft.ScrollMode.AUTO
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(font_family="LingxiSans", use_material3=True)
        page.on_disconnect = lambda _e: self.stop_polling()

        page.add(
            ft.Column(
                [
                    self._demo_bar,
                    self._header,
                    ft.ResponsiveRow(
                        [
                            ft.Container(self._personal, col={"xs": 12, "md": 12, "lg": 3}),
                            ft.Container(
                                ft.Column(
                                    [
                                        self._overview,
                                        self._trend_box,
                                        self._toolbar,
                                        self._grid,
                                    ],
                                    spacing=16,
                                ),
                                col={"xs": 12, "md": 12, "lg": 6},
                            ),
                            ft.Container(self._admin, col={"xs": 12, "md": 12, "lg": 3}),
                        ],
                        spacing=16,
                        run_spacing=16,
                    ),
                    self._footer,
                ],
                spacing=16,
            )
        )

        self._footer.content = _text(
            "灵犀学伴 v8 · 端侧多智能体情感感知 · 本页每一个状态都真的过了一遍"
            "「三路推理 → 环境驱动动态加权融合 → 3 中 2 时序平滑」，不是随机涂色",
            size=11,
            color=skin.MICRO,
        )
        self._render()

    # ── 轮询 ────────────────────────────────────────────────────────────

    def start_polling(self) -> None:
        threading.Thread(target=self._poll_loop, name="lingxi-poll", daemon=True).start()

    def stop_polling(self) -> None:
        self._alive.clear()

    def _poll_loop(self) -> None:
        while self._alive.is_set():
            try:
                payload = self._client.snapshot()
            except Exception as exc:  # 断线是常态，不该让页面崩掉
                with self._lock:
                    self._error = type(exc).__name__
            else:
                with self._lock:
                    self._error = None
                    self._payload = payload
                    self._trend.push(Summary.from_dict(payload.get("summary") or {}))
            self._refresh()
            self._alive.wait(POLL_SECONDS)

    def _refresh(self) -> None:
        """重建动态区块并推给浏览器。

        从后台线程调用是 Flet 允许的；页面断开后 ``page.update()`` 会抛异常 ——
        那是正常退出路径，静默收手即可，不该在后台线程里冒出一条 traceback。
        """
        if not self._alive.is_set():
            return
        try:
            with self._lock:
                self._render()
                self.page.update()
        except Exception:
            self._alive.clear()

    # ── 渲染 ────────────────────────────────────────────────────────────

    def _render(self) -> None:
        payload = self._payload
        self._demo_bar.content = self._demo_bar_card()

        if payload is None:
            message = "正在连接房间…" if self._error is None else f"连不上房间（{self._error}）"
            self._header.content = _card(_text(message, size=16, color=skin.INK_2))
            return

        cells = self._cells(payload)
        now = time.time()

        self._header.content = self._header_card(payload)
        self._personal.content = self._personal_card(payload)
        self._overview.content = self._overview_card(payload)
        self._trend_box.content = self._trend_card()
        self._toolbar.content = self._toolbar_card(cells)
        self._grid.content = self._grid_card(cells, now)
        self._admin.content = self._admin_card(payload)

    @staticmethod
    def _cells(payload: dict[str, Any]) -> list[ParticipantState]:
        """把 ``payload["grid"]`` 还原成信封对象。

        走 ``ParticipantState.from_dict`` 而不是自己读 dict：线路格式的解析规则
        （哪些字段必需、``state`` 为 ``None`` 的三种含义）属于契约层，不该在这里重写。
        """
        raw = payload.get("grid")
        if not isinstance(raw, list):
            return []
        cells: list[ParticipantState] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                cells.append(ParticipantState.from_dict(entry))
            except ValueError:
                continue
        return cells

    # ── 演示控制条（明确不是产品功能）──────────────────────────────────

    def _demo_bar_card(self) -> ft.Control:
        if len(self._viewers) < 2:
            return ft.Container()

        pills = [
            self._pill(
                f"{viewer} · 发起人" if viewer == self._viewers[0] else viewer,
                selected=viewer == self._viewer_id,
                on_click=lambda v=viewer: self.switch_viewer(v),
            )
            for viewer in self._viewers
        ]
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            _tag("演示控制", fg=skin.UNWIRED_TAG_FG, bg=skin.UNWIRED_TAG_BG),
                            _text(
                                "切换观看者以核对「同一套页面」——差别只该出现在"
                                "「管理台可不可见」上。这不是产品功能。",
                                size=11,
                                color=skin.MICRO,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(pills, spacing=6, wrap=True, run_spacing=6),
                ],
                spacing=8,
            ),
            bgcolor=skin.PANEL_BG,
            border=ft.border.all(1, skin.HAIRLINE),
            border_radius=skin.RADIUS_MD,
            padding=12,
        )

    # ── 房间头部 ────────────────────────────────────────────────────────

    def _header_card(self, payload: dict[str, Any]) -> ft.Control:
        room = payload.get("room") or {}
        name = str(room.get("name") or "")
        topic = str(room.get("topic") or "")
        code = str(room.get("invite_code") or "")
        policy = str(room.get("publish_policy") or "")
        forced = policy == POLICY_MANDATORY

        left = ft.Column(
            [
                _text(name or "未命名房间", size=24, color=skin.INK_HI, weight=ft.FontWeight.BOLD),
                _text(
                    topic or "未填写学习主题",
                    size=13,
                    color=skin.INK_2 if topic else skin.MICRO,
                ),
            ],
            spacing=2,
            expand=True,
        )

        invite: ft.Control = (
            ft.Row(
                [
                    _text("邀请码", size=11, color=skin.MICRO),
                    _text(code, size=15, color=skin.INK_HI, weight=ft.FontWeight.BOLD),
                    ft.IconButton(
                        icon=ft.Icons.COPY,
                        icon_size=16,
                        icon_color=skin.BRAND,
                        tooltip="复制邀请码",
                        on_click=lambda _e: self._copy(code),
                    ),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            if code
            else _text("邀请码未生成", size=12, color=skin.MICRO)
        )

        right = ft.Column(
            [
                ft.Container(
                    content=invite,
                    bgcolor=skin.PANEL_BG,
                    border_radius=skin.RADIUS_SM,
                    padding=ft.padding.symmetric(4, 10),
                    border=ft.border.all(1, skin.HAIRLINE),
                ),
                _text(
                    "已连接 · 每 1.2 秒刷新" if self._error is None else f"离线（{self._error}）",
                    size=11,
                    color=skin.MICRO if self._error is None else skin.WARN_TAG_FG,
                ),
            ],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.END,
        )

        stats = ft.Row(
            [
                _stat(
                    "房间总人数",
                    str(room.get("total_count", 0)),
                    hint="含发起人、含未开启感知者、含还没出结果的人。",
                ),
                _stat(
                    "已公开状态",
                    str(room.get("published_count", 0)),
                    color=skin.BRAND,
                    hint="公共概览的分母 —— 只有这些人的状态进了聚合。",
                ),
                _stat(
                    "未公开",
                    str(room.get("hidden_count", 0)),
                    hint="开着感知，但选择不向房间公开。",
                ),
                _stat(
                    "待判定",
                    str(room.get("pending_count", 0)),
                    hint="本帧还没形成判定（刚进房间，或信号不足）。含不产出状态的发起人。",
                ),
                _stat(
                    "未开启感知",
                    str(room.get("closed_count", 0)),
                    hint="主动关掉采集的人 —— 不是「没测出来」，是「被要求不测」。",
                ),
                ft.Container(
                    content=ft.Column(
                        [
                            _text("当前公开规则", size=11, color=skin.MICRO),
                            _tag(
                                POLICY_TEXT.get(policy, policy or "未知"),
                                fg=skin.WARN_TAG_FG if forced else skin.OK_TAG_FG,
                                bg=skin.WARN_TAG_BG if forced else skin.OK_TAG_BG,
                            ),
                        ],
                        spacing=2,
                    ),
                    padding=ft.padding.symmetric(6, 12),
                    border_radius=skin.RADIUS_SM,
                    bgcolor=skin.CARD_BG,
                ),
            ],
            wrap=True,
            spacing=8,
            run_spacing=8,
        )

        return _card(ft.Column([ft.Row([left, right], spacing=16), _divider(), stats], spacing=14))

    def _copy(self, value: str) -> None:
        try:
            self.page.set_clipboard(value)
        except Exception:
            return
        self.page.open(ft.SnackBar(ft.Text(f"已复制邀请码 {value}"), bgcolor=skin.INK_HI))

    # ── 我的状态 ────────────────────────────────────────────────────────

    def _personal_card(self, payload: dict[str, Any]) -> ft.Control:
        me = payload.get("me")
        room = payload.get("room") or {}
        forced = str(room.get("publish_policy") or "") == POLICY_MANDATORY
        viewer = str(payload.get("viewer") or "")
        initiator = bool(payload.get("initiator"))

        blocks: list[ft.Control] = [
            _section_title(
                "我的状态",
                f"编号 {viewer} · " + ("房间发起人" if initiator else "普通参与者"),
            )
        ]

        if not isinstance(me, dict):
            blocks.append(_text("本机尚未登记", size=13, color=skin.MICRO))
            return _card(ft.Column(blocks, spacing=14))

        state = me.get("state")
        if me.get("closed"):
            blocks.append(
                self._state_banner(
                    "已关闭感知", "采集已停止，房间看不到你的任何状态。", CLOSED_COLOR
                )
            )
        elif isinstance(state, dict):
            blocks.append(self._state_banner_from_wire(state))
        elif initiator:
            # 发起人**按设计**不产出状态（``build_demo_hub`` 里它是无状态登记的），
            # 所以这里不能说「等待第一帧」—— 那句话会把「不产出」说成「还没出来」，
            # 用户会一直等一个永远不会到的结果。
            blocks.append(
                self._state_banner(
                    "发起人不产出状态",
                    "发起人的角色是「定规则、看全局」——它自己不进感知链路，"
                    "因此不占宫格，也没有状态可公开。",
                    skin.MICRO,
                )
            )
        else:
            blocks.append(
                self._state_banner(
                    "等待第一帧",
                    "本帧还没形成判定 —— 融合层对信号不足的输入会拒判，这是设计行为。",
                    skin.MICRO,
                )
            )

        blocks.append(_divider())
        blocks.append(self._publish_switch(me, forced, stateless=initiator and state is None))
        blocks.append(self._perception_switch())
        return _card(ft.Column(blocks, spacing=14))

    def _state_banner(self, text: str, hint: str, color: str) -> ft.Control:
        return ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            _text(SHAPE_GLYPHS["flat"], size=22, color=color),
                            _text(text, size=22, color=color, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.symmetric(14, 16),
                    border_radius=skin.RADIUS_MD,
                    bgcolor=skin.PANEL_BG,
                ),
                _text(hint, size=11, color=skin.MICRO),
            ],
            spacing=6,
        )

    def _state_banner_from_wire(self, state: dict[str, Any]) -> ft.Control:
        label = str(state.get("label") or "")
        ink = color_for_key(label)
        fill = fill_for_key(label)
        glyph = SHAPE_GLYPHS.get(shape_for_key(label), "─")
        confidence = float(state.get("confidence") or 0.0)
        stale = bool(state.get("stale"))

        banner = ft.Container(
            content=ft.Row(
                [
                    _text(glyph, size=22, color=on_fill(fill)),
                    _text(
                        _label_text(label),
                        size=22,
                        color=on_fill(fill),
                        weight=ft.FontWeight.BOLD,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(14, 16),
            border_radius=skin.RADIUS_MD,
            bgcolor=fill,
            border=ft.border.all(2, ink),
        )

        rows: list[ft.Control] = [
            banner,
            ft.Row(
                [
                    _text("置信度", size=11, color=skin.MICRO),
                    ft.ProgressBar(
                        value=confidence,
                        bar_height=6,
                        color=ink,
                        bgcolor=skin.PANEL_BG,
                        border_radius=skin.RADIUS_FULL,
                        expand=True,
                    ),
                    _text(f"{confidence:.0%}", size=12, color=skin.INK_2),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ]
        if stale:
            rows.append(
                _text(
                    "维持中 —— 契约要求：stale 时不得当成新结果展示。",
                    size=11,
                    color=skin.WARN_TAG_FG,
                )
            )
        return ft.Column(rows, spacing=8)

    def _publish_switch(
        self, me: dict[str, Any], forced: bool, *, stateless: bool = False
    ) -> ft.Control:
        """个人公开开关 —— 本页**唯一**真正接通的可操作项（``POST /hidden``）。

        ``stateless`` 用于发起人：它不产出状态，所以这个开关当前不会改变任何东西。
        这时**不隐藏开关**（需求文档 §模块二.1 要求所有人看到同一套页面），
        而是在下面加一句说明 —— 让一个开关看起来有用、实际没用，比少一个开关更糟。
        """
        hidden = bool(me.get("hidden"))
        rows: list[ft.Control] = [
            ft.Row(
                [
                    ft.Column(
                        [
                            _text(
                                "向房间公开我的状态",
                                size=13,
                                color=skin.INK if not stateless else skin.UNWIRED_FG,
                            ),
                            _text(
                                "关闭后房间里的其他人只看到「已隐藏」；你自己仍然看得见。",
                                size=11,
                                color=skin.MICRO,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Switch(
                        value=not hidden,
                        active_color=skin.BRAND,
                        inactive_track_color=skin.HAIRLINE_STRONG,
                        disabled=forced,
                        on_change=lambda e: self._set_hidden(not bool(e.control.value)),
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Row(
                [
                    _tag("已接通", fg=skin.OK_TAG_FG, bg=skin.OK_TAG_BG),
                    *(
                        [
                            _tag(
                                f"受规则约束：{POLICY_TEXT[POLICY_MANDATORY]}",
                                fg=skin.WARN_TAG_FG,
                                bg=skin.WARN_TAG_BG,
                            )
                        ]
                        if forced
                        else []
                    ),
                    *(
                        [_tag("当前无作用", fg=skin.UNWIRED_TAG_FG, bg=skin.UNWIRED_TAG_BG)]
                        if stateless
                        else []
                    ),
                ],
                spacing=6,
                wrap=True,
            ),
        ]
        if stateless:
            rows.append(
                _text(
                    "发起人不产出状态，所以这个开关当前不会改变任何东西 ——"
                    "它仍然在，是为了让所有人的页面长得一样（§模块二.1）。",
                    size=11,
                    color=skin.MICRO,
                )
            )
        if forced:
            rows.append(
                _text(
                    "当前房间规则是「全员强制公开」，个人开关被规则覆盖 ——"
                    "这是需求文档 §模块三.2 给发起人的规则层杠杆，不是绕过隐私的后门。",
                    size=11,
                    color=skin.WARN_TAG_FG,
                )
            )
        return ft.Column(rows, spacing=6)

    def _perception_switch(self) -> ft.Control:
        """感知开关 —— 明确留「未接入」态，不做成点了没反应的控件。"""
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Column(
                            [
                                _text("开启感知采集", size=13, color=skin.UNWIRED_FG),
                                _text(
                                    "关掉之后，本机将不再产出任何状态。",
                                    size=11,
                                    color=skin.MICRO,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.Switch(value=True, disabled=True),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                _tag("未接入", fg=skin.UNWIRED_TAG_FG, bg=skin.UNWIRED_TAG_BG),
                _text(
                    "缺的是摄像头采集链路本身 —— 演示环境的状态由模拟帧驱动，"
                    "没有一个「本地采集端」可供开关。",
                    size=11,
                    color=skin.MICRO,
                ),
            ],
            spacing=6,
        )

    # ── 房间状态概览 ────────────────────────────────────────────────────

    def _overview_card(self, payload: dict[str, Any]) -> ft.Control:
        summary = Summary.from_dict(payload.get("summary") or {})
        return _card(
            ft.Column(
                [
                    _section_title(
                        "房间状态概览",
                        "分母是「已公开状态」的人数 —— 未公开的人不进这个统计，"
                        "否则公开开关就是摆设。",
                    ),
                    ft.Row(
                        [_kpi_tile(card) for card in kpi_cards(summary)],
                        wrap=True,
                        spacing=10,
                        run_spacing=10,
                    ),
                ],
                spacing=14,
            )
        )

    # ── 房间趋势 ────────────────────────────────────────────────────────

    def _trend_card(self) -> ft.Control:
        samples = self._trend.samples()
        keys = [*LABEL_ORDER, CLOSED_KEY]
        values = {key: self._trend.series(key) for key in keys}
        peak = max((max(series) for series in values.values() if series), default=0)

        series = [
            ft.LineChartData(
                data_points=[
                    ft.LineChartDataPoint(index, value) for index, value in enumerate(values[key])
                ],
                color=_series_color(key),
                stroke_width=2,
                curved=True,
                point=False,
            )
            for key in keys
            if any(values[key])
        ]

        chart: ft.Control = (
            ft.LineChart(
                data_series=series,
                height=140,
                min_y=0,
                max_y=max(4, peak + 1),
                min_x=0,
                max_x=max(1, len(samples) - 1),
                horizontal_grid_lines=ft.ChartGridLines(color=skin.HAIRLINE, width=1, interval=1),
                left_axis=ft.ChartAxis(
                    labels=[ft.ChartAxisLabel(value=0, label=_text("0", size=9, color=skin.MICRO))],
                    labels_size=24,
                ),
                tooltip_bgcolor=skin.INK_HI,
            )
            if series
            else ft.Container(
                content=_text("正在累积采样…", size=12, color=skin.MICRO),
                height=120,
                alignment=ft.alignment.center,
            )
        )

        legend = [
            _legend_dot(_series_color(key), _label_text(key)) for key in keys if any(values[key])
        ]
        return _card(
            ft.Column(
                [
                    _section_title(
                        "房间状态趋势",
                        f"本页内存累积 {len(samples)}/{TREND_WINDOW} 帧"
                        f"（约 {len(samples) * POLL_SECONDS:.0f} 秒）· 刷新页面即清零 —— "
                        "历史数据落盘未接入（§模块三.4）",
                    ),
                    chart,
                    ft.Row(legend, spacing=12, wrap=True),
                ],
                spacing=10,
            )
        )

    # ── 工具条：筛选 + 排序 ─────────────────────────────────────────────

    def _toolbar_card(self, cells: list[ParticipantState]) -> ft.Control:
        counts = filter_counts(cells)

        chips = [
            self._pill(
                f"{option.text} {counts.get(option.key, 0)}",
                tooltip=option.hint,
                selected=option.key == self._filter_key,
                on_click=lambda key=option.key: self._set_filter(key),
            )
            for option in filter_options()
        ]
        sorts = [
            self._pill(
                option.text,
                tooltip=option.hint,
                selected=option.key == self._sort_key,
                on_click=lambda key=option.key: self._set_sort(key),
            )
            for option in sort_options()
        ]

        return _card(
            ft.Column(
                [
                    ft.Row(
                        [
                            _text("筛选", size=12, color=skin.INK_2, weight=ft.FontWeight.BOLD),
                            *chips,
                        ],
                        spacing=6,
                        wrap=True,
                        run_spacing=6,
                    ),
                    ft.Row(
                        [
                            _text("排序", size=12, color=skin.INK_2, weight=ft.FontWeight.BOLD),
                            *sorts,
                        ],
                        spacing=6,
                        wrap=True,
                        run_spacing=6,
                    ),
                ],
                spacing=10,
            ),
            padding=14,
        )

    def _pill(
        self,
        text: str,
        *,
        tooltip: str = "",
        selected: bool = False,
        on_click: Callable[..., Any] | None = None,
    ) -> ft.Container:
        """自绘胶囊。

        不用 ``ft.Chip`` 是因为它的选中态配色不可控 —— 而这里的底色要参与可达性
        验算（``tests/app/present/test_skin.py`` 把品牌蓝与白字的组合钉住了）。
        """
        return ft.Container(
            content=_text(
                text,
                size=12,
                color=skin.CARD_BG if selected else skin.INK_2,
                weight=ft.FontWeight.BOLD if selected else None,
            ),
            bgcolor=skin.BRAND if selected else skin.PANEL_BG,
            border=ft.border.all(1, skin.BRAND if selected else skin.HAIRLINE),
            border_radius=skin.RADIUS_FULL,
            padding=ft.padding.symmetric(5, 12),
            on_click=on_click,
            ink=True,
            tooltip=tooltip or None,
        )

    # ── 参与者宫格 ──────────────────────────────────────────────────────

    def _grid_card(self, cells: list[ParticipantState], now: float) -> ft.Control:
        rows = sort_rows(
            roster_rows(cells, now, label_filter=self._filter_key),
            self._sort_key,
        )
        geometry = card_geometry(scale_for_width(self.page.width or DEFAULT_WIDTH))

        if rows:
            body: ft.Control = ft.Row(
                [self._cell(row, geometry.width, geometry.height) for row in rows],
                wrap=True,
                spacing=geometry.gap,
                run_spacing=geometry.gap,
            )
        else:
            body = ft.Container(
                content=_text(
                    "这一档当前没有人。" if self._filter_key != BUCKET_ALL else "房间里还没有人。",
                    size=13,
                    color=skin.MICRO,
                ),
                padding=ft.padding.symmetric(24, 0),
                alignment=ft.alignment.center,
            )

        total = filter_counts(cells).get(BUCKET_ALL, 0)
        return _card(
            ft.Column(
                [
                    _section_title(
                        "参与者",
                        f"宫格里 {len(rows)} / {total} 格 · 每个人都是真的过了一遍融合与平滑，"
                        "不是随机涂色",
                    ),
                    body,
                ],
                spacing=14,
            )
        )

    def _cell(self, row: RosterRow, width: int, height: int) -> ft.Control:
        """一个宫格格子。

        颜色、文案、形状全部来自 present 层（``RosterRow`` + ``color_for_key`` +
        ``shape_for_key``）；这里只决定「怎么摆」。
        """
        if row.closed:
            bg, fg, border = skin.UNWIRED_BG, CLOSED_COLOR, skin.HAIRLINE
        elif row.masked:
            bg, fg, border = skin.HIDDEN_BG, skin.HIDDEN_FG, skin.HAIRLINE
        elif row.confidence is None:
            bg, fg, border = skin.CARD_BG, skin.MICRO, skin.HAIRLINE
        else:
            bg, fg, border = skin.CARD_BG, row.color, skin.HAIRLINE

        dim = row.closed or row.masked
        glyph = "─" if dim else SHAPE_GLYPHS.get(shape_for_key(row.label_key), "─")
        note = "维持中" if (row.freshness == "stale" and not dim) else ""

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            _text(row.participant_id, size=11, color=skin.MICRO),
                            _text(note, size=9, color=skin.MICRO),
                        ],
                        spacing=4,
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        [
                            _text(glyph, size=15, color=fg),
                            _text(row.cell_text, size=12, color=fg, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=4,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            width=width,
            height=height,
            bgcolor=bg,
            border=ft.border.all(1, border),
            border_radius=skin.RADIUS_SM,
            padding=ft.padding.symmetric(8, 8),
            tooltip=self._cell_tooltip(row),
        )

    @staticmethod
    def _cell_tooltip(row: RosterRow) -> str:
        if row.closed:
            return f"{row.participant_id} · 已关闭感知（主动停止采集）"
        if row.masked:
            return f"{row.participant_id} · 未公开（本人选择不对房间公开）"
        if row.confidence is None:
            return f"{row.participant_id} · 本帧未形成判定"
        tail = "，维持中" if row.freshness == "stale" else ""
        return f"{row.participant_id} · {row.label_text} · 置信度 {row.confidence:.0%}{tail}"

    # ── 发起人管理台 ────────────────────────────────────────────────────

    def _admin_card(self, payload: dict[str, Any]) -> ft.Control:
        initiator = bool(payload.get("initiator"))
        room = payload.get("room") or {}

        blocks: list[ft.Control] = [
            _section_title(
                "管理台" if initiator else "隐私与规则",
                "你是这间房间的发起人 —— 下面这些开关真的会生效。"
                if initiator
                else "你不是发起人，因此看不到管理功能；这些规则同样约束着发起人。",
            )
        ]
        for section in console.visible_sections(initiator=initiator):
            blocks.append(self._admin_section(section, room, initiator))

        if initiator:
            blocks.append(self._unwired_summary())

        return _card(ft.Column(blocks, spacing=16))

    def _unwired_summary(self) -> ft.Control:
        count = len(console.unwired_items())
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            _text("还有", size=12, color=skin.UNWIRED_FG),
                            _text(
                                str(count),
                                size=14,
                                color=skin.UNWIRED_TAG_FG,
                                weight=ft.FontWeight.BOLD,
                            ),
                            _text("项需求尚未接入", size=12, color=skin.UNWIRED_FG),
                        ],
                        spacing=4,
                    ),
                    _text(
                        "它们都标了「未接入」和缺的是什么，没有做成点了没反应的开关，"
                        "也没有填任何假值。",
                        size=11,
                        color=skin.MICRO,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=skin.UNWIRED_BG,
            border=ft.border.all(1, skin.UNWIRED_BORDER),
            border_radius=skin.RADIUS_SM,
            padding=12,
        )

    def _admin_section(
        self, section: ConsoleSection, room: dict[str, Any], initiator: bool
    ) -> ft.Control:
        return ft.Column(
            [
                _section_title(section.title, section.hint),
                *[self._admin_item(entry, room, initiator) for entry in section.items],
            ],
            spacing=10,
        )

    def _admin_item(self, entry: ConsoleItem, room: dict[str, Any], initiator: bool) -> ft.Control:
        tag_factory: dict[str, Callable[[], ft.Control]] = {
            console.KIND_WIRED: lambda: _tag(entry.kind_text, fg=skin.OK_TAG_FG, bg=skin.OK_TAG_BG),
            console.KIND_READONLY: lambda: _tag(
                entry.kind_text, fg=skin.RO_TAG_FG, bg=skin.RO_TAG_BG
            ),
            console.KIND_FACT: lambda: _tag(entry.kind_text, fg=skin.OK_TAG_FG, bg=skin.OK_TAG_BG),
            console.KIND_UNWIRED: lambda: _tag(
                entry.kind_text, fg=skin.UNWIRED_TAG_FG, bg=skin.UNWIRED_TAG_BG
            ),
        }

        head = ft.Row(
            [
                _text(entry.title, size=13, color=skin.INK, weight=ft.FontWeight.BOLD),
                tag_factory[entry.kind](),
                *([_text(entry.doc_ref, size=10, color=skin.MICRO)] if entry.doc_ref else []),
            ],
            spacing=6,
            wrap=True,
            run_spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        inner: list[ft.Control] = [head]
        if entry.kind == console.KIND_WIRED:
            inner.append(self._wired_control(entry.key, room, initiator))
        elif entry.kind == console.KIND_READONLY:
            inner.append(self._readonly_value(entry.key, room))
        inner.append(_text(entry.hint, size=11, color=skin.MICRO))

        unwired = entry.kind == console.KIND_UNWIRED
        return ft.Container(
            content=ft.Column(inner, spacing=6),
            padding=10,
            border_radius=skin.RADIUS_SM,
            bgcolor=skin.UNWIRED_BG if unwired else None,
            border=ft.border.all(1, skin.UNWIRED_BORDER) if unwired else None,
        )

    def _wired_control(self, key: str, room: dict[str, Any], initiator: bool) -> ft.Control:
        if key != "room.publish_policy":
            # 契约上不该走到这里（``CONSOLE_SECTIONS`` 里只有一项 wired）。
            # 留一条显式分支而不是静默画个空壳，否则将来新增 wired 项忘了实现
            # 会表现为「点不动的按钮」，比一句直白的提示难查得多。
            return _text("（未实现的可操作项）", size=11, color=skin.MICRO)

        current = str(room.get("publish_policy") or "")
        return ft.Row(
            [
                self._pill(
                    POLICY_TEXT[policy],
                    selected=policy == current,
                    on_click=(lambda p=policy: self._set_policy(p)) if initiator else None,
                )
                for policy in (POLICY_OPTIONAL, POLICY_MANDATORY)
            ],
            spacing=6,
            wrap=True,
            run_spacing=6,
        )

    @staticmethod
    def _readonly_value(key: str, room: dict[str, Any]) -> ft.Control:
        mapping = {
            "room.name": room.get("name") or "未命名",
            "room.topic": room.get("topic") or "未填写",
            "room.invite_code": room.get("invite_code") or "未生成",
        }
        if key not in mapping:
            return _text("（无真值可展示）", size=11, color=skin.MICRO)
        return ft.Container(
            content=_text(str(mapping[key]), size=13, color=skin.INK_2, selectable=True),
            bgcolor=skin.PANEL_BG,
            border_radius=skin.RADIUS_XS,
            padding=ft.padding.symmetric(4, 8),
        )

    # ── 交互回调 ────────────────────────────────────────────────────────

    def _set_filter(self, key: str) -> None:
        self._filter_key = normalize_filter(key)
        self._refresh()

    def _set_sort(self, key: str) -> None:
        self._sort_key = normalize_sort(key)
        self._refresh()

    def _write_and_refresh(self, write: Callable[[], None], what: str) -> None:
        """写服务端 → **立刻拉一帧** → 重画。

        为什么写完要马上拉：可见性裁剪在服务端，写回后本地的 payload 还是旧的，
        直接重画会让开关「弹回去」直到下一次轮询（最多 1.2 秒）。本机 HTTP 往返
        是毫秒级，同步拉一帧既没有可感知的卡顿，又让开关看起来是即时生效的。
        """
        try:
            write()
            self._payload = self._client.snapshot()
        except Exception as exc:
            self.page.open(
                ft.SnackBar(
                    ft.Text(f"没能写回{what}（{type(exc).__name__}）"),
                    bgcolor=skin.WARN_TAG_FG,
                )
            )
            return
        self._refresh()

    def _set_hidden(self, hidden: bool) -> None:
        """把公开开关写回服务端（§模块二.2）。

        必须回写：可见性裁剪在**服务端**，客户端自己改一个标志是没用的。
        写失败时给一条提示而不是静默 —— 否则用户会以为开关坏了。
        """
        viewer = str((self._payload or {}).get("viewer") or self._viewer_id)
        self._write_and_refresh(lambda: self._client.set_hidden(viewer, hidden), "公开开关")

    def _set_policy(self, policy: str) -> None:
        self._write_and_refresh(lambda: self._client.set_publish_policy(policy), "公开规则")

    def switch_viewer(self, viewer_id: str) -> None:
        """切换观看者（**演示用**，不是产品功能）。

        需求文档要的是「所有人共用同一个页面」，所以核对时最直接的办法就是
        同一套页面换个观看者看一遍 —— 差别只该出现在「管理台可不可见」上。
        """
        if viewer_id == self._viewer_id:
            return
        self._viewer_id = viewer_id
        self._client = HttpSnapshot(self._base_url, viewer_id)
        with self._lock:
            self._trend.clear()
            self._payload = None
        self._refresh()


def _kpi_tile(card: KpiCard) -> ft.Control:
    """一张 KPI 卡：色点 + 名称 + 人数 + 占比 + 占比条。"""
    color = DIM_COLOR if card.dim else card.color
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Container(width=10, height=10, bgcolor=color, border_radius=5),
                        _text(card.label, size=12, color=skin.INK_2),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                _text(str(card.count), size=26, color=color, weight=ft.FontWeight.BOLD),
                ft.ProgressBar(
                    value=card.ratio,
                    bar_height=5,
                    color=color,
                    bgcolor=skin.PANEL_BG,
                    border_radius=skin.RADIUS_FULL,
                ),
                _text(f"{card.ratio:.0%}", size=11, color=skin.MICRO),
            ],
            spacing=4,
        ),
        width=118,
        padding=12,
        border_radius=skin.RADIUS_MD,
        bgcolor=skin.CARD_BG,
        border=ft.border.all(1, skin.HAIRLINE),
        tooltip=f"{card.label}：{card.count} 人，占已公开状态的 {card.ratio:.0%}",
    )
