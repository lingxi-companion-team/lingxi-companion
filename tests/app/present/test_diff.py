"""``app.present.diff`` 的契约测试。

守住「哪一格刚变了」的判定口径：该检出的（新增/消失/hidden 翻转/label 变/
stale 变）一个不漏；不该检出的（confidence 抖动、ts 前进、纯重排）一个不报 ——
否则高亮会每帧都闪，这个功能就失去意义。
"""

from __future__ import annotations

from app.envelope import ParticipantState
from app.present.diff import changed_participants
from common.perception_types import EmotionLabel, FinalState


def _p(
    pid: str,
    *,
    label: EmotionLabel = EmotionLabel.FOCUSED,
    stale: bool = False,
    hidden: bool = False,
    confidence: float = 0.9,
    ts: float = 0.0,
    with_state: bool = True,
) -> ParticipantState:
    state = (
        FinalState(label=label, timestamp=ts, stale=stale, confidence=confidence)
        if with_state
        else None
    )
    return ParticipantState(participant_id=pid, state=state, hidden=hidden, ts=ts)


class TestMembershipChanges:
    def test_added_participant_is_changed(self) -> None:
        old = [_p("s01")]
        new = [_p("s01"), _p("s02")]
        assert changed_participants(old, new) == {"s02"}

    def test_removed_participant_is_changed(self) -> None:
        old = [_p("s01"), _p("s02")]
        new = [_p("s01")]
        assert changed_participants(old, new) == {"s02"}

    def test_no_change_returns_empty(self) -> None:
        old = [_p("s01"), _p("s02")]
        new = [_p("s01"), _p("s02")]
        assert changed_participants(old, new) == set()

    def test_reorder_is_not_a_change(self) -> None:
        # 宫格按 sort_key 重排是画法，不是状态变化 —— 不该触发高亮。
        old = [_p("s01"), _p("s02")]
        new = [_p("s02"), _p("s01")]
        assert changed_participants(old, new) == set()


class TestFieldChanges:
    def test_hidden_flip_is_changed(self) -> None:
        old = [_p("s01", hidden=False)]
        new = [_p("s01", hidden=True)]
        assert changed_participants(old, new) == {"s01"}

    def test_label_change_is_changed(self) -> None:
        old = [_p("s01", label=EmotionLabel.FOCUSED)]
        new = [_p("s01", label=EmotionLabel.DISTRACTED)]
        assert changed_participants(old, new) == {"s01"}

    def test_stale_flip_is_changed(self) -> None:
        old = [_p("s01", stale=False)]
        new = [_p("s01", stale=True)]
        assert changed_participants(old, new) == {"s01"}

    def test_confidence_jitter_is_not_a_change(self) -> None:
        # confidence 每帧都在抖，算变化会让高亮常亮 —— 明确排除。
        old = [_p("s01", confidence=0.90)]
        new = [_p("s01", confidence=0.87)]
        assert changed_participants(old, new) == set()

    def test_ts_advance_is_not_a_change(self) -> None:
        # ts 每帧都前进，同样不该触发高亮。
        old = [_p("s01", ts=1.0)]
        new = [_p("s01", ts=2.0)]
        assert changed_participants(old, new) == set()

    def test_state_appearing_is_changed(self) -> None:
        # 从「无结果」到「有结果」是真实变化（None -> 有 label）。
        old = [_p("s01", with_state=False)]
        new = [_p("s01", with_state=True)]
        assert changed_participants(old, new) == {"s01"}


class TestEdgeCases:
    def test_both_empty(self) -> None:
        assert changed_participants([], []) == set()

    def test_old_empty_means_everything_changed(self) -> None:
        assert changed_participants([], [_p("s01"), _p("s02")]) == {"s01", "s02"}

    def test_new_empty_means_everything_changed(self) -> None:
        assert changed_participants([_p("s01"), _p("s02")], []) == {"s01", "s02"}


def _wire(
    pid: str,
    *,
    label: str | None = "FOCUSED",
    stale: bool = False,
    hidden: bool = False,
    confidence: float = 0.9,
    ts: float = 0.0,
) -> dict[str, object]:
    """线路 dict —— payload 里 ``grid`` 的真实形态（``state`` 是**嵌套 dict**）。

    这个工厂是补上来的：``changed_participants`` 最早的测试**只**喂
    ``ParticipantState`` 对象，而 client 手上永远只有这种 dict ——
    于是 ``window.refresh`` 一跑就 ``AttributeError: 'dict' object has no
    attribute 'participant_id'``，而测试全绿。以下用例专门守住这条路。
    """
    state = (
        None
        if label is None
        else {
            "label": label,
            "stale": stale,
            "confidence": confidence,
            "timestamp": ts,
            "frame_id": "f1",
        }
    )
    return {"participant_id": pid, "hidden": hidden, "state": state, "ts": ts}


class TestWireDictInput:
    """线路 dict 路径 —— client 实际走的形态（回归护栏）。"""

    def test_wire_dicts_are_accepted(self) -> None:
        # 最早这里喂对象，测试绿、界面崩。现在这条路径必须自己立住。
        old = [_wire("s01", label="FOCUSED")]
        new = [_wire("s01", label="DISTRACTED")]
        assert changed_participants(old, new) == {"s01"}

    def test_wire_dicts_no_change(self) -> None:
        old = [_wire("s01"), _wire("s02", label="CONFUSED")]
        new = [_wire("s01"), _wire("s02", label="CONFUSED")]
        assert changed_participants(old, new) == set()

    def test_wire_dict_hidden_flip(self) -> None:
        old = [_wire("s01", hidden=False)]
        new = [_wire("s01", hidden=True)]
        assert changed_participants(old, new) == {"s01"}

    def test_wire_dict_state_appearing(self) -> None:
        # label=None ↔ 有 label：线路上的「无结果」正是 state=None。
        old = [_wire("s01", label=None)]
        new = [_wire("s01", label="FOCUSED")]
        assert changed_participants(old, new) == {"s01"}

    def test_wire_dict_confidence_and_ts_jitter_not_a_change(self) -> None:
        old = [_wire("s01", confidence=0.90, ts=1.0)]
        new = [_wire("s01", confidence=0.71, ts=9.9)]
        assert changed_participants(old, new) == set()

    def test_wire_dict_membership_change(self) -> None:
        old = [_wire("s01")]
        new = [_wire("s01"), _wire("s02")]
        assert changed_participants(old, new) == {"s02"}

    def test_objects_and_wire_dicts_can_be_mixed(self) -> None:
        # 混用也要对（签名口径一致，形态不该影响判定）。
        old = [_p("s01", label=EmotionLabel.FOCUSED)]
        new = [_wire("s01", label="FOCUSED"), _wire("s02")]
        assert changed_participants(old, new) == {"s01", "s02"}

    def test_malformed_wire_entry_does_not_raise(self) -> None:
        # 畸形 payload（缺字段/state 不是 dict）一律兜底，界面不能崩。
        assert changed_participants([{}], [{"participant_id": "s01"}]) == {"s01", "None"}
