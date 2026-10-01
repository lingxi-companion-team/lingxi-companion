"""``app.present.roster`` 的测试：行产出、筛选（决策 ④）、排序与新鲜度。"""

from __future__ import annotations

from app.envelope import ROLE_TEACHER, ParticipantState
from app.present.colors import CLOSED_COLOR
from app.present.roster import roster_rows
from app.present.summary import CLOSED_KEY, CLOSED_TEXT
from common.perception_types import EmotionLabel, FinalState


def _student(
    pid: str, label: EmotionLabel, *, ts: float = 1.0, stale: bool = False, hidden: bool = False
) -> ParticipantState:
    state = FinalState(label=label, timestamp=ts, confidence=0.7, stale=stale)
    return ParticipantState(pid, state=state, hidden=hidden)


def _closed(pid: str, *, hidden: bool = False) -> ParticipantState:
    return ParticipantState(pid, closed=True, hidden=hidden)


def _teacher() -> ParticipantState:
    return ParticipantState("t01", role=ROLE_TEACHER)


def test_rows_exclude_teacher() -> None:
    rows = roster_rows([_teacher(), _student("s01", EmotionLabel.FOCUSED)], now_ts=1.0)
    assert [row.participant_id for row in rows] == ["s01"]


def test_rows_are_sorted_by_id_by_default() -> None:
    people = [
        _student("s03", EmotionLabel.FOCUSED),
        _student("s01", EmotionLabel.CONFUSED),
        _student("s02", EmotionLabel.DISTRACTED),
    ]
    rows = roster_rows(people, now_ts=1.0)
    assert [row.participant_id for row in rows] == ["s01", "s02", "s03"]


def test_row_carries_label_color_and_confidence() -> None:
    rows = roster_rows([_student("s01", EmotionLabel.FOCUSED)], now_ts=1.0)
    row = rows[0]
    assert row.label_key == "focused"
    assert row.label_text == "专注"
    assert row.confidence == 0.7
    assert row.closed is False
    assert row.initial == "S"


def test_closed_participant_becomes_a_closed_row() -> None:
    rows = roster_rows([_closed("s09")], now_ts=1.0)
    row = rows[0]
    assert row.label_key == CLOSED_KEY
    assert row.label_text == CLOSED_TEXT
    assert row.color == CLOSED_COLOR
    assert row.confidence is None
    assert row.freshness == "stale"
    assert row.closed is True


def test_hidden_participant_still_gets_a_row() -> None:
    """已隐藏者对教师仍可见，故仍占一行（R5）。"""
    rows = roster_rows([_student("s01", EmotionLabel.FOCUSED, hidden=True)], now_ts=1.0)
    assert len(rows) == 1
    assert rows[0].hidden is True


def test_hidden_without_state_falls_back_to_unknown_row() -> None:
    """占格但本帧没有任何读数（隐藏且未出结果）→ 归到「未知」档，不单开一档。"""
    rows = roster_rows([ParticipantState("s07", hidden=True)], now_ts=1.0)
    assert len(rows) == 1
    row = rows[0]
    assert row.label_key == "unknown"
    assert row.label_text == "未知"
    assert row.confidence is None
    assert row.freshness == "stale"
    assert row.closed is False
    assert row.hidden is True


def test_freshness_tracks_age() -> None:
    people = [_student("s01", EmotionLabel.FOCUSED, ts=1.0)]
    assert roster_rows(people, now_ts=1.5)[0].freshness == "fresh"
    assert roster_rows(people, now_ts=10.0)[0].freshness == "aging"
    stale = [_student("s02", EmotionLabel.FOCUSED, stale=True)]
    assert roster_rows(stale, now_ts=1.0)[0].freshness == "stale"


# ── 筛选（决策 ④：右面板筛选真生效）────────────────────────────────────


def _mixed() -> list[ParticipantState]:
    return [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.CONFUSED),
        _student("s03", EmotionLabel.FOCUSED),
        _closed("s04"),
    ]


def test_filter_by_label_keeps_only_that_status() -> None:
    rows = roster_rows(_mixed(), now_ts=1.0, label_filter="focused")
    assert [row.participant_id for row in rows] == ["s01", "s03"]


def test_filter_by_closed_key() -> None:
    rows = roster_rows(_mixed(), now_ts=1.0, label_filter=CLOSED_KEY)
    assert [row.participant_id for row in rows] == ["s04"]


def test_filter_all_and_empty_mean_no_filter() -> None:
    assert len(roster_rows(_mixed(), now_ts=1.0, label_filter="all")) == 4
    assert len(roster_rows(_mixed(), now_ts=1.0, label_filter="")) == 4
    assert len(roster_rows(_mixed(), now_ts=1.0, label_filter=None)) == 4


def test_illegal_filter_key_falls_back_to_no_filter() -> None:
    """非法筛选键不抛错，按「不筛」处理（同展示层其它兜底约定）。"""
    rows = roster_rows(_mixed(), now_ts=1.0, label_filter="not-a-label")
    assert len(rows) == 4


def test_query_is_case_insensitive_substring() -> None:
    people = [_student("S01", EmotionLabel.FOCUSED), _student("x02", EmotionLabel.FOCUSED)]
    assert [row.participant_id for row in roster_rows(people, now_ts=1.0, query="s0")] == ["S01"]
    assert [row.participant_id for row in roster_rows(people, now_ts=1.0, query="S0")] == ["S01"]
    assert len(roster_rows(people, now_ts=1.0, query="  ")) == 2  # 空白 = 不筛


def test_filter_and_query_combine() -> None:
    rows = roster_rows(_mixed(), now_ts=1.0, label_filter="focused", query="s03")
    assert [row.participant_id for row in rows] == ["s03"]


def test_group_by_label_orders_by_status_then_id() -> None:
    rows = roster_rows(_mixed(), now_ts=1.0, group_by_label=True)
    # 专注(focused) 在前（s01, s03），困惑(confused) 次之，已关闭最后
    assert [row.participant_id for row in rows] == ["s01", "s03", "s02", "s04"]
