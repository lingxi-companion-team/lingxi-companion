"""汇总与分发中枢：在线表 + 按观看者生成 payload（P1）。

职责边界
--------
本模块只做**装配**：维护在线表、调用 :mod:`app.present` 的纯函数、按观看者组装 payload。
所有可测的判定规则都在 ``app.present`` 里 —— 这样本模块薄到能被 CI 完整覆盖，
而真正复杂的规则也各自有独立测试。

服务端裁剪是**硬要求**，不是优化
--------------------------------
学生 payload 里**压根没有** ``summary`` 键（验收项 A2），宫格也按观看者裁剪（D5）。
不是「前端不渲染」—— 见 :mod:`app.present.visibility` 的说明。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from app.envelope import ParticipantState
from app.present.bubble import student_components, teacher_components
from app.present.grid import grid_cells
from app.present.summary import summarize
from app.present.visibility import visible_to

__all__ = ["Hub", "build_payload", "make_server", "serve"]


def build_payload(
    viewer: ParticipantState,
    participants: Iterable[ParticipantState],
) -> dict[str, Any]:
    """生成某个观看者**应看到**的 payload。

    两条硬约束（对应验收项 A2 / A7）：

    - **学生端不含** ``summary`` 键 —— 不是前端不渲染，是压根不下发；
    - **教师端的 ``summary`` 基于全部参与者计算**，不排除已对同学隐藏的人。

    教师端汇总刻意用 ``everyone`` 而不是 ``visible`` 来计算。虽然对教师而言两者当前相同
    （教师不受隐藏影响），但显式写成 ``everyone`` 是为了让「教师口径 = 全部」这条语义
    留在代码里 —— 将来若给教师加过滤条件，这里不会跟着一起缩水。
    """
    everyone = list(participants)
    visible = visible_to(viewer, everyone)

    payload: dict[str, Any] = {
        "viewer": viewer.participant_id,
        "role": viewer.role,
        "grid": [participant.to_dict() for participant in grid_cells(visible)],
    }

    if viewer.is_teacher:
        summary = summarize(everyone)
        payload["summary"] = summary.to_dict()
        payload["bubble"] = teacher_components(summary.by_label)
        return payload

    me = next(
        (p for p in everyone if p.participant_id == viewer.participant_id),
        None,
    )
    payload["bubble"] = student_components(me.state) if me is not None and me.state else []
    return payload


class Hub:
    """内存在线表。

    不做持久化也不做鉴权 —— 课堂内网、几十人规模，与会话同生命周期即可。
    将来扩到几百人时，``snapshot_for`` 会退化为「广播全量 + 客户端过滤」，
    **那时可见性性质会退化**，需要重新评估（设计稿 §07.2 已记）。
    """

    def __init__(self) -> None:
        self._participants: dict[str, ParticipantState] = {}

    def register(self, participant: ParticipantState) -> None:
        """登记或更新一个参与者（同 id 覆盖）。"""
        self._participants[participant.participant_id] = participant

    def unregister(self, participant_id: str) -> None:
        """注销；不存在时静默忽略（断开连接是常态，不该抛错）。"""
        self._participants.pop(participant_id, None)

    def set_hidden(self, participant_id: str, hidden: bool) -> None:
        """改写某人的「对同学隐藏」开关。

        Raises:
            KeyError: 该参与者不在线 —— 这种情况是真的调用错误，不静默吞掉。
        """
        current = self._participants.get(participant_id)
        if current is None:
            raise KeyError(participant_id)
        self._participants[participant_id] = current.with_hidden(hidden)

    @property
    def participants(self) -> list[ParticipantState]:
        return list(self._participants.values())

    def snapshot_for(self, viewer_id: str) -> dict[str, Any]:
        """按观看者出 payload。

        Raises:
            KeyError: 观看者自己不在在线表里（未登记就取快照属于调用错误）。
        """
        viewer = self._participants.get(viewer_id)
        if viewer is None:
            raise KeyError(viewer_id)
        return build_payload(viewer, self._participants.values())


class _BoundHandler(BaseHTTPRequestHandler):
    """只读端点 ``GET /snapshot?viewer=<id>``。"""

    server_version = "LingxiHub/1.0"

    @property
    def _hub(self) -> Hub:
        return cast(_HubServer, self.server).hub

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/snapshot":
            self._respond(404, {"error": "not found"})
            return
        viewer = (parse_qs(parsed.query).get("viewer") or [""])[0]
        try:
            payload = self._hub.snapshot_for(viewer)
        except KeyError:
            self._respond(404, {"error": f"unknown viewer: {viewer!r}"})
            return
        self._respond(200, payload)

    def _respond(self, status: int, body: Mapping[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        """静音访问日志。

        基类实现会把每一行请求写进 stderr，在 pytest 的捕获里表现为「莫名输出」，
        在 CI 日志里则是噪声。显式覆盖成空实现。
        """


class _HubServer(ThreadingHTTPServer):
    """把 :class:`Hub` 挂在 server 上，供 handler 取用（``BaseHTTPRequestHandler`` 的惯用挂法）。"""

    def __init__(self, address: tuple[str, int], hub: Hub) -> None:
        super().__init__(address, _BoundHandler)
        self.hub = hub


def make_server(hub: Hub, host: str = "127.0.0.1", port: int = 8765) -> _HubServer:
    """构造（但**不启动**）服务。

    ``port=0`` 时由操作系统分配空闲端口，测试据此避免端口冲突 ——
    测试里应当传 0，再读 ``server.server_address[1]`` 拿真实端口。
    """
    return _HubServer((host, port), hub)


def serve(hub: Hub, host: str = "127.0.0.1", port: int = 8765) -> None:  # pragma: no cover
    """阻塞式启动服务（真实运行时使用，Ctrl+C 退出）。

    这是一个无限循环，CI 里不可能跑到 —— 覆盖的是 :func:`make_server` 与
    :class:`Hub` 本身，端点行为由 ``tests/app/test_hub_payload.py`` 起真实端口验证。
    """
    with make_server(hub, host, port) as server:
        server.serve_forever()
