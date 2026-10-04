"""应用层：多端展示与分发（C 主责）。

分层（设计稿 §03）
------------------

- :mod:`app.envelope` —— 参与者信封：给单机 ``FinalState`` 补上会话层的身份与可见性；
- :mod:`app.present`  —— 展示逻辑（**纯函数**，headless CI 可满跑，覆盖率的绝对主力）；
- :mod:`app.hub`      —— 在线表 + 按观看者裁剪 payload + 只读 HTTP 端点；
- :mod:`app.room`     —— 房间元数据与**公开规则**（全员强制公开 / 个人自主选择）；
- :mod:`app.web`      —— **产品前端**（Flet 统一单页；CI 不装 ``flet``，整体 omit）；
- :mod:`app.demo`     —— 演示环境与 HTTP 客户端（与界面框架无关，前端与集成共用）；
- :mod:`app.integration` —— 串行集成主流程 + CLI 入口。

分层动机来自 CI 约束、不是审美：``pyproject.toml`` 的覆盖率 ``source`` 已含 ``app``
且门槛 80%，而 CI 既不装 ``flet`` 也无显示环境 —— 所以「能测的规则」全部下沉到
:mod:`app.present`。

``common/`` 与 ``fusion/`` **不反向依赖本包**，因此删掉 ``app/`` 下的任何东西
都不会伤到已实现的部分（这正是回滚成本低的原因）。
2026-10-04 删掉 Tk 前端时这条判断被验证了一次：``common/`` 与 ``fusion/`` 一行未动。
"""
