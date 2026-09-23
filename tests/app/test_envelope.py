"""``app.envelope`` 的测试：信封往返、非法输入、占格判据。"""

from __future__ import annotations

import pytest

from app.envelope import ROLE_STUDENT, ROLE_TEACHER, ROLES, ParticipantState
from common.perception_types import EmotionLabel, FinalState


def _state(label: EmotionLabel = EmotionLabel.FOCUSED, confidence: float = 0.8) -> FinalState:
    return FinalState(label=label, timestamp=1.5, confidence=confidence, frame_id=3)


def test_defaults_are_student_without_state() -> None:
    participant = ParticipantState("s01")
    assert participant.role == ROLE_STUDENT
    assert participant.state is None
    assert participant.hidden is False
    assert participant.ts == 0.0
    assert participant.is_teacher is False


def test_rejects_empty_participant_id() -> None:
    with pytest.raises(ValueError, match="participant_id"):
        ParticipantState("")


def test_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="role"):
        ParticipantState("s01", role="admin")


def test_rejects_negative_ts() -> None:
    with pytest.raises(ValueError, match="ts"):
        ParticipantState("s01", ts=-0.1)


def test_roles_tuple_is_the_allowed_set() -> None:
    assert ROLES == (ROLE_STUDENT, ROLE_TEACHER)


def test_teacher_is_not_a_teacher_by_id_but_by_role() -> None:
    assert ParticipantState("t01", role=ROLE_TEACHER).is_teacher is True
    assert ParticipantState("s01").is_teacher is False


def test_has_state_tracks_the_state_field() -> None:
    assert ParticipantState("s01", state=_state()).has_state is True
    assert ParticipantState("s01").has_state is False


def test_teacher_never_occupies_a_cell() -> None:
    """教师没有状态、也不隐藏 → 不占格，宫格据此自然排除它。"""
    assert ParticipantState("t01", role=ROLE_TEACHER).occupies_cell is False


def test_plain_student_without_state_does_not_occupy_a_cell() -> None:
    assert ParticipantState("s01").occupies_cell is False


def test_hidden_student_still_occupies_a_cell() -> None:
    """设计稿 §10.1 d2：被隐藏者仍占格（只显示「已隐藏」），否则宫格会重排。"""
    masked = ParticipantState("s02", state=_state(), hidden=True).masked()
    assert masked.state is None
    assert masked.hidden is True
    assert masked.occupies_cell is True


def test_masked_keeps_everything_but_the_state() -> None:
    original = ParticipantState("s03", state=_state(), hidden=True, ts=2.0)
    masked = original.masked()
    assert masked.state is None
    assert masked.participant_id == "s03"
    assert masked.role == original.role
    assert masked.hidden is True
    assert masked.ts == 2.0


def test_with_hidden_only_changes_the_flag() -> None:
    original = ParticipantState("s04", state=_state())
    toggled = original.with_hidden(True)
    assert toggled.hidden is True
    assert toggled.state is original.state
    assert original.hidden is False, "原对象不可变"


def test_to_dict_shape() -> None:
    participant = ParticipantState("s05", state=_state(EmotionLabel.CONFUSED, 0.42), ts=1.5)
    payload = participant.to_dict()
    assert payload["participant_id"] == "s05"
    assert payload["role"] == ROLE_STUDENT
    assert payload["hidden"] is False
    assert payload["ts"] == 1.5
    assert payload["state"] == {
        "label": "confused",
        "confidence": 0.42,
        "stale": False,
        "timestamp": 1.5,
        "frame_id": 3,
    }


def test_to_dict_state_is_none_for_teacher() -> None:
    assert ParticipantState("t01", role=ROLE_TEACHER).to_dict()["state"] is None


def test_round_trip_preserves_everything() -> None:
    original = ParticipantState("s06", state=_state(EmotionLabel.DISTRACTED), hidden=True, ts=9.0)
    restored = ParticipantState.from_dict(original.to_dict())
    assert restored == original


def test_round_trip_without_state() -> None:
    original = ParticipantState("s07")
    assert ParticipantState.from_dict(original.to_dict()) == original


def test_from_dict_applies_defaults_for_optional_fields() -> None:
    restored = ParticipantState.from_dict({"participant_id": "s08", "role": ROLE_STUDENT})
    assert restored.hidden is False
    assert restored.ts == 0.0


def test_from_dict_rejects_missing_identity_fields() -> None:
    with pytest.raises(ValueError, match="participant_id"):
        ParticipantState.from_dict({"role": ROLE_STUDENT})


def test_from_dict_rejects_incomplete_state() -> None:
    """「断言字段完备」是 P0-2 的要求：缺字段必须报错，不能静默补默认值。"""
    with pytest.raises(ValueError, match="state missing required field"):
        ParticipantState.from_dict(
            {
                "participant_id": "s09",
                "role": ROLE_STUDENT,
                "state": {"label": "focused", "confidence": 0.5},
            }
        )


def test_from_dict_rejects_unknown_label() -> None:
    with pytest.raises(ValueError, match="unknown label"):
        ParticipantState.from_dict(
            {
                "participant_id": "s10",
                "role": ROLE_STUDENT,
                "state": {
                    "label": "bored",
                    "confidence": 0.5,
                    "stale": False,
                    "timestamp": 0.0,
                    "frame_id": 0,
                },
            }
        )
