"""演示环境与 HTTP 客户端 —— **与界面框架无关**的那一半。

为什么从 ``app/client/__main__.py`` 搬出来
--------------------------------------------
原先 ``DemoDriver`` / ``build_demo_hub`` / ``HttpSnapshot`` 都写在 Tk 客户端入口里。
但它们是「起一个真 hub + 喂真数据 + 用 HTTP 拉快照」，**与 Tk 没有半点关系**。
新的统一页面（Flet）要用同一套演示环境，而 ``app/client/__main__.py`` 顶层
``import tkinter`` —— 在无桌面环境或非 Tk 客户端里 import 它会白拉一个 GUI 依赖。

所以把这一半搬到这里：**一个模块，两个前端共用**。Tk 客户端改为从这里 import。

为什么演示要起**真**的 HTTP 服务而不是直接函数调用
----------------------------------------------------
真实链路上「服务端按观看者裁剪」这件事只发生在 HTTP 端点背后。若演示走进程内直调，
就永远验证不到可见性裁剪与写端点 —— 而那正是最容易做错的地方。
所以 ``--demo`` 起 ``http.server``，客户端用 ``urllib`` 去拉，走的是与真实部署相同的路径。

演示数据来自 :class:`app.integration.SerialSession`（三智能体 → 融合 → 平滑）：
环境智能体是真的，表情 / 行为两路缺席时会降级为 UNKNOWN 并如实记进 ``FrameReport``，
所以宫格上看到的颜色与「维持中」角标都是**真的**过了一遍融合与平滑，而不是随机涂色。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast
from urllib.request import Request, urlopen

from app.envelope import ROLE_TEACHER, ParticipantState
from app.hub import Hub
from app.integration import SerialSession
from app.room import POLICY_OPTIONAL, RoomInfo
from common.mock.generator import SCENARIOS, get_scenario, scenario_frame

__all__ = [
    "DEFAULT_INVITE_CODE",
    "DEFAULT_ROOM_NAME",
    "DEFAULT_ROOM_TOPIC",
    "DEFAULT_STUDENTS",
    "DEFAULT_TEACHER_ID",
    "DEFAULT_STUDENT_ID",
    "DemoDriver",
    "HttpSnapshot",
    "build_demo_hub",
    "default_demo_room",
    "preset_hidden",
]

DEFAULT_STUDENTS = 28
DEFAULT_TEACHER_ID = "t01"
DEFAULT_STUDENT_ID = "s01"
STUDENT_PREFIX = "s"
HTTP_TIMEOUT = 5.0

#: 演示房间的默认元数据。**刻意给一个像样的名字与主题** ——
#: 界面要展示「房间名 / 主题 / 邀请码」这三处排版，用空串看不到真实效果；
#: 但邀请码是**显式给的值**，不是界面临时编出来的（真实部署里由服务端生成）。
DEFAULT_ROOM_NAME = "周三晚自习"
DEFAULT_ROOM_TOPIC = "线性代数 · 特征值与特征向量"
DEFAULT_INVITE_CODE = "LX-3F9K"


def default_demo_room() -> RoomInfo:
    """演示房间的元数据。默认走「个人自主选择」—— 展示公开开关的真实效果。

    ⚠️ **演示前提，与需求文档的默认值不同**：需求文档 §模块二.2 规定公开开关
    「默认仅本人可见」，即刚进房间时谁的状态都不对别人公开。但若演示照此默认，
    公共概览一上来就是一屏 0 —— 那个页面看起来像坏了，评审时也看不出聚合区在做什么。

    所以演示房间采取「**全员已公开**，其中几个人预设为未公开」（后者见
    :func:`preset_hidden`）：既让聚合概览有内容，又保留未公开者那几种样式的可见性。
    真实部署必须遵循文档默认值 —— 这条差异是**演示配置**，不是产品行为。
    """
    return RoomInfo(
        name=DEFAULT_ROOM_NAME,
        topic=DEFAULT_ROOM_TOPIC,
        invite_code=DEFAULT_INVITE_CODE,
        publish_policy=POLICY_OPTIONAL,
    )


class HttpSnapshot:
    """通过 HTTP 与 hub 通信：读快照 + 上报「未公开」开关 + 改房间公开规则。

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

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(dict(payload)).encode("utf-8")
        request = Request(
            f"{self._base}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            return cast("dict[str, Any]", json.loads(response.read().decode("utf-8")))

    def set_hidden(self, participant_id: str, hidden: bool) -> None:
        """上报「是否向房间公开」开关（§模块二.2）。服务端才是裁剪点，客户端只能请求。"""
        self._post("/hidden", {"participant_id": participant_id, "hidden": hidden})

    def set_publish_policy(self, policy: str) -> None:
        """上报房间公开规则（§模块三.2，发起人专属）。"""
        self._post("/room", {"publish_policy": policy})


@dataclass
class _Feed:
    """一个演示参与者的喂数状态（各自独立，不共享平滑窗口）。"""

    session: SerialSession
    scenario: str
    length: int
    cursor: int = 0

    def tick(self) -> None:
        """推进一帧：按自己的场景循环取帧，交给会话跑完融合与平滑。

        ``step_perception`` 只产出报告、**不登记在线表** —— 登记是 :meth:`publish` 的事。
        漏掉这一步的症状很隐蔽：hub 里只有发起人，客户端拿到 404，界面显示「离线」。

        这里用**墙钟**给帧盖时间戳，而不是沿用 mock 场景自带的 ``env.ts``。
        后者是 ``0.1 / 0.2 / 0.3`` 这样的合成序号（给单测用的确定性序列），
        而前端的新鲜度判定是 ``now - state.timestamp`` —— 拿墙钟去减 ``0.3``
        会得到十七亿秒，于是**每一格都显示「维持中」**。
        真实采集循环本来就是用墙钟盖戳的，这里只是把同一件事补上。
        （平滑层是「3 中 2」的**计数**窗口，不读 ts，所以换掉它不改变任何判定。）
        """
        results, env = scenario_frame(self.scenario, self.cursor % self.length)
        report = self.session.step_perception(results, replace(env, ts=time.time()))
        self.session.publish(report)
        self.cursor += 1


class DemoDriver:
    """给演示 hub 持续喂数据的后台线程。

    每个参与者一个**独立**的 ``SerialSession``：平滑层是「3 中 2」的滑动窗口，
    共用一个会话会让不同人的窗口互相污染 —— 那样「维持中」就不是真的维持中了。
    代价是几十个会话同时存在，但它们全是纯计算、没有模型权重，开销可以忽略。

    参与者按 ``common.mock.SCENARIOS`` **原样轮转**，刻意不做「只挑好看的场景」这种筛选。
    后果是 ``conflict`` 与 ``low_confidence`` 那两组**永远显示「无结果」** ——
    实测它们在第 1~4 帧的 ``final`` 恒为 ``None``：融合层对这两类输入拒判（输出 ``UNKNOWN``），
    而 UNKNOWN 依契约不参与投票，平滑层自然无票可确认。这正是设计要的行为，
    宫格上留几格「无结果」比伪造成一片整齐的绿更诚实。
    """

    TICK_SECONDS = 0.9

    #: 启动时先跑几帧再开线程。
    #:
    #: 平滑层是「3 中 2」的：第 1、2 帧窗口没填满，``report.final`` 必然是 ``None``。
    #: 若只跑一帧就交界面，用户打开页面看到的会是一屏「无结果」的空宫格 ——
    #: 那不是 bug，是**预热不足**。实测 5 个场景在第 3 帧出稳定状态。
    WARMUP_ROUNDS = 3

    def __init__(self, hub: Hub, student_ids: Sequence[str], scenarios: Sequence[str]) -> None:
        self._hub = hub
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
        """全体推进一帧。

        推进前先**回读服务端的「未公开」开关**并让本地会话采纳 —— 这一步不是可选的。

        ``SerialSession.publish`` 用 ``session.hidden`` 组信封，而 ``Hub.register``
        是整条替换。于是界面通过 ``POST /hidden`` 写下的开关，会被下一次 ``publish``
        用会话自己的 ``hidden``（恒为 ``False``）冲掉：**实测 0.9 秒后开关自己弹回去**。

        为什么修复放在这里而不是让 ``Hub.set_hidden`` 去通知会话：真实部署里这个标志
        归**客户端**所有，服务端只是镜像。演示环境里「喂数的会话」与「浏览器里的界面」
        是两个客户端，所以镜像方向只能是「界面写服务端 → 喂数方回读采纳」。
        让 hub 反向去改会话，等于把服务端变成权威 —— 那是另一套（错误的）架构。
        """
        for feed in self._feeds:
            current = self._hub.get(feed.session.participant_id)
            if current is not None:
                feed.session.hidden = current.hidden
            feed.tick()

    def _loop(self) -> None:
        while not self._stop.wait(self.TICK_SECONDS):
            self.pump()


def build_demo_hub(
    students: int,
    *,
    teacher_id: str = DEFAULT_TEACHER_ID,
    room: RoomInfo | None = None,
) -> tuple[Hub, DemoDriver]:
    """造一个演示用的 hub：1 名发起人 + ``students`` 名参与者，并返回喂数驱动。"""
    if students < 1:
        raise ValueError("--students must be at least 1")
    hub = Hub(room if room is not None else default_demo_room())
    # 发起人率先登记且**不带状态** —— 它因此天然不占格，无需任何前端判断。
    hub.register(ParticipantState(teacher_id, role=ROLE_TEACHER))
    student_ids = [f"{STUDENT_PREFIX}{index:02d}" for index in range(1, students + 1)]
    driver = DemoDriver(hub, student_ids, [scenario.name for scenario in SCENARIOS])
    return hub, driver


def preset_hidden(hub: Hub, participant_ids: Sequence[str]) -> int:
    """把若干参与者预设为「不向房间公开」，返回成功改写的个数。

    **必须在驱动跑过预热之后调用**（见 :func:`build_demo_hub` 的返回值用法）：
    ``DemoDriver.pump`` 会从 hub 回读开关，而参与者要跑完第一帧才会出现在在线表里 ——
    早于那时设置，回读拿到的 ``None`` 会让本次设置被下一帧的无状态发布顶掉。

    为什么要预设几个未公开者：需求文档 §模块二.2 的公开开关是本产品的核心主张之一，
    而「一个未公开的人长什么样」在宫格、统计条、筛选档位上都有专门的样式。
    如果演示环境里人人都公开，那套样式在页面上**一次都不会出现** ——
    评审时看不到，就等于没有。

    这与「不填假数据」不冲突：改的是**演示房间的配置**（谁选择不公开），
    不是感知结果。每一格的颜色仍然是真的过了一遍融合与平滑。
    """
    changed = 0
    for participant_id in participant_ids:
        if hub.get(participant_id) is None:
            continue
        hub.set_hidden(participant_id, True)
        changed += 1
    return changed
