"""汇总与分发中枢：在线表 + 按观看者生成 payload（P1）。

职责边界
--------
本模块只做**装配**：维护在线表、调用 :mod:`app.present` 的纯函数、按观看者组装 payload。
所有可测的判定规则都在 ``app.present`` 里 —— 这样本模块薄到能被 CI 完整覆盖，
而真正复杂的规则也各自有独立测试。

v8（2026-10-01）去分端
----------------------
旧实现里 **学生 payload 压根没有 ``summary`` 键**（验收项 A2），只有教师拿得到聚合。
需求文档 §模块二.1 要求「所有用户共用同一页面形态」，§模块二.3 要求
「公共区域展示**所有已公开状态**的参与者状态概览」—— 所以现在**所有人都拿得到聚合**，
口径统一为「房间内已公开状态的人」。

随之而来的是可见性口径的变化：发起人**不再**享有「看得见全部」的特权，
他的杠杆是 §模块三.2 的房间级公开规则（见 :mod:`app.room`）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from app.envelope import ParticipantState
from app.present.bubble import aggregate_components, personal_components
from app.present.grid import grid_cells
from app.present.summary import room_stats, summarize
from app.present.visibility import visible_to
from app.room import RoomInfo

__all__ = ["Hub", "build_payload", "make_server", "serve"]


def build_payload(
    viewer: ParticipantState,
    participants: Iterable[ParticipantState],
    *,
    room: RoomInfo | None = None,
) -> dict[str, Any]:
    """生成某个观看者**应看到**的 payload。

    三条口径（v8）：

    - **所有人都拿得到** ``summary`` —— 不再有 ``if viewer.is_teacher`` 的分支；
    - ``summary`` 的分母是**已公开状态的人数**，不是房间总人数
      （未公开者的状态本就不该被统计进去，否则「公开开关」就是摆设）；
    - ``room`` 块同时给出总人数与四档去向，让「总数 30、公开 24」的差额有解释。

    ``summary`` 用**掩去后的列表**算（而不是 ``everyone``）：这样「未公开者不计入
    聚合」是由同一条可见性规则顺带保证的，不需要在统计里再写一遍判断 ——
    两处各写一遍迟早会漂移。
    """
    everyone = list(participants)
    visible = visible_to(viewer, everyone, room=room)

    # 只有「已公开且确有状态」的人才进聚合。``visible`` 里未公开的他人已被掩成
    # ``state=None``，所以 ``has_state`` 这一条就足以把两类都排除掉。
    published = [participant for participant in visible if participant.has_state]

    me = next((p for p in everyone if p.participant_id == viewer.participant_id), None)

    room_block: dict[str, Any] = {
        "name": room.name if room is not None else "",
        "topic": room.topic if room is not None else "",
        "invite_code": room.invite_code if room is not None else "",
        "publish_policy": room.publish_policy if room is not None else "optional",
    }
    room_block.update(
        room_stats(
            everyone, forces_publish=room.forces_publish if room is not None else False
        ).to_dict()
    )

    payload: dict[str, Any] = {
        "viewer": viewer.participant_id,
        "role": viewer.role,
        #: 是否房间发起人。界面**只**用它决定「管理区可不可见」，
        #: 不用它决定「能不能看别人的状态」—— 那是公开规则的事。
        "initiator": viewer.is_teacher,
        "room": room_block,
        "grid": [participant.to_dict() for participant in grid_cells(visible)],
        "summary": summarize(published).to_dict(),
        #: 房间聚合分量（恒 4 个，0 人置灰占位）。
        "bubble": aggregate_components(summarize(published).by_label),
        #: 自己的状态与开关。``None`` 表示自己也没有状态（刚进房间 / 已关闭感知）。
        "me": None if me is None else me.to_dict(),
    }
    if me is not None and me.state is not None:
        payload["personal"] = personal_components(me.state)
    else:
        payload["personal"] = []
    return payload


class Hub:
    """内存在线表 + 房间元数据。

    不做持久化也不做鉴权 —— 课堂内网、几十人规模，与会话同生命周期即可。
    将来扩到几百人时，``snapshot_for`` 会退化为「广播全量 + 客户端过滤」，
    **那时可见性性质会退化**，需要重新评估（设计稿 §07.2 已记）。

    房间元数据（名称/主题/邀请码/公开规则）挂在这里而不是另起一个服务：
    它与在线表同生命周期，拆开只会多一层需要同步的引用。
    """

    def __init__(self, room: RoomInfo | None = None) -> None:
        self._participants: dict[str, ParticipantState] = {}
        self._room = room if room is not None else RoomInfo()

    @property
    def room(self) -> RoomInfo:
        return self._room

    def set_room(self, room: RoomInfo) -> None:
        """整块替换房间元数据（改名字、换公开规则都走这里）。"""
        self._room = room

    def set_publish_policy(self, policy: str) -> None:
        """只换公开规则一档（需求文档 §模块三.2）。

        Raises:
            ValueError: 规则名非法。``RoomInfo`` 会拦下并抛错 —— 这里不吞，
                因为「配错规则的房间静默退回默认口径」是最难查的一类问题。
        """
        self._room = self._room.with_policy(policy)

    def register(self, participant: ParticipantState) -> None:
        """登记或更新一个参与者（同 id 覆盖）。"""
        self._participants[participant.participant_id] = participant

    def unregister(self, participant_id: str) -> None:
        """注销；不存在时静默忽略（断开连接是常态，不该抛错）。"""
        self._participants.pop(participant_id, None)

    def set_hidden(self, participant_id: str, hidden: bool) -> None:
        """改写某人的「未公开」开关（需求文档 §模块二.2 的自主开关）。

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

    def get(self, participant_id: str) -> ParticipantState | None:
        """按 id 取一份当前状态；不在线时返回 ``None``。

        为什么需要它：演示驱动线程每帧都要 ``register`` 一遍参与者，而 ``register``
        是**整条替换** —— 界面刚通过 ``POST /hidden`` 写下的开关会被下一帧的
        ``publish`` 用会话自己的 ``hidden`` 冲掉（实测 0.9 秒后弹回）。
        所以喂数方需要先回读「服务端现在认为的开关是什么」，再让本地会话采纳它。
        与 :meth:`set_hidden` 的分工：那个会抛 ``KeyError``（写错了必须响），
        这个返回 ``None``（读不到只是「还没登记」，不是错误）。
        """
        return self._participants.get(participant_id)

    def snapshot_for(self, viewer_id: str) -> dict[str, Any]:
        """按观看者出 payload。

        Raises:
            KeyError: 观看者自己不在在线表里（未登记就取快照属于调用错误）。
        """
        viewer = self._participants.get(viewer_id)
        if viewer is None:
            raise KeyError(viewer_id)
        return build_payload(viewer, self._participants.values(), room=self._room)


