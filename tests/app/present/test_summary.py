"""``app.present.summary`` 的测试：汇总口径与边界。

守护两条决策的落地：D1（按状态分量，不做「不佳」二分）、
D2（分母 = 在线人数，不引入应到人数），以及 A7（教师口径 = 全部，含已隐藏者）。
"""

from __future__ import annotations

import json

from app.envelope import ROLE_TEACHER, ParticipantState
from app.present.summary import DISPLAY_LABELS, LABEL_ORDER, summarize
from common.perception_types import EMOTION_LABELS, EmotionLabel, FinalState


def _student(pid: str, label: EmotionLabel, *, hidden: bool = False) -> ParticipantState:
    state = FinalState(label=label, timestamp=1.0, confidence=0.7)
    return ParticipantState(pid, state=state, hidden=hidden)


def test_display_labels_are_the_three_emotions_plus_unknown() -> None:
    """UNKNOWN 不在 EMOTION_LABELS 里，但展示层需要第 4 个分量。"""
    assert len(DISPLAY_LABELS) == 4
    assert DISPLAY_LABELS[:3] == EMOTION_LABELS
    assert DISPLAY_LABELS[3] is EmotionLabel.UNKNOWN


def test_label_order_is_strings_and_matches_display_labels() -> None:
    assert LABEL_ORDER == tuple(label.value for label in DISPLAY_LABELS)
    assert all(isinstance(key, str) for key in LABEL_ORDER)


def test_all_four_keys_exist_even_when_empty() -> None:
    """0 人的状态也要有键 —— 分量固定 4 个（设计稿 §10.1 d1）。"""
    summary = summarize([])
    assert list(summary.by_label) == list(LABEL_ORDER)
    assert set(summary.by_label.values()) == {0}
    assert summary.online_count == 0


def test_zero_online_gives_zero_ratios() -> None:
    """边界：在线为 0 时不能除零。"""
    summary = summarize([])
    assert set(summary.ratio.values()) == {0.0}


def test_counts_and_ratio() -> None:
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.FOCUSED),
        _student("s03", EmotionLabel.CONFUSED),
        _student("s04", EmotionLabel.DISTRACTED),
    ]
    summary = summarize(people)
    assert summary.online_count == 4
    assert summary.by_label["focused"] == 2
    assert summary.by_label["confused"] == 1
    assert summary.by_label["distracted"] == 1
    assert summary.by_label["unknown"] == 0
    assert summary.ratio["focused"] == 0.5
    assert summary.ratio["unknown"] == 0.0


def test_counts_sum_to_online_count() -> None:
    """A8 的分量自洽性：各分量之和 == 在线人数。"""
    people = [_student(f"s{i:02d}", EmotionLabel.CONFUSED) for i in range(1, 5)]
    summary = summarize(people)
    assert sum(summary.by_label.values()) == summary.online_count


def test_members_by_label_lists_ids_per_state() -> None:
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.CONFUSED),
        _student("s03", EmotionLabel.FOCUSED),
    ]
    members = summarize(people).members_by_label
    assert members["focused"] == ["s01", "s03"]
    assert members["confused"] == ["s02"]
    assert members["unknown"] == []


def test_participants_without_state_are_not_counted() -> None:
    """还没出结果的人不计入在线数 —— 否则各分量之和与分母对不上。"""
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        ParticipantState("s02"),  # 本帧无结果
        ParticipantState("t01", role=ROLE_TEACHER),  # 教师无状态
    ]
    assert summarize(people).online_count == 1


def test_hidden_participants_are_still_counted() -> None:
    """A7：教师口径是「全部」，不因对同学隐藏而缩水。"""
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.DISTRACTED, hidden=True),
    ]
    summary = summarize(people)
    assert summary.online_count == 2
    assert summary.by_label["distracted"] == 1
    assert summary.members_by_label["distracted"] == ["s02"]


def test_all_unknown_scenario() -> None:
    people = [_student(f"s{i:02d}", EmotionLabel.UNKNOWN) for i in range(3)]
    summary = summarize(people)
    assert summary.by_label["unknown"] == 3
    assert summary.ratio["unknown"] == 1.0


def test_ratio_is_only_computed_over_online_participants() -> None:
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        ParticipantState("s02"),  # 不计入分母
    ]
    summary = summarize(people)
    assert summary.ratio["focused"] == 1.0


def test_to_dict_is_json_ready() -> None:
    payload = summarize([_student("s01", EmotionLabel.CONFUSED)]).to_dict()
    assert json.loads(json.dumps(payload))["by_label"]["confused"] == 1
    assert isinstance(payload["members_by_label"]["confused"], list)


def test_to_dict_copies_members_into_lists() -> None:
    original = summarize([_student("s01", EmotionLabel.FOCUSED)]).members_by_label["focused"]
    dumped = summarize([_student("s01", EmotionLabel.FOCUSED)]).to_dict()
    assert dumped["members_by_label"]["focused"] == list(original)
