"""``app.present.visibility`` 的测试：按观看者裁剪 + 房间公开规则。

守护三条性质：

- **未公开的他人状态看不到**（自主选择模式）；
- **自己永远看得见自己** —— 否则开关一按自己都不知道自己什么状态；
- **发起人没有隐私后门** —— 这是 v8（2026-10-01）按需求文档**有意删除**的一条旧特权
  （旧实现有 ``if viewer.is_teacher: return people``）。所以这里不再有「教师不受影响」
  的断言，取而代之的是 ``test_initiator_has_no_privacy_backdoor``：
  发起人在「自主选择」模式下，同样看不到未公开者的状态。

另外守护一处**与设计稿伪代码的有意偏离**：裁剪实现为「掩去状态」而不是
「过滤掉条目」，因为 §10.1 d2 要求被隐藏者仍占一格。
"""

from __future__ import annotations

from app.envelope import ROLE_TEACHER, ParticipantState
from app.present.grid import grid_cells
from app.present.visibility import HIDDEN_SCOPE_TEXT, visible_to
from app.room import POLICY_MANDATORY, POLICY_OPTIONAL, RoomInfo
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


def test_unpublished_peers_state_is_masked() -> None:
    """A 未公开后，B 看到 A 时拿不到状态。"""
    viewer = _student("s02")
    result = visible_to(viewer, [_student("s01", hidden=True), viewer])
    hidden_peer = next(p for p in result if p.participant_id == "s01")
    assert hidden_peer.state is None
    assert hidden_peer.hidden is True


def test_unpublished_student_still_sees_their_own_state() -> None:
    """自己永远看得见自己 —— 否则开关一按自己都不知道自己什么状态。"""
    me = _student("s01", hidden=True)
    result = visible_to(me, [me, _student("s02")])
    myself = next(p for p in result if p.participant_id == "s01")
    assert myself.state is not None
    assert myself.has_state is True


def test_hiding_is_one_way() -> None:
    """单向 —— A 未公开自己后，仍能观察已公开的同学。"""
    me = _student("s01", hidden=True)
    result = visible_to(me, [me, _student("s02"), _student("s03", hidden=True)])
    peers = {p.participant_id: p for p in result if p.participant_id != "s01"}
    assert peers["s02"].state is not None, "已公开者仍可见"
    assert peers["s03"].state is None, "同样未公开的人也不该互相看到"


# ── v8：发起人没有隐私后门 ────────────────────────────────────────────────


def test_initiator_has_no_privacy_backdoor() -> None:
    """**这是 v8 最重要的一条**：发起人在「个人自主选择」模式下同样看不到未公开者的状态。

    旧实现有一条 ``if viewer.is_teacher: return people``，让教师绕过所有人的隐私开关。
    需求文档 §模块二.1（界面统一化）+ §模块二.2（自主公开）+
    §模块三.2（发起人的杠杆是**定规则**而不是绕过隐私）三条合起来要求删掉它。

    若哪天有人把这条特权加回来，这条测试会红。
    """
    people = [_student("s01", hidden=True), _student("s02")]
    result = visible_to(_teacher(), people)
    hidden_student = next(p for p in result if p.participant_id == "s01")
    assert hidden_student.state is None, "发起人不该看到未公开者的状态"
    assert hidden_student.hidden is True


def test_initiator_sees_the_same_as_everyone_else() -> None:
    """发起人的可见结果与普通参与者**逐条相同**（界面统一化的传输层保证）。"""
    people = [_student("s01", hidden=True), _student("s02"), _student("s03", hidden=True)]
    assert visible_to(_teacher(), people) == visible_to(_student("s02"), people)


def test_mandatory_policy_reveals_everything_to_everyone() -> None:
    """「全员强制公开」时个人开关被忽略 —— 包括对普通参与者。"""
    room = RoomInfo(publish_policy=POLICY_MANDATORY)
    people = [_student("s01", hidden=True), _student("s02")]
    for viewer in (_teacher(), _student("s02")):
        result = visible_to(viewer, people, room=room)
        assert next(p for p in result if p.participant_id == "s01").state is not None


def test_optional_policy_is_the_default_when_no_room_given() -> None:
    """``room=None``（裸 hub）走**最保守**的一档，而不是最宽松的那档。"""
    people = [_student("s01", hidden=True)]
    assert visible_to(_student("s02"), people)[0].state is None
    assert visible_to(
        _student("s02"), people, room=RoomInfo(publish_policy=POLICY_OPTIONAL)
    ) == visible_to(_student("s02"), people)


def test_hidden_peer_keeps_its_cell_in_the_grid() -> None:
    """§10.1 d2：他人视角下仍占格（显示为「未公开」）。"""
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


# ---------------------------------------------------------------------------
# 开关文案（HIDDEN_SCOPE_TEXT）—— 它是一句**隐私承诺**，必须与行为一致
# ---------------------------------------------------------------------------


def test_hidden_scope_text_does_not_promise_a_privileged_viewer() -> None:
    """文案不得暗示「还有人能看到」。

    历史教训（2026-10-02）：旧文案是「仅对同学隐藏（**教师仍可见**）」，
    那是 v7 的语义（教师绕过个人开关）。v8 删掉角色分支后，同一句话变成
    **把保护范围说小**的误导性承诺 —— 用户以为老师还能看到，于是不敢关。

    这条断言把「不得出现任何角色词」变成机器检查，防止文案被改回去。
    """
    forbidden = ("教师", "老师", "同学", "学生", "管理员", "发起人")
    hits = [word for word in forbidden if word in HIDDEN_SCOPE_TEXT]
    assert not hits, f"开关文案不得提到特定角色（命中 {hits}）：{HIDDEN_SCOPE_TEXT}"


def test_hidden_scope_text_states_the_actual_scope() -> None:
    """文案必须同时说清两件事：对谁隐藏、以及自己是否还看得见。"""
    assert "房间" in HIDDEN_SCOPE_TEXT, "必须说明作用范围是「房间」"
    assert "本人" in HIDDEN_SCOPE_TEXT, "必须说明自己仍然可见"


def test_hidden_scope_text_matches_the_rule_it_describes() -> None:
    """文案说「仅本人可见」—— 就用真规则跑一遍，确认不是空话。

    掩去之后，除自己以外的任何人拿到的都是 ``state is None``。
    """
    me = _student("s01", hidden=True)
    other = _student("s02")
    teacher = _teacher()
    people = [me, other, teacher]

    for viewer in (other, teacher):
        visible = {p.participant_id: p for p in visible_to(viewer, people)}
        assert visible["s01"].state is None, f"{viewer.participant_id} 不该看到 s01 的状态"
    # 自己仍然看得见 —— 文案里的「仅本人可见」这半句同样要成立。
    visible = {p.participant_id: p for p in visible_to(me, people)}
    assert visible["s01"].state is not None
