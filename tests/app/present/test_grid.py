"""``app.present.grid`` 的测试：占格过滤 + 稳定排序。

重点守护验收项 A1（宫格不含教师格）与「用数据判据而非身份判据」这条设计约束。
"""

from __future__ import annotations

from app.envelope import ROLE_TEACHER, ParticipantState
from app.present.grid import grid_cells, sort_key
from common.perception_types import EmotionLabel, FinalState


def _state(label: EmotionLabel = EmotionLabel.FOCUSED) -> FinalState:
    return FinalState(label=label, timestamp=1.0, confidence=0.5)


def test_teacher_is_excluded() -> None:
    """A1：教师没有状态 → 不进宫格。"""
    teacher = ParticipantState("t01", role=ROLE_TEACHER)
    student = ParticipantState("s01", state=_state())
    assert grid_cells([teacher, student]) == [student]


def test_student_without_state_is_excluded() -> None:
    """本帧还没出结果的参与者不占格。"""
    assert grid_cells([ParticipantState("s01")]) == []


def test_hidden_student_keeps_its_cell() -> None:
    """设计稿 §10.1 d2：被隐藏者仍占格（他人视角下 state 已被掩去）。"""
    masked = ParticipantState("s02", state=_state(), hidden=True).masked()
    assert grid_cells([masked]) == [masked]


def test_classroom_of_thirty_excludes_only_the_teacher() -> None:
    """A1 的量化版：30 名学生 + 1 名教师 → 恰 30 格。"""
    people = [ParticipantState(f"s{index:02d}", state=_state()) for index in range(1, 31)]
    people.append(ParticipantState("t01", role=ROLE_TEACHER))
    assert len(grid_cells(people)) == 30


def test_order_is_stable_and_sorted_by_id() -> None:
    people = [
        ParticipantState("s03", state=_state()),
        ParticipantState("s01", state=_state()),
        ParticipantState("s02", state=_state()),
    ]
    assert [p.participant_id for p in grid_cells(people)] == ["s01", "s02", "s03"]


def test_sorting_does_not_depend_on_input_order() -> None:
    forward = [ParticipantState(f"s{i:02d}", state=_state()) for i in range(1, 8)]
    assert grid_cells(forward) == grid_cells(list(reversed(forward)))


def test_teacher_sorts_last_if_it_ever_occupies_a_cell() -> None:
    """确定性兜底：万一教师带了状态，次序仍可复现（排在学生之后）。"""
    teacher = ParticipantState("t01", role=ROLE_TEACHER, state=_state())
    student = ParticipantState("s01", state=_state())
    assert [p.participant_id for p in grid_cells([teacher, student])] == ["s01", "t01"]


def test_sort_key_prefers_students_over_teachers() -> None:
    student = ParticipantState("s99", state=_state())
    teacher = ParticipantState("t00", role=ROLE_TEACHER)
    assert sort_key(student) < sort_key(teacher)


def test_empty_input() -> None:
    assert grid_cells([]) == []
