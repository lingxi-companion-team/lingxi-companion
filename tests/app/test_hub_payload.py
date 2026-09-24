"""``app.hub`` 的测试：按观看者裁剪 payload + 读写端点。

本文件里两条断言是**整个方案最该守住的**：

- 学生 payload 里 **压根没有** ``summary`` 键（A2）—— 不是「前端不渲染」；
- 教师 payload 的汇总**包含已隐藏者**（A7）且宫格里有全部学生（A1）。

不写这两条，「汇总只给教师」「隐藏有效」就只是前端的一行 ``if``，随时可能被改回去。
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
from common.perception_types import EmotionLabel, FinalState


def _state(label: EmotionLabel = EmotionLabel.FOCUSED, *, stale: bool = False) -> FinalState:
    return FinalState(label=label, timestamp=1.0, stale=stale, confidence=0.9, frame_id=1)


def _people() -> list[ParticipantState]:
    """一间小教室：2 名学生 + 1 名（已隐藏的）学生 + 教师。"""
    return [
        ParticipantState("s01", state=_state(EmotionLabel.FOCUSED)),
        ParticipantState("s02", state=_state(EmotionLabel.CONFUSED)),
        ParticipantState("s03", state=_state(EmotionLabel.DISTRACTED), hidden=True),
        ParticipantState("t01", role=ROLE_TEACHER),
    ]


def _viewer(pid: str) -> ParticipantState:
    return next(p for p in _people() if p.participant_id == pid)


# --------------------------------------------------------------------------
# A2 / A7 —— 裁剪口径
# --------------------------------------------------------------------------


def test_a2_student_payload_has_no_summary_key() -> None:
    payload = build_payload(_viewer("s01"), _people())
    assert "summary" not in payload


def test_a2_student_payload_never_leaks_counts() -> None:
    """连 by_label / online_count 这类字段名都不该出现，避免以后被顺手加回来。"""
    serialized = json.dumps(build_payload(_viewer("s01"), _people()), ensure_ascii=False)
    for forbidden in ("summary", "by_label", "online_count", "ratio", "members_by_label"):
        assert forbidden not in serialized


def test_teacher_payload_has_summary() -> None:
    payload = build_payload(_viewer("t01"), _people())
    assert "summary" in payload


def test_a7_teacher_summary_counts_hidden_students() -> None:
    """A7：已隐藏者也计入教师汇总。"""
    summary = build_payload(_viewer("t01"), _people())["summary"]
    assert summary["online_count"] == 3
    assert summary["by_label"]["distracted"] == 1
    assert summary["members_by_label"]["distracted"] == ["s03"]


def test_a7_teacher_grid_contains_the_hidden_student_with_state() -> None:
    grid = {
        cell["participant_id"]: cell for cell in build_payload(_viewer("t01"), _people())["grid"]
    }
    assert grid["s03"]["state"] is not None, "教师看得见已隐藏者的状态"
    assert grid["s03"]["hidden"] is True


def test_a1_teacher_has_no_cell_in_any_payload() -> None:
    """A1：宫格不含教师格 —— 对教师自己与学生都一样。"""
    for viewer_id in ("s01", "t01"):
        ids = [
            cell["participant_id"] for cell in build_payload(_viewer(viewer_id), _people())["grid"]
        ]
        assert "t01" not in ids


def test_student_grid_masks_the_hidden_peer() -> None:
    grid = {
        cell["participant_id"]: cell for cell in build_payload(_viewer("s01"), _people())["grid"]
    }
    assert grid["s03"]["state"] is None
    assert grid["s03"]["hidden"] is True, "仍占格（§10.1 d2）"


def test_student_grid_shows_self_and_unhidden_peers() -> None:
    ids = [cell["participant_id"] for cell in build_payload(_viewer("s01"), _people())["grid"]]
    assert ids == ["s01", "s02", "s03"]


# --------------------------------------------------------------------------
# 气泡
# --------------------------------------------------------------------------


def test_student_bubble_is_the_personal_state() -> None:
    payload = build_payload(_viewer("s02"), _people())
    assert len(payload["bubble"]) == 1
    assert payload["bubble"][0]["label"] == "confused"


def test_student_without_state_gets_an_empty_bubble() -> None:
    people = [ParticipantState("s01"), _people()[-1]]
    payload = build_payload(people[0], people)
    assert payload["bubble"] == []


def test_teacher_bubble_has_four_components() -> None:
    payload = build_payload(_viewer("t01"), _people())
    assert len(payload["bubble"]) == 4
    counts = {component["label"]: component["count"] for component in payload["bubble"]}
    assert counts == {"focused": 1, "confused": 1, "distracted": 1, "unknown": 0}


def test_payload_echoes_viewer_identity() -> None:
    payload = build_payload(_viewer("s01"), _people())
    assert payload["viewer"] == "s01"
    assert payload["role"] == "student"


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


def test_hub_snapshot_applies_per_viewer_cropping() -> None:
    hub = Hub()
    for participant in _people():
        hub.register(participant)
    assert "summary" not in hub.snapshot_for("s01")
    assert "summary" in hub.snapshot_for("t01")


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


def test_endpoint_serves_a_student_payload(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    status, body, content_type = _get(f"{base}/snapshot?viewer=s01")
    assert status == 200
    assert body["viewer"] == "s01"
    assert "summary" not in body
    assert content_type.startswith("application/json")


def test_endpoint_serves_a_teacher_payload_with_summary(live_server: tuple[Hub, str]) -> None:
    _, base = live_server
    _, body, _ = _get(f"{base}/snapshot?viewer=t01")
    assert "summary" in body


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
