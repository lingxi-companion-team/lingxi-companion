"""参与者状态变化检测（纯逻辑；供 client 做「哪一格刚变了」高亮）。

宫格每帧整体重绘，人眼无法分辨「哪一格的内容刚刚发生了变化」。这一层把前后
两帧的快照做差，产出**发生变化的 participant_id 集合**，client 据此给变化的
格子加一个短暂的高亮（纯画法，不在这里）。

比较口径（任一不同即视为变化）：

- 参与者新增 / 消失（按 ``participant_id``）；
- ``hidden`` 标志翻转；
- 有状态方的 ``label`` / ``stale`` 变化（``confidence`` 的微小抖动**不**算，
  否则高亮会每帧都闪，失去意义）。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.envelope import ParticipantState

__all__ = ["changed_participants"]


def _signature(p: ParticipantState) -> tuple[bool, object, object]:
    """把一个参与者压成可比较的签名（只含参与判定的字段）。"""
    label = p.state.label if p.state is not None else None
    stale = p.state.stale if p.state is not None else None
    return (p.hidden, label, stale)


def changed_participants(
    old: Iterable[ParticipantState],
    new: Iterable[ParticipantState],
) -> set[str]:
    """返回 ``new`` 相对 ``old`` 发生变化的 ``participant_id`` 集合。

    顺序无关；任一帧为空也可调用（空 → 全集或空集）。不参与比较的字段
    （``ts``、``confidence``）见模块 docstring。
    """
    old_map = {p.participant_id: _signature(p) for p in old}
    new_map = {p.participant_id: _signature(p) for p in new}
    changed: set[str] = set()
    for pid, sig in new_map.items():
        if old_map.get(pid) != sig:
            changed.add(pid)
    for pid in old_map:
        if pid not in new_map:
            changed.add(pid)
    return changed
