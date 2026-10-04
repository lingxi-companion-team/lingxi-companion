"""v8 统一单页前端（Flet）—— **唯一的产品界面**。

一套页面，不分端
----------------
需求文档（通用网课场景版）要求「所有用户通过同一链接/邀请码进入房间，页面基础布局
完全一致」「管理功能仅对发起人可见」—— 所以本包是**一套**页面，角色只影响
「管理区可不可见」这一个布尔值，不影响页面结构。

（2026-10-04：曾并存一个按角色建两套布局的 Tkinter 前端 ``app/client/``，
与上面这条要求直接冲突，已退役并归档到
``不推送/技术方案/归档/已退役-Tkinter前端/``。本包因此成为唯一入口。）

本包为什么整体 omit 于覆盖率
-----------------------------
``flet`` 不在 ``requirements.txt`` / ``requirements-dev.txt`` 里，CI 装不到它，
所以本包里任何一行在 CI 里都执行不到。所有能测的判定（配色、可见性、筛选、排序、
管理台内容）都已下沉到 :mod:`app.present`；本包只做「读 payload → 调用 present 层 → 画」。
``pyproject.toml`` 的 ``[tool.coverage.run] omit`` 里有对应条目。

⚠️ 这条 omit 是**「测不到」而不是「懒得测」**，有实测反证：``ft.Page()`` 需要
``conn`` / ``session_id`` / ``loop`` 三个参数，**无法无头构造**，因此
:class:`app.web.page.UnifiedPage` 真的进不了 CI。
"""

from __future__ import annotations

__all__: list[str] = []
