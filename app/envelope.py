"""参与者信封：把单机的感知结果升级为「可多端分发」的会话层对象。

为什么需要它（而不是给 ``FinalState`` 加字段）
--------------------------------------------
``common/perception_types.py`` 是**共享冻结层**，改一行需三方评审，
且 ``tests/test_contract.py`` 对 ``FinalState`` 的构造方式有位置参数断言。
参与者身份、角色、可见性都属于**会话层**，不属于感知层 —— 所以统一放在这层信封里，
``common/`` 与 ``fusion/`` 一个字节都不用改。

线路格式（``to_dict`` / ``from_dict`` 一对）
------------------------------------------
::

    {"participant_id": "s01", "role": "student", "hidden": false,
     "closed": false, "ts": 1.5,
     "state": {"label": "focused", "confidence": 0.87, "stale": false,
               "timestamp": 1.5, "frame_id": 3}}

``state`` 为 ``None`` 有**三种**含义，客户端靠 ``hidden`` / ``closed`` 区分：

- ``state is None`` 且两个标志都假 —— 该参与者本帧还没有结果（或它就是教师）；
- ``state is None`` 且 ``hidden is True`` —— **状态被按观看者掩去了**（见
  :mod:`app.present.visibility`）；
- ``state is None`` 且 ``closed is True`` —— 该参与者**主动关闭了感知采集**，
  压根没有产生过状态。

三种情形**都仍要占一个宫格**（设计稿 §10.1 d2 与 v7 仪表盘设计稿的「已关闭感知」格），
所以网格过滤的判据是 :attr:`ParticipantState.occupies_cell`，而不是单看 ``has_state``。

``hidden`` 与 ``closed`` 的区别（**不要混用**）
----------------------------------------------
============  ============================  ======================
              ``hidden``                    ``closed``
============  ============================  ======================
采集          **仍在采集**                   停止采集
可见性        对同学掩去状态，教师仍可见       本来就没有状态可看
占格          占                          占
文案          「已隐藏」                    「已关闭感知」
============  ============================  ======================

两者可以同时为真（关闭后仍保持隐藏开关），此时按「已关闭感知」呈现 —— 关闭是更强的事实。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from common.perception_types import EmotionLabel, FinalState

__all__ = [
    "ROLE_STUDENT",
    "ROLE_TEACHER",
    "ROLES",
    "ParticipantState",
]

#: 角色常量。字符串约定与 ``perception_types`` 里 ``AGENT_*`` 的写法保持一致。
ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLES = (ROLE_STUDENT, ROLE_TEACHER)

#: ``FinalState`` 线路格式里必需携带的字段。
_STATE_FIELDS = ("label", "confidence", "stale", "timestamp", "frame_id")


def _state_to_dict(state: FinalState) -> dict[str, Any]:
    """``FinalState`` → 线路格式。

    ``weights`` 刻意不下发：它是调试用的中间量，展示层用不到，
    少一个字段就少一处需要同步的格式。
    """
    return {
        "label": state.label.value,
        "confidence": state.confidence,
        "stale": state.stale,
        "timestamp": state.timestamp,
        "frame_id": state.frame_id,
    }


def _state_from_dict(raw: Mapping[str, Any]) -> FinalState:
    """线路格式 → ``FinalState``；字段缺失或标签非法一律抛 ``ValueError``。"""
    missing = [name for name in _STATE_FIELDS if name not in raw]
    if missing:
        raise ValueError(f"state missing required field(s): {', '.join(missing)}")
    try:
        label = EmotionLabel(str(raw["label"]))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in EmotionLabel)
        raise ValueError(f"unknown label {raw['label']!r}; allowed: {allowed}") from exc
    return FinalState(
        label=label,
        timestamp=float(raw["timestamp"]),
        stale=bool(raw["stale"]),
        confidence=float(raw["confidence"]),
        frame_id=int(raw["frame_id"]),
    )


@dataclass(frozen=True)
class ParticipantState:
    """一个参与者在某一时刻的可分发状态。

    Attributes:
        participant_id: 会话内唯一标识（用编号/昵称，**不要用真实姓名**）。
        role: :data:`ROLE_STUDENT` 或 :data:`ROLE_TEACHER`。
        state: 该参与者的稳定感知结果；教师恒为 ``None``。
        hidden: 是否对同学隐藏自己的状态（教师端不受影响）。**仍在采集**。
        ts: 该条状态的时间戳，用于判断新鲜度。
        closed: 是否**主动关闭了感知采集**。与 ``hidden`` 语义不同（见模块
            docstring 的对照表）：``hidden`` 是「采了但不给同学看」，``closed``
            是「压根没采」。关闭者 ``state`` 恒为 ``None``。

    ``closed`` 为什么是独立字段而不是复用 ``hidden`` 或 ``UNKNOWN``
    ------------------------------------------------------------
    - 复用 ``hidden``：会推翻 R5 那条「仅对同学隐藏（教师仍可见）」的硬文案 ——
      关闭采集与对同学隐藏是**两件不同的事**，教师看得到「已隐藏」的状态，
      但看不到「已关闭」的任何状态（因为没有）；
    - 复用 ``EmotionLabel.UNKNOWN``：``UNKNOWN`` 是「本帧未形成判定」（系统没测出来），
      而 ``closed`` 是「用户主动关掉」（系统被要求不测）—— 归因完全相反，
      挤进同一个标签会让教师误判。
    """

    participant_id: str
    role: str = ROLE_STUDENT
    state: FinalState | None = None
    hidden: bool = False
    ts: float = 0.0
    closed: bool = False

    def __post_init__(self) -> None:
        if not self.participant_id:
            raise ValueError("participant_id must not be empty")
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")
        if self.ts < 0.0:
            raise ValueError("ts must not be negative")

    @property
    def is_teacher(self) -> bool:
        return self.role == ROLE_TEACHER

    @property
    def has_state(self) -> bool:
        """是否携带可展示的状态。教师恒为 ``False``。"""
        return self.state is not None

    @property
    def occupies_cell(self) -> bool:
        """是否在宫格中占一格。

        判据是「有状态 **或** 被隐藏 **或** 已关闭感知」：后两者的状态虽不可见，
        仍要占格并显示为「已隐藏」/「已关闭感知」，否则宫格布局会随同学反复开关
        而重排（设计稿 §10.1 d2）。
        """
        return self.has_state or self.hidden or self.closed

    def masked(self) -> ParticipantState:
        """抹掉状态、保留占格 —— 供「对同学隐藏」时生成他人视角。"""
        return replace(self, state=None)

    def with_hidden(self, hidden: bool) -> ParticipantState:
        """改写可见性开关（其余字段不变）。"""
        return replace(self, hidden=hidden)

    def with_closed(self, closed: bool) -> ParticipantState:
        """改写「已关闭感知」开关（其余字段不变）。

        关闭采集时调用方应同时把 ``state`` 置空 —— 但本方法**不代劳**：
        它只改标志，让「标志与状态」的一致性由调用方显式表达，避免这里藏一条
        隐式副作用。真正停止采集的编排在会话侧（``app.integration``）。
        """
        return replace(self, closed=closed)

    def to_dict(self) -> dict[str, Any]:
        """转为线路格式（JSON 可直接序列化）。"""
        return {
            "participant_id": self.participant_id,
            "role": self.role,
            "hidden": self.hidden,
            "closed": self.closed,
            "ts": self.ts,
            "state": None if self.state is None else _state_to_dict(self.state),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ParticipantState:
        """从线路格式还原，并断言必需字段完备。"""
        missing = [name for name in ("participant_id", "role") if name not in payload]
        if missing:
            raise ValueError(f"missing required field(s): {', '.join(missing)}")
        raw_state = payload.get("state")
        return cls(
            participant_id=str(payload["participant_id"]),
            role=str(payload["role"]),
            state=None if raw_state is None else _state_from_dict(raw_state),
            hidden=bool(payload.get("hidden", False)),
            ts=float(payload.get("ts", 0.0)),
            closed=bool(payload.get("closed", False)),
        )
