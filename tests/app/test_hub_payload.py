"""``app.hub`` 的测试：payload 装配 + 读写端点。

v8（2026-10-01）去分端后，本文件里三条断言是**整个方案最该守住的**：

- **所有人都拿得到** ``summary``（旧实现里学生 payload 压根没有这个键）；
- ``summary`` 的分母是**已公开状态的人数**，未公开者的状态不进聚合；
- **发起人没有隐私后门** —— 他在「自主选择」模式下同样看不到未公开者的状态。

不写这三条，「界面统一化」与「公开开关真的生效」就只是前端的一行 ``if``，
随时可能被改回去。
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pytest

from app.envelope import ROLE_TEACHER, ParticipantState
from app.hub import Hub, build_payload, make_server
from app.room import POLICY_MANDATORY, RoomInfo
from common.perception_types import EmotionLabel, FinalState


def _state(label: EmotionLabel = EmotionLabel.FOCUSED, *, stale: bool = False) -> FinalState:
    return FinalState(label=label, timestamp=1.0, stale=stale, confidence=0.9, frame_id=1)


def _people() -> list[ParticipantState]:
    """一间小房间：2 名已公开 + 1 名未公开 + 1 名还没出结果 + 发起人。"""
    return [
        ParticipantState("s01", state=_state(EmotionLabel.FOCUSED)),
        ParticipantState("s02", state=_state(EmotionLabel.CONFUSED)),
        ParticipantState("s03", state=_state(EmotionLabel.DISTRACTED), hidden=True),
        ParticipantState("t01", role=ROLE_TEACHER),
    ]


def _viewer(pid: str) -> ParticipantState:
    return next(p for p in _people() if p.participant_id == pid)


# --------------------------------------------------------------------------
# 去分端 —— 聚合对所有人可见
# --------------------------------------------------------------------------


@pytest.mark.parametrize("viewer_id", ["s01", "s02", "s03", "t01"])
def test_everyone_gets_the_summary(viewer_id: str) -> None:
    """**v8 的核心断言**：发起人与普通参与者拿到的 payload **结构完全一致**。

    旧实现里 ``summary`` 只在 ``viewer.is_teacher`` 时才下发。需求文档 §模块二.1
    要求「所有用户共用同一页面形态」，§模块二.3 要求公共区域展示状态概览 ——
    所以聚合必须对所有人可见。
    """
    payload = build_payload(_viewer(viewer_id), _people())
    assert "summary" in payload
    assert "room" in payload


def test_payload_shape_is_identical_regardless_of_role() -> None:
    """两端的**键集合**必须一模一样 —— 否则前端仍要分端渲染。"""
    initiator_keys = set(build_payload(_viewer("t01"), _people()))
    participant_keys = set(build_payload(_viewer("s01"), _people()))
    assert initiator_keys == participant_keys


def test_summary_counts_only_published_states() -> None:
    """分母是**已公开状态的人**：s01 专注 + s02 困惑 = 2；未公开的 s03 不计入。

    旧实现里教师汇总把未公开者也算进去（``online_count == 3``）。v8 改成
    「未公开不进聚合」—— 否则 §模块二.2 的公开开关就是摆设。
    """
    summary = build_payload(_viewer("t01"), _people())["summary"]
    assert summary["online_count"] == 2
    assert summary["by_label"]["focused"] == 1
    assert summary["by_label"]["confused"] == 1
    assert summary["by_label"]["distracted"] == 0
    assert "s03" not in json.dumps(summary["members_by_label"], ensure_ascii=False)


def test_room_block_reports_where_everyone_went() -> None:
    """``room`` 块必须让「总数 4、公开 2」的差额有答案：1 未公开 + 1 待结果 + 发起人。"""
    room = build_payload(_viewer("s01"), _people())["room"]
    assert room["total_count"] == 4
    assert room["published_count"] == 2
    assert room["hidden_count"] == 1
    assert room["pending_count"] == 1
    assert room["closed_count"] == 0
    assert (
        room["published_count"]
        + room["hidden_count"]
        + room["pending_count"]
        + room["closed_count"]
        == room["total_count"]
    )


def test_initiator_flag_is_exposed_but_does_not_grant_visibility() -> None:
    """``initiator`` 只用来决定「管理区可不可见」，不决定「能不能看别人的状态」。"""
    initiator = build_payload(_viewer("t01"), _people())
    assert initiator["initiator"] is True
    grid = {cell["participant_id"]: cell for cell in initiator["grid"]}
    assert grid["s03"]["state"] is None, "发起人同样看不到未公开者的状态"


def test_initiator_has_no_cell_in_any_payload() -> None:
    """发起人不占格 —— 对发起人自己与对普通参与者都一样。"""
    for viewer_id in ("s01", "t01"):
        ids = [
            cell["participant_id"] for cell in build_payload(_viewer(viewer_id), _people())["grid"]
        ]
        assert "t01" not in ids


def test_grid_masks_the_unpublished_peer_for_everyone() -> None:
    grid = {
        cell["participant_id"]: cell for cell in build_payload(_viewer("s01"), _people())["grid"]
    }
    assert grid["s03"]["state"] is None
    assert grid["s03"]["hidden"] is True, "仍占格（§10.1 d2）"


def test_grid_shows_self_and_published_peers() -> None:
    ids = [cell["participant_id"] for cell in build_payload(_viewer("s01"), _people())["grid"]]
    assert ids == ["s01", "s02", "s03"]


def test_mandatory_room_policy_publishes_everyone() -> None:
    """「全员强制公开」时，未公开者的状态也进聚合（发起人的规则杠杆）。"""
    room = RoomInfo(
        name="周三晚自习", topic="线性代数", invite_code="LX-3F9K", publish_policy=POLICY_MANDATORY
    )
    payload = build_payload(_viewer("s01"), _people(), room=room)
    assert payload["room"]["published_count"] == 3
    assert payload["summary"]["by_label"]["distracted"] == 1
    assert payload["room"]["name"] == "周三晚自习"
    assert payload["room"]["invite_code"] == "LX-3F9K"


def test_room_defaults_are_blank_not_fabricated() -> None:
    """未配置房间时字段是**空串**，而不是编一个假邀请码 —— 前端据此显示「未生成」。"""
    room = build_payload(_viewer("s01"), _people())["room"]
    assert room["name"] == ""
    assert room["topic"] == ""
    assert room["invite_code"] == ""
    assert room["publish_policy"] == "optional"


# --------------------------------------------------------------------------
# 分量与个人状态
# --------------------------------------------------------------------------


def test_aggregate_bubble_is_the_room_wide_breakdown() -> None:
    """``bubble`` 现在是**房间聚合分量**（恒 4 个），不再是「学生只看自己那一个」。"""
    payload = build_payload(_viewer("s02"), _people())
    assert len(payload["bubble"]) == 4
    counts = {component["label"]: component["count"] for component in payload["bubble"]}
    assert counts == {"focused": 1, "confused": 1, "distracted": 0, "unknown": 0}


def test_personal_block_is_the_viewers_own_state() -> None:
    payload = build_payload(_viewer("s02"), _people())
    assert len(payload["personal"]) == 1
    assert payload["personal"][0]["label"] == "confused"
    assert payload["me"]["participant_id"] == "s02"


def test_personal_block_is_empty_without_a_state() -> None:
    people = [ParticipantState("s01"), _people()[-1]]
    payload = build_payload(people[0], people)
    assert payload["personal"] == []
    assert payload["me"]["participant_id"] == "s01"
    assert payload["me"]["state"] is None


def test_payload_echoes_viewer_identity() -> None:
    payload = build_payload(_viewer("s01"), _people())
    assert payload["viewer"] == "s01"
    assert payload["role"] == "student"
    assert payload["initiator"] is False


# --------------------------------------------------------------------------
# Hub 在线表
# --------------------------------------------------------------------------


def test_hub_registers_and_lists_participants() -> None:
    hub = Hub()
    hub.register(ParticipantState("s01", state=_state()))
    assert [p.participant_id for p in hub.participants] == ["s01"]


def test_hub_register_overwrites_same_id() -> None:
    hub = Hub()
    hub.register(ParticipantState("s01", state=_state()))
    hub.register(ParticipantState("s01", state=_state(EmotionLabel.CONFUSED)))
    assert len(hub.participants) == 1
    assert hub.participants[0].state is not None
    assert hub.participants[0].state.label is EmotionLabel.CONFUSED


def test_hub_unregister_is_silent_when_absent() -> None:
    """断开连接是常态，注销不存在的 id 不该抛错。"""
    hub = Hub()
    hub.unregister("ghost")


def test_hub_set_hidden_toggles_the_flag() -> None:
    hub = Hub()
    hub.register(ParticipantState("s01", state=_state()))
    hub.set_hidden("s01", True)
    assert hub.participants[0].hidden is True
    hub.set_hidden("s01", False)
    assert hub.participants[0].hidden is False


def test_hub_set_hidden_raises_for_unknown_participant() -> None:
    """调用错误不该静默吞掉。"""
    with pytest.raises(KeyError):
        Hub().set_hidden("ghost", True)


def test_hub_snapshot_for_unknown_viewer_raises() -> None:
    with pytest.raises(KeyError):
        Hub().snapshot_for("ghost")


def test_hub_snapshot_is_uniform_across_viewers() -> None:
    """同一间房间里，两个观看者的 payload **结构一致**（去分端的传输层保证）。"""
    hub = Hub()
    for participant in _people():
        hub.register(participant)
    assert set(hub.snapshot_for("s01")) == set(hub.snapshot_for("t01"))


def test_hub_room_policy_change_takes_effect() -> None:
    """发起人换公开规则后，聚合口径立即变化（端到端性质的单进程版）。"""
    hub = Hub()
    for participant in _people():
        hub.register(participant)
    assert hub.snapshot_for("s01")["room"]["published_count"] == 2
    hub.set_publish_policy(POLICY_MANDATORY)
    assert hub.snapshot_for("s01")["room"]["published_count"] == 3


def test_hub_rejects_an_invalid_policy() -> None:
    """非法规则名必须响，不能静默退回默认 —— 配错规则的房间最难查。"""
    hub = Hub()
    with pytest.raises(ValueError, match="publish_policy"):
        hub.set_publish_policy("whatever")


def test_hub_room_defaults_to_blank() -> None:
    hub = Hub()
    assert hub.room.name == ""
    assert hub.room.invite_code == ""
    hub.set_room(RoomInfo(name="周三晚自习", invite_code="LX-3F9K"))
    assert hub.room.name == "周三晚自习"


# --------------------------------------------------------------------------
# 只读端点（起真实端口验证，不是 mock）
# --------------------------------------------------------------------------


@pytest.fixture()
def live_server() -> Iterator[tuple[Hub, str]]:
    """起一个真实监听的服务（``port=0`` 由系统分配空闲端口，避免冲突）。"""
    hub = Hub()
    for participant in _people():
        hub.register(participant)
    server = make_server(hub, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield hub, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url: str) -> tuple[int, dict[str, object], str]:
    with urlopen(url, timeout=5) as response:
        return (
            response.status,
            json.loads(response.read().decode("utf-8")),
            response.headers["Content-Type"],
        )


def test_endpoint_serves_a_uniform_payload(live_server: tuple[Hub, str]) -> None:
    """任何 viewer 都拿到带 ``summary`` 与 ``room`` 的同一形态 payload。"""
    _, base = live_server
    status, body, content_type = _get(f"{base}/snapshot?viewer=s01")
    assert status == 200
    assert body["viewer"] == "s01"
    assert "summary" in body
    assert "room" in body
    assert content_type.startswith("application/json")


def test_endpoint_returns_the_same_shape_for_the_initiator(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    _, body, _ = _get(f"{base}/snapshot?viewer=t01")
    assert "summary" in body
    assert body["initiator"] is True


def test_endpoint_404_for_unknown_viewer(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        urlopen(f"{base}/snapshot?viewer=ghost", timeout=5)
    assert excinfo.value.code == 404


def test_endpoint_404_when_viewer_parameter_is_missing(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        urlopen(f"{base}/snapshot", timeout=5)
    assert excinfo.value.code == 404


def test_endpoint_404_for_unknown_path(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        urlopen(f"{base}/health", timeout=5)
    assert excinfo.value.code == 404


def test_endpoint_reflects_a_hidden_toggle_immediately(live_server: tuple[Hub, str]) -> None:
    """端到端：把 s03 的隐藏开关打开后，s01 的宫格里 s03 状态立即消失。"""
    hub, base = live_server
    hub.set_hidden("s03", True)
    _, body, _ = _get(f"{base}/snapshot?viewer=s01")
    grid = {cell["participant_id"]: cell for cell in body["grid"]}
    assert grid["s03"]["state"] is None


# --------------------------------------------------------------------------
# 写端点 POST /hidden（D5 开关的上报通道）
# --------------------------------------------------------------------------


def _post(url: str, body: object) -> tuple[int, dict[str, object]]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _raw_post(base: str, *, headers: str, body: bytes = b"") -> int:
    """发一条原始 HTTP 请求并返回状态码。

    为什么需要它：``urllib`` 造不出「``Content-Length`` 不是数字」「请求体不是合法
    UTF-8」这类畸形请求，而这几个分支恰恰是 400 的判定逻辑所在 —— 不测就等于没写。
    """
    parsed = urlparse(base)
    assert parsed.hostname is not None and parsed.port is not None
    head = f"POST /hidden HTTP/1.1\r\nHost: {parsed.netloc}\r\n{headers}Connection: close\r\n\r\n"
    with socket.create_connection((parsed.hostname, parsed.port), timeout=5) as sock:
        sock.sendall(head.encode("ascii") + body)
        response = b""
        while b"\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    return int(response.split(b" ", 2)[1])


def test_write_endpoint_turns_hiding_off_end_to_end(live_server: tuple[Hub, str]) -> None:
    """A6 的传输版：POST 关闭隐藏 → 同学宫格里重新出现该同学的状态。"""
    hub, base = live_server
    status, body = _post(f"{base}/hidden", {"participant_id": "s03", "hidden": False})
    assert status == 200
    assert body == {"participant_id": "s03", "hidden": False}
    assert next(p for p in hub.participants if p.participant_id == "s03").hidden is False

    _, snapshot, _ = _get(f"{base}/snapshot?viewer=s01")
    grid = {cell["participant_id"]: cell for cell in snapshot["grid"]}
    assert grid["s03"]["state"] is not None, "开关关掉后，同学应重新看得到状态"


def test_write_endpoint_is_idempotent(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    for _ in range(2):
        assert _post(f"{base}/hidden", {"participant_id": "s01", "hidden": True})[0] == 200


def test_write_endpoint_404_for_unknown_participant(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        _post(f"{base}/hidden", {"participant_id": "ghost", "hidden": True})
    assert excinfo.value.code == 404


def test_write_endpoint_404_for_unknown_path(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        _post(f"{base}/elsewhere", {"participant_id": "s01", "hidden": True})
    assert excinfo.value.code == 404


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no-fields"),
        pytest.param({"hidden": True}, id="missing-participant-id"),
        pytest.param({"participant_id": "", "hidden": True}, id="empty-participant-id"),
        pytest.param({"participant_id": 7, "hidden": True}, id="non-string-participant-id"),
        pytest.param({"participant_id": "s01"}, id="missing-hidden"),
        pytest.param({"participant_id": "s01", "hidden": "yes"}, id="non-boolean-hidden"),
    ],
)
def test_write_endpoint_400_for_bad_fields(live_server: tuple[Hub, str], payload: object) -> None:
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        _post(f"{base}/hidden", payload)
    assert excinfo.value.code == 400


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        pytest.param("Content-Length: abc\r\n", b"{}", id="unparsable-content-length"),
        pytest.param("Content-Length: 0\r\n", b"", id="empty-body"),
        pytest.param("Content-Length: 3\r\n", b"{o}", id="invalid-json"),
        pytest.param("Content-Length: 2\r\n", b"[]", id="json-not-an-object"),
        pytest.param("Content-Length: 2\r\n", b"\xff\xfe", id="not-utf8"),
        pytest.param("", b"", id="no-content-length-header"),
    ],
)
def test_write_endpoint_400_for_malformed_requests(
    live_server: tuple[Hub, str], headers: str, body: bytes
) -> None:
    """畸形请求必须被挡在 400，而不是把服务端线程打崩。"""
    _, base = live_server
    assert _raw_post(base, headers=headers, body=body) == 400


def test_server_survives_a_malformed_request(live_server: tuple[Hub, str]) -> None:
    """写路径报 400 之后，读路径必须照常工作（线程没被打死）。"""
    _, base = live_server
    assert _raw_post(base, headers="Content-Length: 0\r\n") == 400
    assert _get(f"{base}/snapshot?viewer=s01")[0] == 200


# --------------------------------------------------------------------------
# 写端点 POST /room（房间公开规则，§模块三.2）
# --------------------------------------------------------------------------


def test_room_endpoint_switches_the_policy(live_server: tuple[Hub, str]) -> None:
    """端到端：切成「全员强制公开」后，未公开者的状态立刻进入聚合。"""
    _, base = live_server
    _, before, _ = _get(f"{base}/snapshot?viewer=s01")
    assert before["room"]["published_count"] == 2

    status, body = _post(f"{base}/room", {"publish_policy": POLICY_MANDATORY})
    assert status == 200
    assert body == {"publish_policy": POLICY_MANDATORY}

    _, after, _ = _get(f"{base}/snapshot?viewer=s01")
    assert after["room"]["publish_policy"] == POLICY_MANDATORY
    assert after["room"]["published_count"] == 3


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no-fields"),
        pytest.param({"publish_policy": ""}, id="empty-policy"),
        pytest.param({"publish_policy": 7}, id="non-string-policy"),
        pytest.param({"publish_policy": "everyone-can-see"}, id="unknown-policy"),
    ],
)
def test_room_endpoint_400_for_bad_policy(live_server: tuple[Hub, str], payload: object) -> None:
    """未知规则名一律 400 —— 绝不静默退回默认口径。"""
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        _post(f"{base}/room", payload)
    assert excinfo.value.code == 400


def test_room_endpoint_404_for_unknown_path(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    with pytest.raises(HTTPError) as excinfo:
        _post(f"{base}/somewhere-else", {"publish_policy": POLICY_MANDATORY})
    assert excinfo.value.code == 404


# --------------------------------------------------------------------------
# Hub.get：读单个参与者（喂数方回读「未公开」开关用）
# --------------------------------------------------------------------------


def test_hub_get_returns_the_current_participant() -> None:
    hub = Hub()
    hub.register(ParticipantState("s01", state=_state()))
    got = hub.get("s01")
    assert got is not None
    assert got.participant_id == "s01"


def test_hub_get_returns_none_instead_of_raising() -> None:
    """读不到只是「还没登记」，不是错误 —— 与 ``set_hidden`` 的分工刻意不同：
    那个写错了必须响（``KeyError``），这个每帧都被喂数线程调用，不能抛。
    """
    assert Hub().get("nobody") is None


def test_hub_get_reflects_a_hidden_write() -> None:
    hub = Hub()
    hub.register(ParticipantState("s01", state=_state()))
    hub.set_hidden("s01", True)
    got = hub.get("s01")
    assert got is not None
    assert got.hidden is True


def test_register_replaces_the_whole_record_including_hidden() -> None:
    """**这是演示里那个「开关 0.9 秒后自己弹回去」的根因**，所以钉在这里。

    ``register`` 是整条替换：喂数线程每帧都 ``register`` 一遍，于是界面刚通过
    ``POST /hidden`` 写下的开关会被下一帧的 ``publish`` 用会话自己的 ``hidden``
    冲掉。修法是喂数方先 ``get`` 回读、再让本地会话采纳（见
    ``app.demo.DemoDriver.pump``）—— 这条测试锁住「为什么会需要那个回读」。
    """
    hub = Hub()
    hub.register(ParticipantState("s01", state=_state(), hidden=True))
    hub.register(ParticipantState("s01", state=_state(), hidden=False))
    got = hub.get("s01")
    assert got is not None
    assert got.hidden is False
