"""``app.present.visibility`` 的测试：D5 的按观看者裁剪。

这是本批最重要的测试之一 —— 它逐条守护验收项 A6（隐藏是**单向**的）
与 A7（教师端不受影响）。

另外守护一处**与设计稿伪代码的有意偏离**：裁剪实现为「掩去状态」而不是
「过滤掉条目」，因为 §10.1 d2 要求被隐藏者仍占一格。
"""

from __future__ import annotations

from app.envelope import ROLE_TEACHER, ParticipantState
from app.present.grid import grid_cells
from app.present.visibility import visible_to
from common.perception_types import EmotionLabel, FinalState


def _state(label: EmotionLabel = EmotionLabel.FOCUSED) -> FinalState:
    return FinalState(label=label, timestamp=1.0, confidence=0.6)


def _student(pid: str, *, hidden: bool = False) -> ParticipantState:
    return ParticipantState(pid, state=_state(), hidden=hidden, ts=1.0)


def _teacher() -> ParticipantState:
    return ParticipantState("t01", role=ROLE_TEACHER)


def test_length_and_order_are_preserved() -> None:
    """掩去而非过滤：条目数与顺序都不变，否则宫格会重排。"""
    people = [_student("s01"), _student("s02", hidden=True), _student("s03")]
    result = visible_to(_student("s01"), people)
    assert [p.participant_id for p in result] == ["s01", "s02", "s03"]


def test_a6_1_student_cannot_see_a_hidden_peers_state() -> None:
    """A6-①：A 隐藏后，B 看到 A 时拿不到状态。"""
    viewer = _student("s02")
    result = visible_to(viewer, [_student("s01", hidden=True), viewer])
    hidden_peer = next(p for p in result if p.participant_id == "s01")
    assert hidden_peer.state is None
    assert hidden_peer.hidden is True


def test_a6_2_hidden_student_still_sees_their_own_state() -> None:
    """A6-②：自己永远看得见自己 —— 否则开关一按自己都不知道自己什么状态。"""
    me = _student("s01", hidden=True)
    result = visible_to(me, [me, _student("s02")])
    myself = next(p for p in result if p.participant_id == "s01")
    assert myself.state is not None
    assert myself.has_state is True


def test_a6_3_hidden_student_still_sees_unhidden_peers() -> None:
    """A6-③：单向 —— A 隐藏自己后，仍能观察未关闭的同学。"""
    me = _student("s01", hidden=True)
    result = visible_to(me, [me, _student("s02"), _student("s03", hidden=True)])
    peers = {p.participant_id: p for p in result if p.participant_id != "s01"}
    assert peers["s02"].state is not None, "未隐藏者仍可见"
    assert peers["s03"].state is None, "同样隐藏的人也不该互相看到"


def test_a7_teacher_is_unaffected_by_hiding() -> None:
    """A7：教师端不受影响 —— 已隐藏者的状态照常可见。"""
    people = [_student("s01", hidden=True), _student("s02")]
    result = visible_to(_teacher(), people)
    hidden_student = next(p for p in result if p.participant_id == "s01")
    assert hidden_student.state is not None
    assert hidden_student.has_state is True


def test_teacher_result_is_identical_to_input() -> None:
    people = [_student("s01", hidden=True), _student("s02")]
    assert visible_to(_teacher(), people) == people


def test_hidden_peer_keeps_its_cell_in_the_grid() -> None:
    """§10.1 d2：他人视角下仍占格（显示为「已隐藏」）。"""
    viewer = _student("s02")
    visible = visible_to(viewer, [_student("s01", hidden=True), viewer])
    assert [p.participant_id for p in grid_cells(visible)] == ["s01", "s02"]


def test_empty_input() -> None:
    assert visible_to(_student("s01"), []) == []
    assert visible_to(_teacher(), []) == []


def test_input_iterable_is_not_mutated() -> None:
    people = [_student("s01", hidden=True), _student("s02")]
    visible_to(_student("s02"), people)
    assert people[0].state is not None, "原对象必须保持不可变"
