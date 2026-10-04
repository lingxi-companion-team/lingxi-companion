"""``app.present.summary`` 的测试：汇总口径与边界。

守护两条决策的落地：D1（按状态分量，不做「不佳」二分）、
D2（分母 = 在线人数，不引入应到人数）。

v8 追加 :func:`room_stats`：房间级的四档计数（已公开 / 未公开 / 待结果 / 未开启感知）。
它存在的意义是让「总数 30、公开 24」旁边那 6 个人去哪了**永远有答案**，
不会在界面上出现对不上的数字。
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.envelope import ROLE_TEACHER, ParticipantState
from app.present.summary import (
    DISPLAY_LABELS,
    LABEL_ORDER,
    LABEL_TEXT,
    RoomStats,
    Summary,
    room_stats,
    summarize,
)
from app.present.trend import TrendBuffer
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


# ── v8：房间级四档计数 ─────────────────────────────────────────────────────


def test_room_stats_counts_the_four_buckets() -> None:
    """一间典型的房间：2 已公开 + 1 未公开 + 1 待结果 + 1 未开启感知 + 发起人。"""
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.CONFUSED),
        _student("s03", EmotionLabel.DISTRACTED, hidden=True),
        ParticipantState("s04"),
        _closed("s05"),
        ParticipantState("t01", role=ROLE_TEACHER),
    ]
    stats = room_stats(people)
    assert stats.total_count == 6
    assert stats.published_count == 2
    assert stats.hidden_count == 1
    assert stats.pending_count == 2  # s04 与发起人 t01 都还没出结果
    assert stats.closed_count == 1


def test_room_stats_buckets_are_exhaustive_and_exclusive() -> None:
    """四档之和必须**恒等于**总人数 —— 这是界面上「数字对得上」的数学保证。"""
    cases = [
        [],
        [_student("s01", EmotionLabel.FOCUSED)],
        [_closed("s01")],
        [ParticipantState("s01")],
        [_student("s01", EmotionLabel.FOCUSED, hidden=True)],
        [
            _student("s01", EmotionLabel.FOCUSED),
            _student("s02", EmotionLabel.CONFUSED, hidden=True),
            ParticipantState("s03"),
            _closed("s04"),
        ],
    ]
    for people in cases:
        stats = room_stats(people)
        assert (
            stats.published_count + stats.hidden_count + stats.pending_count + stats.closed_count
            == stats.total_count
        ), people


def test_room_stats_empty_room() -> None:
    stats = room_stats([])
    assert stats.total_count == 0
    assert stats.publish_ratio == 0.0, "空房间的公开率不能是 0/0"


def test_room_stats_forces_publish_moves_hidden_into_published() -> None:
    """「全员强制公开」时，带未公开标志的人计入**已公开**而不是未公开。

    否则界面会出现「规则写着全员公开，可公开人数却少了几个」的自相矛盾。
    """
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.CONFUSED, hidden=True),
    ]
    assert room_stats(people).published_count == 1
    forced = room_stats(people, forces_publish=True)
    assert forced.published_count == 2
    assert forced.hidden_count == 0
    assert forced.total_count == 2


def test_room_stats_forces_publish_does_not_touch_closed_or_pending() -> None:
    """强制公开**不该**把「未开启感知」「待结果」的人也变成已公开 —— 他们根本没有状态。"""
    people = [_closed("s01"), ParticipantState("s02")]
    forced = room_stats(people, forces_publish=True)
    assert forced.published_count == 0
    assert forced.closed_count == 1
    assert forced.pending_count == 1


def test_room_stats_publish_ratio() -> None:
    people = [_student(f"s{i:02d}", EmotionLabel.FOCUSED) for i in range(3)] + [
        ParticipantState("s99")
    ]
    assert room_stats(people).publish_ratio == pytest.approx(0.75)


def test_room_stats_to_dict_is_json_ready() -> None:
    dumped = room_stats([_student("s01", EmotionLabel.FOCUSED)]).to_dict()
    assert set(dumped) == {
        "total_count",
        "published_count",
        "hidden_count",
        "pending_count",
        "closed_count",
    }
    assert json.dumps(dumped)


def test_room_stats_returns_a_frozen_dataclass() -> None:
    """冻结是刻意的：房间计数一旦算出来，就不该被下游顺手改（那会让界面对不上）。"""
    stats = room_stats([])
    assert isinstance(stats, RoomStats)
    with pytest.raises(dataclasses.FrozenInstanceError):
        stats.total_count = 5  # type: ignore[misc]


# ── Summary.from_dict：线路格式 → Summary（供趋势缓冲消费）────────────────


def test_from_dict_round_trips_through_to_dict() -> None:
    original = summarize(
        [
            _student("s01", EmotionLabel.FOCUSED),
            _student("s02", EmotionLabel.CONFUSED),
            ParticipantState("s03", closed=True),
        ]
    )
    restored = Summary.from_dict(original.to_dict())
    assert restored.by_label == original.by_label
    assert restored.online_count == original.online_count
    assert restored.closed_count == original.closed_count


def test_from_dict_recomputes_the_ratio_instead_of_trusting_the_wire() -> None:
    """**这条是关键**：线路上的 ``ratio`` 一律不采信，按人数现算。

    若照抄线上的比例，一个自相矛盾的 payload（比如 ratio 与 by_label 对不上）
    会让趋势图上的比例与人数各说各话 —— 而那种错看起来只是「图有点怪」，
    极难被发现。重算还顺带修掉「比之和不等于 1」。
    """
    lying = {
        "by_label": {"focused": 2, "confused": 2, "distracted": 0, "unknown": 0},
        "ratio": {"focused": 0.99, "confused": 0.99, "distracted": 0.99, "unknown": 0.99},
        "online_count": 4,
        "members_by_label": {},
        "closed_count": 0,
    }
    summary = Summary.from_dict(lying)
    assert summary.ratio["focused"] == pytest.approx(0.5)
    assert sum(summary.ratio.values()) == pytest.approx(1.0)


def test_from_dict_fills_missing_label_keys_with_zero() -> None:
    summary = Summary.from_dict({"by_label": {"focused": 3}, "online_count": 5})
    assert set(summary.by_label) == set(LABEL_ORDER)
    assert summary.by_label["focused"] == 3
    assert summary.by_label["confused"] == 0


def test_from_dict_ignores_unknown_label_keys() -> None:
    """线上多出来的键一律丢弃 —— 只认 ``LABEL_ORDER`` 里的四个。"""
    summary = Summary.from_dict({"by_label": {"focused": 1, "sleeping": 7}, "online_count": 1})
    assert set(summary.by_label) == set(LABEL_ORDER)
    assert "sleeping" not in summary.by_label


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"by_label": None, "online_count": "abc"},
        {"by_label": {"focused": "abc"}, "online_count": 3},
        {"by_label": {"focused": 1}, "online_count": None, "closed_count": None},
    ],
)
def test_from_dict_survives_a_dirty_payload(payload: dict[str, object]) -> None:
    """兜底口径与展示层其它函数一致：不为一条脏数据整体崩掉。"""
    summary = Summary.from_dict(payload)
    assert isinstance(summary, Summary)
    assert summary.online_count >= 0
    assert set(summary.by_label) == set(LABEL_ORDER)


def test_from_dict_of_a_non_mapping_is_an_empty_summary() -> None:
    for junk in (None, [], "nope", 42):
        summary = Summary.from_dict(junk)  # type: ignore[arg-type]
        assert summary.online_count == 0
        assert sum(summary.by_label.values()) == 0


def test_from_dict_clamps_negative_counts() -> None:
    """负数人数是脏数据；钳到 0 而不是让「在线 -3 人」流到界面上。"""
    summary = Summary.from_dict({"by_label": {}, "online_count": -5, "closed_count": -2})
    assert summary.online_count == 0
    assert summary.closed_count == 0


def test_from_dict_keeps_members_empty() -> None:
    """名单不在线路上（它只用于聚合），所以还原时一律空 —— 不假装有名单。"""
    summary = Summary.from_dict({"by_label": {"focused": 1}, "online_count": 1})
    assert all(not ids for ids in summary.members_by_label.values())


def test_from_dict_result_can_feed_the_trend_buffer() -> None:
    """端到端：payload 里的 summary → TrendBuffer → 序列。

    统一页的房间趋势图就是这么画的，所以这条链路必须真的通。
    """
    buffer = TrendBuffer(window=3)
    for focused in (1, 2, 3):
        buffer.push(
            Summary.from_dict(
                {
                    "by_label": {"focused": focused, "confused": 0},
                    "online_count": focused,
                    "closed_count": 0,
                }
            )
        )
    assert buffer.series("focused") == [1, 2, 3]