class _BoundHandler(BaseHTTPRequestHandler):
    """``GET /snapshot?viewer=<id>``（读）、``POST /hidden``、``POST /room``（写）。"""

    server_version = "LingxiHub/2.0"

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

    def do_POST(self) -> None:
        """写端点：``POST /hidden``（个人公开开关）与 ``POST /room``（房间公开规则）。

        为什么必须有一个写端点：§模块二.2 的前端开关若没有回写路径，就是一个
        「点了没用」的控件 —— 而可见性裁剪在**服务端**，客户端无法自行生效。

        为什么单开端点而不是往 ``GET /snapshot`` 上挂参数：GET 不该有副作用
        （任何预取/重试都可能意外改写别人的可见性），读写分开也让权限边界将来好收紧。

        ⚠️ ``/room`` 目前**没有鉴权**：真实部署时必须校验「调用者是发起人」。
        当前是内网单房间的演示规模，且服务只监听 127.0.0.1；这一点记在
        团队内部的方案文档里（不入库），不在这里假装已经做好了。
        """
        parsed = urlparse(self.path)
        if parsed.path == "/hidden":
            self._post_hidden()
            return
        if parsed.path == "/room":
            self._post_room()
            return
        self._respond(404, {"error": "not found"})

    def _post_hidden(self) -> None:
        """上报个人的「是否向房间公开」开关（§模块二.2）。"""
        try:
            body = self._read_json()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return

        participant_id = body.get("participant_id")
        hidden = body.get("hidden")
        if not isinstance(participant_id, str) or not participant_id:
            self._respond(400, {"error": "participant_id must be a non-empty string"})
            return
        if not isinstance(hidden, bool):
            self._respond(400, {"error": "hidden must be a boolean"})
            return

        try:
            self._hub.set_hidden(participant_id, hidden)
        except KeyError:
            self._respond(404, {"error": f"unknown participant: {participant_id!r}"})
            return
        self._respond(200, {"participant_id": participant_id, "hidden": hidden})

    def _post_room(self) -> None:
        """改房间的公开规则（§模块三.2「全员强制公开 / 个人自主选择」）。"""
        try:
            body = self._read_json()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return

        policy = body.get("publish_policy")
        if not isinstance(policy, str) or not policy:
            self._respond(400, {"error": "publish_policy must be a non-empty string"})
            return
        try:
            self._hub.set_publish_policy(policy)
        except ValueError as exc:
            # 非法规则名：400 而不是静默退回默认 —— 配错规则的房间最不该被静默接受。
            self._respond(400, {"error": str(exc)})
            return
        self._respond(200, {"publish_policy": policy})

    def _read_json(self) -> dict[str, Any]:
        """读请求体并解析为 JSON 对象；空体 / 非法 JSON / 非对象一律抛 ``ValueError``。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("invalid Content-Length header") from exc
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            raise ValueError("empty request body")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("request body must be a JSON object")
        return cast(dict[str, Any], parsed)

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
