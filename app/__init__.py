"""应用层：多端展示与分发（C 主责）。

分层（设计稿 §03）
------------------

- :mod:`app.envelope` —— 参与者信封：给单机 ``FinalState`` 补上会话层的身份与可见性；
- :mod:`app.present`  —— 展示逻辑（**纯函数**，headless CI 可满跑，覆盖率的绝对主力）；
- :mod:`app.hub`      —— 在线表 + 按观看者裁剪 payload + 只读 HTTP 端点；
- ``app/client/``     —— 桌面客户端外壳（Tkinter；headless CI 执行不到，整体 omit）。

分层动机来自 CI 约束、不是审美：``pyproject.toml`` 的覆盖率 ``source`` 已含 ``app``
且门槛 80%，而 CI 无显示环境 —— 所以「能测的规则」全部下沉到 :mod:`app.present`。

``common/`` 与 ``fusion/`` **不反向依赖本包**，因此删掉 ``app/`` 下的任何东西
都不会伤到已实现的部分（这正是回滚成本低的原因）。
"""
