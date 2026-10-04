"""v8 统一单页前端（Flet）。

与 :mod:`app.client`（Tk 旧界面）的关系
---------------------------------------
``app/client/`` 是 v7 的三栏仪表盘，**分教师端 / 学生端两套渲染路径**。
需求文档（通用网课场景版）要求「所有用户通过同一链接/邀请码进入房间，页面基础布局
完全一致」「管理功能仅对发起人可见」—— 所以本包是**一套**页面，角色只影响
「管理区可不可见」这一个布尔值，不影响页面结构。

本包为什么整体 omit 于覆盖率
-----------------------------
``flet`` 不在 ``requirements.txt`` / ``requirements-dev.txt`` 里，CI 装不到它，
所以本包里任何一行在 CI 里都执行不到。所有能测的判定（配色、可见性、筛选、排序、
管理台内容）都已下沉到 :mod:`app.present`；本包只做「读 payload → 调用 present 层 → 画」。
``pyproject.toml`` 的 ``[tool.coverage.run] omit`` 里有对应条目。
"""

from __future__ import annotations

__all__: list[str] = []
