"""``python -m app.client`` —— 桌面客户端入口。

三种用法
--------

.. code-block:: console

    # 1) 一键演示：本机起一个 hub（含假数据驱动），直接打开窗口
    python -m app.client --demo --role teacher
    python -m app.client --demo --role student --viewer s03

    # 2) 连一个已经在跑的 hub（例如另一台机器上的）
    python -m app.client --url http://127.0.0.1:8765 --role student --viewer s03

    # 3) 冒烟自检：构建 + 绘制一轮后立刻退出（无交互，适合脚本化验证）
    python -m app.client --demo --selftest --json

为什么演示要起**真**的 HTTP 服务而不是直接函数调用
--------------------------------------------------
真实链路上「服务端按观看者裁剪」这件事只发生在 HTTP 端点背后。若演示走进程内直调，
就永远验证不到 D5 的按观看者裁剪与写端点 —— 而那正是这一轮最容易做错的地方。
所以 ``--demo`` 起 ``http.server``，客户端用 ``urllib`` 去拉，走的是与真实部署相同的路径。

演示数据来自 ``app.integration.SerialSession``（三智能体 → 融合 → 平滑）：
环境智能体是真的，表情 / 行为两路缺席时会降级为 UNKNOWN 并如实记进 ``FrameReport``，
所以宫格上看到的颜色与「维持中」角标都是**真的**过了一遍融合与平滑，而不是随机涂色。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import tkinter as tk
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from urllib.request import Request, urlopen

from app.client.window import ClientWindow
from app.envelope import ROLE_TEACHER, ParticipantState
from app.hub import Hub, make_server
from app.integration import SerialSession
from common.mock.generator import SCENARIOS, get_scenario, scenario_frame

__all__ = ["DemoDriver", "HttpSnapshot", "build_demo_hub", "main", "parse_args"]

DEFAULT_PORT = 8765
DEFAULT_STUDENTS = 28
DEFAULT_POLL_MS = 1200
DEFAULT_TEACHER_ID = "t01"
DEFAULT_STUDENT_ID = "s01"
STUDENT_PREFIX = "s"
HTTP_TIMEOUT = 5.0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.client",
        description="灵犀学伴 · 桌面客户端（课堂共享宫格 + 教师汇总）",
        epilog="不给 --url 时默认按 --demo 启动一个本机演示 hub。",
    )
    parser.add_argument("--viewer", default=None, help="自己的 participant_id，默认按角色取")
    parser.add_argument("--role", choices=["student", "teacher"], default="student")
    parser.add_argument("--url", default=None, help="已运行的 hub 地址，如 http://127.0.0.1:8765")
    parser.add_argument("--demo", action="store_true", help="本机起演示 hub 并喂入假数据")
    parser.add_argument("--students", type=int, default=DEFAULT_STUDENTS, help="演示的学生数")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="演示 hub 端口，0 = 系统分配"
    )
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS, help="轮询间隔（毫秒）")
    parser.add_argument("--selftest", action="store_true", help="构建并绘制一轮后退出")
    parser.add_argument("--json", action="store_true", help="--selftest 时以 JSON 输出统计")
    return parser.parse_args(argv)


class HttpSnapshot:
    """通过 HTTP 与 hub 通信：读快照 + 上报「对同学隐藏」开关。

    就是一个极小的客户端 —— 只用 stdlib 的 ``urllib``，不引第三方 HTTP 库。
    """

    def __init__(self, base_url: str, viewer_id: str, *, timeout: float = HTTP_TIMEOUT) -> None:
        self._base = base_url.rstrip("/")
        self._viewer = viewer_id
        self._timeout = timeout

    def snapshot(self) -> dict[str, Any]:
        url = f"{self._base}/snapshot?viewer={self._viewer}"
        with urlopen(url, timeout=self._timeout) as response:
            return cast("dict[str, Any]", json.loads(response.read().decode("utf-8")))

    def set_hidden(self, participant_id: str, hidden: bool) -> None:
        """上报隐藏开关（D5）。服务端才是裁剪点，客户端只能请求。"""
        body = json.dumps({"participant_id": participant_id, "hidden": hidden}).encode("utf-8")
        request = Request(
            f"{self._base}/hidden",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            response.read()


@dataclass
class _Feed:
    """一个演示学生的喂数状态（各自独立，不共享平滑窗口）。"""

    session: SerialSession
    scenario: str
    length: int
    cursor: int = 0

    def tick(self) -> None:
        """推进一帧：按自己的场景循环取帧，交给会话跑完融合与平滑。

        ``step_perception`` 只产出报告、**不登记在线表** —— 登记是 :meth:`publish` 的事。
        漏掉这一步的症状很隐蔽：hub 里只有教师，客户端拿到 404，界面显示「离线」。
        """
        report = self.session.step_perception(
            *scenario_frame(self.scenario, self.cursor % self.length)
        )
        self.session.publish(report)
        self.cursor += 1


class DemoDriver:
    """给演示 hub 持续喂数据的后台线程。

    每个学生一个**独立**的 ``SerialSession``：平滑层是「3 中 2」的滑动窗口，共用一个会话
    会让不同人的窗口互相污染 —— 那样「维持中」就不是真的维持中了。代价是 28 个会话
    同时存在，但它们全是纯计算、没有模型权重，开销可以忽略。

    学生按 ``common.mock.SCENARIOS`` **原样轮转**，刻意不做「只挑好看的场景」这种筛选。
    后果是 ``conflict`` 与 ``low_confidence`` 那两组**永远显示「无结果」** ——
    实测它们在第 1~4 帧的 ``final`` 恒为 ``None``：融合层对这两类输入拒判（输出 ``UNKNOWN``），
    而 UNKNOWN 依契约不参与投票，平滑层自然无票可确认。这正是设计要的行为，
    宫格上留几格「无结果」比伪造成一片整齐的绿更诚实。
    """

    TICK_SECONDS = 0.9

    #: 启动时先跑几帧再开线程。
    #:
    #: 平滑层是「3 中 2」的：第 1、2 帧窗口没填满，``report.final`` 必然是 ``None``。
    #: 若只跑一帧就交界面，用户打开窗口看到的会是一屏「无结果」的空宫格 ——
    #: 那不是 bug，是**预热不足**。实测 5 个场景在第 3 帧出稳定状态。
    WARMUP_ROUNDS = 3

    def __init__(self, hub: Hub, student_ids: Sequence[str], scenarios: Sequence[str]) -> None:
        self._feeds: list[_Feed] = []
        for index, student_id in enumerate(student_ids):
            name = scenarios[index % len(scenarios)]
            self._feeds.append(
                _Feed(
                    session=SerialSession(participant_id=student_id, hub=hub),
                    scenario=name,
                    length=len(get_scenario(name).frames),
                )
            )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        for _ in range(self.WARMUP_ROUNDS):
            self.pump()
        self._thread = threading.Thread(target=self._loop, name="demo-driver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        for feed in self._feeds:
            feed.session.close()

    def pump(self) -> None:
        for feed in self._feeds:
            feed.tick()

    def _loop(self) -> None:
        while not self._stop.wait(self.TICK_SECONDS):
            self.pump()


def build_demo_hub(
    students: int, *, teacher_id: str = DEFAULT_TEACHER_ID
) -> tuple[Hub, DemoDriver]:
    """造一个演示用的 hub：1 名教师 + ``students`` 名学生，并返回它们的喂数驱动。"""
    if students < 1:
        raise ValueError("--students must be at least 1")
    hub = Hub()
    # 教师率先登记且**不带状态** —— 它因此天然不占格（A1），无需任何前端判断。
    hub.register(ParticipantState(teacher_id, role=ROLE_TEACHER))
    student_ids = [f"{STUDENT_PREFIX}{index:02d}" for index in range(1, students + 1)]
    driver = DemoDriver(hub, student_ids, [scenario.name for scenario in SCENARIOS])
    return hub, driver


def _default_viewer(role: str) -> str:
    return DEFAULT_TEACHER_ID if role == ROLE_TEACHER else DEFAULT_STUDENT_ID


def _start_demo(args: argparse.Namespace) -> tuple[Hub, DemoDriver, str]:
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
    viewer_id = args.viewer or _default_viewer(args.role)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法初始化图形界面（{exc}）—— 本入口需要桌面环境。", file=sys.stderr)
        return 2
    root.withdraw()

    hub: Hub | None = None
    driver: DemoDriver | None = None
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
        print(f"已连接 {base_url}（viewer={viewer_id}, role={args.role}）")

        window = ClientWindow(
            root,
            source.snapshot,
            viewer_id=viewer_id,
            role=args.role,
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
