"""参与者状态变化检测（纯逻辑；供 client 做「哪一格刚变了」高亮）。

宫格每帧整体重绘，人眼无法分辨「哪一格的内容刚刚发生了变化」。这一层把前后
两帧的快照做差，产出**发生变化的 participant_id 集合**，client 据此给变化的
格子加一个短暂的高亮（纯画法，不在这里）。

比较口径（任一不同即视为变化）：

- 参与者新增 / 消失（按 ``participant_id``）；
- ``hidden`` 标志翻转；
- 有状态方的 ``label`` / ``stale`` 变化（``confidence`` 的微小抖动**不**算，
  否则高亮会每帧都闪，失去意义）。

两种输入形态
------------
``ParticipantState`` 对象（信封层）与**线路 dict**（payload 里 ``grid`` 的条目，
形如 ``{"participant_id", "hidden", "state": {...}}``）都支持。

client 手上只有线路 dict —— 它拿到的是 ``/snapshot`` 的 JSON，不会再重建
``ParticipantState``（重建要么依赖信封的字段顺序，要么让线路格式变了而对象
没跟着变）。所以这里直接认 dict，而不是逼调用方转一道手。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["changed_participants"]


def _state_fields(state: Any) -> tuple[object, object]:
    """从 ``FinalState`` 对象 / state dict / ``None`` 里取出 ``(label, stale)``。"""
    if state is None:
        return (None, None)
    if isinstance(state, Mapping):
        return (state.get("label"), state.get("stale"))
    return (getattr(state, "label", None), getattr(state, "stale", None))


def _signature(p: Any) -> tuple[str, bool, object, object]:
    """把一个参与者压成可比较的签名（只含参与判定的字段）。

    对象与 dict 走同一条抽取路径：都读 ``participant_id`` / ``hidden`` /
    ``state.label`` / ``state.stale``。缺字段一律按「没有」处理、不抛错 ——
    畸形 payload 不该让界面崩掉（同 ``present`` 其他模块的兜底约定）。
    """
    if isinstance(p, Mapping):
        pid = p.get("participant_id")
        hidden = bool(p.get("hidden"))
        label, stale = _state_fields(p.get("state"))
    else:
        pid = getattr(p, "participant_id", None)
        hidden = bool(getattr(p, "hidden", False))
        label, stale = _state_fields(getattr(p, "state", None))
    return (str(pid), hidden, label, stale)


def changed_participants(old: Iterable[Any], new: Iterable[Any]) -> set[str]:
    """返回 ``new`` 相对 ``old`` 发生变化的 ``participant_id`` 集合。

    顺序无关；任一帧为空也可调用（空 → 全集或空集）。不参与比较的字段
    （``ts``、``confidence``）见模块 docstring。输入元素可以是
    ``ParticipantState`` 对象或线路 dict，两者可混用。
    """
    old_map = {_signature(p)[0]: _signature(p) for p in old}
    new_map = {_signature(p)[0]: _signature(p) for p in new}
    changed: set[str] = set()
    for pid, sig in new_map.items():
        if old_map.get(pid) != sig:
            changed.add(pid)
    for pid in old_map:
        if pid not in new_map:
            changed.add(pid)
    return changed
