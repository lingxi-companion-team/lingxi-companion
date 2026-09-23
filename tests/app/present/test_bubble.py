"""``app.present.bubble`` 的测试：D1 的分量生成。

守护验收项 A8：教师气泡的分量数恒为 4、顺序固定、0 人置灰。
"""

from __future__ import annotations

from app.envelope import ParticipantState
from app.present.bubble import student_components, teacher_components
from app.present.colors import DIM_COLOR, STATE_COLORS
from app.present.summary import LABEL_ORDER, summarize
from common.perception_types import EmotionLabel, FinalState


def _state(label: EmotionLabel = EmotionLabel.FOCUSED, *, stale: bool = False) -> FinalState:
    return FinalState(
        label=label,
        timestamp=1.0,
        stale=stale,
        confidence=0.87,
        frame_id=2,
    )


def test_student_bubble_has_exactly_one_component() -> None:
    """本人只有一个状态 → 1 个分量。"""
    components = student_components(_state())
    assert len(components) == 1


def test_student_component_carries_confidence_and_state() -> None:
    component = student_components(_state(EmotionLabel.CONFUSED))[0]
    assert component["label"] == "confused"
    assert component["count"] == 1
    assert component["confidence"] == 0.87
    assert component["stale"] is False
    assert component["dim"] is False
    assert component["color"] == STATE_COLORS[EmotionLabel.CONFUSED]


def test_student_component_reports_stale() -> None:
    """契约层要求 ``stale`` 透出，前端据此显示「维持中」而不是当成新结果。"""
    assert student_components(_state(stale=True))[0]["stale"] is True


def test_teacher_bubble_always_has_four_components() -> None:
    """A8：分量数恒为 4，即使一个状态都没有人。"""
    components = teacher_components({})
    assert len(components) == 4


def test_teacher_component_order_is_fixed() -> None:
    components = teacher_components({"focused": 22, "confused": 3})
    assert [component["label"] for component in components] == list(LABEL_ORDER)


def test_teacher_zero_count_component_is_dimmed_but_kept() -> None:
    """设计稿 §10.1 d1：0 人分量保留占位并置灰（避免气泡宽度跳动）。"""
    components = teacher_components({"focused": 5, "confused": 0})
    by_label = {component["label"]: component for component in components}
    assert by_label["focused"]["dim"] is False
    assert by_label["confused"]["dim"] is True
    assert by_label["confused"]["color"] == DIM_COLOR


def test_teacher_missing_key_is_treated_as_zero() -> None:
    components = teacher_components({"focused": 1})
    counts = {component["label"]: component["count"] for component in components}
    assert counts["focused"] == 1
    assert counts["distracted"] == 0
    assert counts["unknown"] == 0


def test_teacher_example_from_the_design_doc() -> None:
    """复现设计稿里的示例：专注 22 / 困惑 3 / 分神 2 / 未知 0，在线 28。"""
    people = (
        [ParticipantState(f"s{i:02d}", state=_state()) for i in range(22)]
        + [ParticipantState(f"c{i:02d}", state=_state(EmotionLabel.CONFUSED)) for i in range(3)]
        + [ParticipantState(f"d{i:02d}", state=_state(EmotionLabel.DISTRACTED)) for i in range(2)]
        + [ParticipantState("x00")]
    )
    summary = summarize(people)
    assert summary.online_count == 27
    counts = {
        component["label"]: component["count"] for component in teacher_components(summary.by_label)
    }
    assert counts == {"focused": 22, "confused": 3, "distracted": 2, "unknown": 0}
