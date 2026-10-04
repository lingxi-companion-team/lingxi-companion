"""``python -m app.client`` —— **Tk 调试外壳**入口（不是产品前端）。

三种用法
--------

.. code-block:: console

    # 1) 一键演示：本机起一个 hub（含假数据驱动），直接打开窗口
    python -m app.client --demo --view dashboard
    python -m app.client --demo --view grid --viewer s03

    # 2) 连一个已经在跑的 hub（例如另一台机器上的）
    python -m app.client --url http://127.0.0.1:8765 --view grid --viewer s03

    # 3) 冒烟自检：构建 + 绘制一轮后立刻退出（无交互，适合脚本化验证）
    python -m app.client --demo --selftest --json

⚠️ **本入口不是产品界面**（2026-10-02 拍板）
------------------------------------------
需求文档《灵犀学伴产品需求方案》§二.1 要求「**无教师/学生端的产品差异**」。
**产品前端是 ``python -m app.web``（Flet 统一单页）** —— 所有观看者同一套页面，
差别只在「管理台可不可见」（由 payload 的 ``initiator`` 决定）。

本入口保留的唯一理由是**零第三方依赖**（纯标准库）：在装不上 ``flet`` 的环境
（离线机器、无显示环境）里，仍能肉眼看一眼链路是不是活的。因此：

* ``--view`` 是**调试视图**（``dashboard`` 三栏监管视图 / ``grid`` 共享宫格视图），
  **不是产品角色** —— 与线路上的 ``ParticipantState.role`` 是两回事；
* 本模块**冻结**：新功能一律加在 :mod:`app.web`，这里只修缺陷。

演示环境与 HTTP 客户端已抽到 :mod:`app.demo`（与 GUI 框架无关），两个前端共用。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import tkinter as tk
from collections.abc import Mapping, Sequence
from typing import Any

from app.client.dpi import enable_dpi_awareness, lock_tk_scaling
from app.client.window import VIEW_DASHBOARD, VIEWS, ClientWindow
from app.demo import (
    DEFAULT_STUDENT_ID,
    DEFAULT_TEACHER_ID,
    HttpSnapshot,
    build_demo_hub,
)
from app.hub import Hub, make_server

__all__ = ["main", "parse_args"]

DEFAULT_PORT = 8765
DEFAULT_POLL_MS = 1200


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.client",
        description="灵犀学伴 · Tk 调试外壳（**不是产品界面**，产品界面见 python -m app.web）",
        epilog="不给 --url 时默认按 --demo 启动一个本机演示 hub。",
    )
    parser.add_argument("--viewer", default=None, help="自己的 participant_id，默认按调试视图取")
    parser.add_argument(
        "--view",
        choices=list(VIEWS),
        default="grid",
        help="调试视图（不是产品角色）：dashboard=三栏监管视图 / grid=共享宫格视图",
    )
    parser.add_argument("--url", default=None, help="已运行的 hub 地址，如 http://127.0.0.1:8765")
    parser.add_argument("--demo", action="store_true", help="本机起演示 hub 并喂入假数据")
    parser.add_argument("--students", type=int, default=28, help="演示的参与者数")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="演示 hub 端口，0 = 系统分配"
    )
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS, help="轮询间隔（毫秒）")
    parser.add_argument("--selftest", action="store_true", help="构建并绘制一轮后退出")
    parser.add_argument("--json", action="store_true", help="--selftest 时以 JSON 输出统计")
    return parser.parse_args(argv)


def _default_viewer(view: str) -> str:
    """监管视图默认看发起人，共享视图默认看第一个参与者。"""
    return DEFAULT_TEACHER_ID if view == VIEW_DASHBOARD else DEFAULT_STUDENT_ID


def _start_demo(args: argparse.Namespace) -> tuple[Hub, Any, str]:
    hub, driver = build_demo_hub(args.students)
    driver.start()
    server = make_server(hub, port=args.port)
    threading.Thread(target=server.serve_forever, name="hub-server", daemon=True).start()
    return hub, driver, f"http://127.0.0.1:{server.server_address[1]}"


def _report(stats: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
        return
    for key, value in stats.items():
        print(f"{key}: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    viewer_id = args.viewer or _default_viewer(args.view)

    # ⚠️ 必须在 ``tk.Tk()`` **之前**声明 DPI 感知：进程默认是 DPI-unaware 的，
    # 150% 缩放的屏幕上 Windows 会把整个窗口位图放大 1.5× —— 所有文字线条一起变糊。
    # 见 ``app.client.dpi`` 的模块说明（含实测数据）。
    dpi_result = enable_dpi_awareness()

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法初始化图形界面（{exc}）—— 本入口需要桌面环境。", file=sys.stderr)
        return 2
    # 声明感知后 Tk 会按真实 DPI 放大字号，而本项目几何常量是**像素**（卡片 98×80…），
    # 所以立刻把 ``tk scaling`` 锁回 96 DPI 的比值，让「字号 ↔ 像素」比例保持原样。
    lock_tk_scaling(root)
    root.withdraw()

    hub: Hub | None = None
    driver: Any = None
    server = None
    use_demo = args.demo or not args.url

    try:
        if use_demo:
            hub, driver, base_url = _start_demo(args)
            known = {participant.participant_id for participant in hub.participants}
            if viewer_id not in known:
                print(
                    f"演示 hub 里没有 {viewer_id!r}；可用 id：{', '.join(sorted(known))}",
                    file=sys.stderr,
                )
                return 2
        else:
            base_url = str(args.url)

        source = HttpSnapshot(base_url, viewer_id)
        print(f"已连接 {base_url}（viewer={viewer_id}, view={args.view}）")
        print(f"DPI 感知: {dpi_result}")

        window = ClientWindow(
            root,
            source.snapshot,
            viewer_id=viewer_id,
            view=args.view,
            poll_ms=args.poll_ms,
            hidden_reporter=source.set_hidden,
        )

        if args.selftest:
            stats = window.selftest()
            _report(stats, as_json=args.json)
            # 冒烟判定：宫格必须真的画出了东西，否则这次自检毫无意义。
            return 0 if stats.get("grid_cells") else 1

        root.mainloop()
        return 0
    finally:
        if driver is not None:
            driver.stop()
        if server is not None:
            server.shutdown()
            server.server_close()
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
