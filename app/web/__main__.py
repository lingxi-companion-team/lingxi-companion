"""v8 统一单页前端的入口。

跑起来
------
::

    python -m app.web                 # 起 hub + 演示数据 + 页面，自动开浏览器
    python -m app.web --viewer s01    # 以某个参与者的视角打开
    python -m app.web --students 12   # 房间里放 12 个参与者

为什么要起**两个**服务
----------------------
1. ``app.hub`` 的 HTTP 服务（``GET /snapshot`` / ``POST /hidden`` / ``POST /room``）——
   真实链路。可见性裁剪就发生在它背后，走进程内直调是验不到的；
2. Flet 自己的 web 服务 —— 它负责把控件树推给浏览器。

页面通过 HTTP 拉 hub 的快照，与真实部署里浏览器做的事完全一样。

为什么 ``assets_dir`` 必须是绝对路径
------------------------------------
``ft.app`` 的 ``assets_dir`` 默认值是字符串 ``"assets"``，而 ``app_async`` 内部按
``Path(flet/app.py).parent.parent``（即 **site-packages**）解析相对路径 —— 不是 cwd。
于是默认值指向 ``site-packages/assets``（不存在），被静默丢弃（只打一条 INFO 日志），
``page.fonts`` 里注册的中文字体随之 404 → 又退回 CanvasKit 从 CDN 拉 Noto，
而那条回退**不稳定**（同样的页面有时正常、有时整片豆腐块），且离线必然白屏。
所以这里显式传 ``Path(__file__).resolve().parents[2] / "assets"`` 的绝对路径。

字体是 :file:`assets/fonts/NotoSansSC-Regular.ttf`（Noto Sans SC，OFL 授权，
可随项目分发）。不注册它的话，中文靠运行时从 ``fonts.gstatic.com`` 拉 10.5MB 的
字体 —— 对一个主张「隐私原生、端侧运行」的产品，那既不体面也不可靠。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

from app.demo import (
    DEFAULT_STUDENTS,
    DEFAULT_TEACHER_ID,
    HttpSnapshot,
    build_demo_hub,
    preset_hidden,
)
from app.hub import make_server
from app.web.page import VIEWER_LIMIT, UnifiedPage

if TYPE_CHECKING:
    from flet import Page

__all__: list[str] = []

#: 项目根目录（``app/web/__main__.py`` → 上溯三级）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 静态资源目录。**必须绝对路径**（见模块 docstring）。
ASSETS_DIR = str(PROJECT_ROOT / "assets")

#: 演示里预设为「不公开」的人 —— 让「已隐藏」那几种样式在页面上真的出现一次。
#: 改的是**演示配置**（谁选择不公开），不是感知结果。
DEMO_HIDDEN_IDS = ("s03", "s09", "s17")

#: 中文字体在静态资源目录里的相对路径（与 ``page.fonts`` 的映射必须一致）。
FONT_RELATIVE = "fonts/NotoSansSC-Regular.ttf"


def _check_font() -> bool:
    """字体在不在？不在就**大声说**，别让它变成一片豆腐块。

    字体加载失败**不会抛异常** —— 浏览器只是拿不到字形，然后退回 CanvasKit 的
    在线回退（不稳定）或干脆画方块。没有这条检查时，症状是「页面能开、字全是方块」，
    排查成本很高（本会话就为此浪费过一轮）。返回是否找得到。
    """
    path = Path(ASSETS_DIR) / FONT_RELATIVE
    if path.is_file():
        return True
    print(
        f"[app.web] ⚠️ 找不到中文字体：{path}\n"
        "           页面仍会打开，但中文很可能显示为方块（豆腐块）。\n"
        "           该字体随仓库分发，正常克隆里应当存在 ——\n"
        "           若被删除，请从版本控制恢复：git checkout -- assets/fonts/",
        flush=True,
    )
    return False


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.web", description="灵犀学伴 v8 统一单页前端")
    parser.add_argument("--port", type=int, default=8600, help="页面端口（默认 8600）")
    parser.add_argument("--hub-port", type=int, default=8601, help="hub 端口（默认 8601）")
    parser.add_argument("--students", type=int, default=DEFAULT_STUDENTS, help="房间里的参与者人数")
    parser.add_argument(
        "--viewer",
        default=DEFAULT_TEACHER_ID,
        help="以谁的视角打开（默认发起人 t01；演示控制条里可以随时切换）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="顺手拉起系统默认浏览器（默认不拉：页面通常由 IDE 的面板承载）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # ``ft.app`` 在 ``view=WEB_BROWSER`` 且没有 ``FLET_DISPLAY_URL_PREFIX`` 时会调
    # ``webbrowser.open``。给它一个（空的）前缀就能把那个动作换成「打印 URL」——
    # 本环境下页面由 IDE 的浏览器面板承载，再拉起一个系统默认浏览器只会多开一扇窗。
    if not args.open_browser:
        os.environ.setdefault("FLET_DISPLAY_URL_PREFIX", "")

    hub, driver = build_demo_hub(args.students)
    driver.start()
    # 预热之后再预设未公开者：参与者要跑完第一帧才会出现在在线表里。
    preset_hidden(hub, DEMO_HIDDEN_IDS)

    server = make_server(hub, host=args.host, port=args.hub_port)
    threading.Thread(target=server.serve_forever, name="lingxi-hub", daemon=True).start()

    base_url = f"http://{args.host}:{args.hub_port}"
    students = [f"s{index:02d}" for index in range(1, args.students + 1)]
    viewers = [DEFAULT_TEACHER_ID, *students[:VIEWER_LIMIT]]
    if args.viewer not in viewers:
        viewers.insert(0, args.viewer)

    def target(page: Page) -> None:
        page.fonts = {"LingxiSans": FONT_RELATIVE}
        controller = UnifiedPage(
            page,
            HttpSnapshot(base_url, args.viewer),
            viewer_id=args.viewer,
            viewers=viewers,
            base_url=base_url,
        )
        controller.build()
        controller.start_polling()
        page.on_disconnect = lambda _e: controller.stop_polling()

    print(f"[app.web] hub      → {base_url}/snapshot?viewer={args.viewer}", flush=True)
    print(f"[app.web] 页面     → http://{args.host}:{args.port}/", flush=True)
    print(f"[app.web] 观看者   → {args.viewer}（演示控制条可切换）", flush=True)
    print(f"[app.web] 静态资源 → {ASSETS_DIR}", flush=True)
    _check_font()

    try:
        ft.app(
            target=target,
            view=ft.AppView.WEB_BROWSER,
            port=args.port,
            host=args.host,
            assets_dir=ASSETS_DIR,
        )
    finally:
        driver.stop()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
