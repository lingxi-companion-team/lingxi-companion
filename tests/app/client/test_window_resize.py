"""``app.client.window`` 的**无显示器**回归测试。

为什么这里只测 ``_on_resize`` 的**派发过滤**，不测整窗
------------------------------------------------------
``app/client/`` 整包被覆盖率 omit，理由是 CI 无显示环境、建 ``tk.Tk()`` 必失败。
但「事件该不该被处理」这件事是**纯判断**，与有没有窗口无关 —— 把它抽出来单独
守住，成本几乎为零，挡住的却是本项目最贵的一次缺陷：

一次真实崩溃
------------
Step3 给主窗口挂了 ``<Configure>`` → ``_on_resize``，想「窗口跨档时重排宫格」。
``--selftest`` 全绿、541 项测试全绿 —— 但窗口一打开就**原生崩溃**
（Windows ``0xC0000005`` 访问违例，进程直接死，Python 层连 traceback 都没有）。

原因是 Tk 的 ``<Configure>`` **不只属于主窗口**：子控件每次改尺寸/位置也会发，
bindtag 会把这些事件一并送进回调。于是「重排宫格 = 销毁并重建所有格子」这件事
发生在了**事件派发栈内部** —— Tk 在派发过程中踩到自己刚释放的控件，硬崩。

``_on_resize`` 因此加了两道闸：``event.widget is not self.win`` 直接 return
（只认主窗口的 Configure），以及跨档判断（``scale`` 没变就不重排）。
下面是这两道闸的机器化护栏。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.client.window import ClientWindow
from app.present import SCALE_COMPACT, SCALE_ROOMY, SCALE_STANDARD, scale_for_width

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class _FakeWidget:
    """够用的 tk widget 替身 —— 只用来区分「谁是事件源」。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<widget {self.name}>"


class _FakeEvent:
    def __init__(self, widget: Any, width: int = 800) -> None:
        self.widget = widget
        self.width = width


def _bare_window() -> ClientWindow:
    """不调 ``__init__`` 造一个只带 `_on_resize` 所需字段的实例。

    刻意绕开构造：``__init__`` 要 ``tk.Toplevel``（CI 里必失败），而这里要测的
    判断**不依赖任何真控件**。
    """
    win = ClientWindow.__new__(ClientWindow)
    win.win = _FakeWidget("main")  # type: ignore[assignment]
    win._minimized = False
    win._last_scale = SCALE_STANDARD
    win._rendered = 0  # type: ignore[attr-defined]

    def _render_expanded() -> None:
        win._rendered += 1  # type: ignore[attr-defined]

    win._render_expanded = _render_expanded  # type: ignore[method-assign]
    return win


class TestChildConfigureIsIgnored:
    """子控件的 ``<Configure>`` 必须被丢掉 —— 这是那次原生崩溃的直接原因。"""

    def test_child_widget_configure_is_ignored(self) -> None:
        win = _bare_window()
        win._on_resize(_FakeEvent(_FakeWidget("a grid cell"), width=1200))
        assert win._rendered == 0  # type: ignore[attr-defined]

    def test_main_window_configure_is_processed(self) -> None:
        win = _bare_window()
        win._on_resize(_FakeEvent(win.win, width=1200))
        assert win._rendered == 1  # type: ignore[attr-defined]

    def test_many_child_events_never_render(self) -> None:
        # 一次重排会产出几十个子控件事件；不挡的话它们互相触发，就是崩溃现场。
        win = _bare_window()
        for i in range(50):
            win._on_resize(_FakeEvent(_FakeWidget(f"cell{i}"), width=500 + i * 30))
        assert win._rendered == 0  # type: ignore[attr-defined]


class TestCrossBandOnly:
    """同档内的像素级拖动不该重排（每像素都重排会让界面卡死）。"""

    def test_same_band_does_not_render(self) -> None:
        win = _bare_window()
        win._on_resize(_FakeEvent(win.win, width=700))
        win._on_resize(_FakeEvent(win.win, width=701))
        win._on_resize(_FakeEvent(win.win, width=899))
        assert win._rendered == 0  # type: ignore[attr-defined]

    def test_crossing_band_renders_once(self) -> None:
        win = _bare_window()
        win._on_resize(_FakeEvent(win.win, width=1100))  # standard -> roomy
        assert win._rendered == 1  # type: ignore[attr-defined]
        win._on_resize(_FakeEvent(win.win, width=1150))  # 仍在 roomy
        assert win._rendered == 1  # type: ignore[attr-defined]

    def test_band_boundaries_match_present_scale(self) -> None:
        # 护栏与 present.scale 的分档必须一致（阈值是产品决策，只在 present 定义）。
        win = _bare_window()
        win._last_scale = scale_for_width(500)
        assert win._last_scale == SCALE_COMPACT
        win._on_resize(_FakeEvent(win.win, width=950))
        assert win._last_scale == SCALE_ROOMY
        assert win._rendered == 1  # type: ignore[attr-defined]


class TestMinimizedSuppressesResize:
    def test_minimized_ignores_main_window_configure(self) -> None:
        win = _bare_window()
        win._minimized = True
        win._on_resize(_FakeEvent(win.win, width=1200))
        assert win._rendered == 0  # type: ignore[attr-defined]
