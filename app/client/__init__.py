"""桌面客户端（Tkinter 外壳，多端展示 P2）。

本包整体被覆盖率 ``omit`` 掉（``pyproject.toml`` 的 ``[tool.coverage.report] omit``）——
原因是硬性的：CI 跑在**无显示环境**，``import tkinter`` 之后一旦创建 ``Tk()`` 就会失败，
这部分代码在 CI 里根本执行不到。所以「能测的规则」全部在 :mod:`app.present`（纯函数、
有测试），这里只留「读快照 → 画出来」这一层。

文件分工：
- :mod:`app.client.theme`    —— 外观常量（底色 / 字号 / 图标尺寸）；
- :mod:`app.client.bubblewin`—— 悬浮层：气泡卫星窗口 + 右键菜单 + 退出热键；
- :mod:`app.client.window`   —— 主窗口：**一个** ``Toplevel``，展开态 ⇄ 最小化态（D3）；
- :mod:`app.client.__main__` —— 入口：``python -m app.client``。
"""

from __future__ import annotations

from app.client.bubblewin import BubbleWindow, ExitHotkey, build_context_menu
from app.client.window import ClientWindow

__all__ = ["BubbleWindow", "ClientWindow", "ExitHotkey", "build_context_menu"]
