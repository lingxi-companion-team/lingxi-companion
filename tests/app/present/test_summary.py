"""``app.present.summary`` 的测试：汇总口径与边界。

守护两条决策的落地：D1（按状态分量，不做「不佳」二分）、
D2（分母 = 在线人数，不引入应到人数），以及 A7（教师口径 = 全部，含已隐藏者）。
"""

from __future__ import annotations

import json

import pytest

from app.envelope import ROLE_TEACHER, ParticipantState
from app.present.summary import DISPLAY_LABELS, LABEL_ORDER, LABEL_TEXT, summarize
from common.perception_types import EMOTION_LABELS, EmotionLabel, FinalState


def _student(pid: str, label: EmotionLabel, *, hidden: bool = False) -> ParticipantState:
    state = FinalState(label=label, timestamp=1.0, confidence=0.7)
    return ParticipantState(pid, state=state, hidden=hidden)


def _closed(pid: str) -> ParticipantState:
    """已关闭感知者：主动停止采集，因此没有状态。"""
    return ParticipantState(pid, closed=True)


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
    """A8 的分量自洽性：各情感分量之和 + 已关闭 == 在线人数。

    v7 起在线人数含「已关闭感知」者，所以不变式要带上 ``closed_count`` ——
    这也是为什么本条刻意放一个已关闭者进来：只测「无关闭」的旧情形，
    这条不变式就退化成恒等式，挡不住任何回归。
    """
    people = [_student(f"s{i:02d}", EmotionLabel.CONFUSED) for i in range(1, 5)]
    people.append(_closed("s05"))
    summary = summarize(people)
    assert sum(summary.by_label.values()) + summary.closed_count == summary.online_count


def test_closed_counts_into_online_but_not_into_by_label() -> None:
    """已关闭感知者人在课堂 → 计入在线；但没有情感状态 → 不进任何情感分量。"""
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _closed("s02"),
        _closed("s03"),
    ]
    summary = summarize(people)
    assert summary.online_count == 3
    assert summary.closed_count == 2
    assert summary.by_label["focused"] == 1
    assert sum(summary.by_label.values()) == 1


def test_closed_ratio_uses_the_online_denominator() -> None:
    """四个情感分量的比例之和 == 1 - closed_ratio（「有 10% 关了，专注最多 90%」）。"""
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _closed("s02"),
    ]
    summary = summarize(people)
    assert summary.closed_ratio == 0.5
    assert summary.ratio["focused"] == 0.5
    assert sum(summary.ratio.values()) + summary.closed_ratio == pytest.approx(1.0)


def test_closed_ratio_is_zero_when_nobody_is_online() -> None:
    assert summarize([]).closed_ratio == 0.0


def test_closed_members_are_listed_in_no_state() -> None:
    """已关闭者不该出现在任何情感名单里（否则气泡展开会张冠李戴）。"""
    summary = summarize([_student("s01", EmotionLabel.FOCUSED), _closed("s02")])
    assert all("s02" not in ids for ids in summary.members_by_label.values())


def test_hidden_and_closed_together_count_once() -> None:
    """同时隐藏且关闭时只计一次 —— 关闭是更强的事实，不能把人数算重。"""
    both = ParticipantState("s01", hidden=True, closed=True)
    summary = summarize([both])
    assert summary.online_count == 1
    assert summary.closed_count == 1


def test_to_dict_includes_closed_fields() -> None:
    payload = summarize([_closed("s01")]).to_dict()
    assert payload["closed_count"] == 1
    assert payload["closed_ratio"] == 1.0


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


def test_label_text_covers_exactly_the_display_keys() -> None:
    """中文展示名的键集必须与分量键集**完全一致**。

    多一个键是死数据，少一个键会让客户端退化成显示英文枚举值 —— 两种都是静默劣化，
    所以这里用等号断言而不是「包含」。
    """
    assert set(LABEL_TEXT) == set(LABEL_ORDER)


def test_label_text_has_no_empty_value() -> None:
    assert all(value.strip() for value in LABEL_TEXT.values())
