"""教师端汇总指标（纯逻辑）。

两轮决策的落点：

- **D1**：不再有「不佳 / 良好」的二分口径。有几个状态就统计几个分量，
  所以这里产出的是 ``by_label`` + 每个状态的名单，而不是一个「不佳人数」。
- **D2**：分母是**当前在线人数**（``online_count``），不引入应到人数 / 花名册。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.envelope import ParticipantState
from common.perception_types import EMOTION_LABELS, EmotionLabel

__all__ = ["DISPLAY_LABELS", "LABEL_ORDER", "Summary", "summarize"]

#: 展示用的 4 个状态，**顺序固定**（设计稿 §10.1 d1：0 人分量也保留占位，不随人数增减）。
#:
#: 注意 ``UNKNOWN`` **不在** ``EMOTION_LABELS`` 里 —— 它是「本帧未形成判定」的哨兵，
#: 按规定不得进入 ``prob_dist``。但展示层需要一个「未知」分量（学生没出结果时也得有个格子），
#: 所以在这里追加，并沿用规范顺序把它放在最后。
DISPLAY_LABELS: tuple[EmotionLabel, ...] = (*EMOTION_LABELS, EmotionLabel.UNKNOWN)

#: 线路格式里的键（字符串），与 ``EmotionLabel.value`` 同源。
#:
#: 用字符串而不是枚举当键，是为了让 payload 直接可 JSON 序列化，
#: 不必在传输层再转一道（少一层转换就少一类格式 bug）。
LABEL_ORDER: tuple[str, ...] = tuple(label.value for label in DISPLAY_LABELS)


@dataclass(frozen=True)
class Summary:
    """教师端汇总。字段全部是 JSON-ready 的普通容器。

    Attributes:
        by_label: 各状态人数，键取自 :data:`LABEL_ORDER`（4 个键恒存在）。
        ratio: 各状态占在线人数的比例；在线为 0 时全为 ``0.0``。
        online_count: **当前在线人数**（D2 的分母）。
        members_by_label: 各状态对应的参与者 id 列表，供气泡分量点击后展开。
    """

    by_label: Mapping[str, int]
    ratio: Mapping[str, float]
    online_count: int
    members_by_label: Mapping[str, Sequence[str]]

    def to_dict(self) -> dict[str, Any]:
        """转为线路格式。``Sequence`` 统一转 ``list``，避免序列化出元组。"""
        return {
            "by_label": dict(self.by_label),
            "ratio": dict(self.ratio),
            "online_count": self.online_count,
            "members_by_label": {key: list(ids) for key, ids in self.members_by_label.items()},
        }


def summarize(participants: Iterable[ParticipantState]) -> Summary:
    """统计汇总。

    统计口径是**传入的全部参与者**，包含「已对同学隐藏」的人 —— 这正是
    「教师端不受隐藏影响」的实现方式（验收项 A7）：它落在服务端的一份数据上，
    而不是客户端的一个开关。

    **已隐藏者也会被计入**，因为教师本该看得见他。至于学生视角，汇总根本不下发（A2）。

    只有「有状态」的参与者计入 ``online_count`` —— 还没出结果的人谈不上在线与否，
    计入会让分母虚高、各状态之和与分母对不上。
    """
    by_label: dict[str, int] = dict.fromkeys(LABEL_ORDER, 0)
    members: dict[str, list[str]] = {key: [] for key in LABEL_ORDER}
    online_count = 0

    for participant in participants:
        state = participant.state
        if state is None:
            continue
        online_count += 1
        key = state.label.value
        by_label[key] = by_label.get(key, 0) + 1
        members.setdefault(key, []).append(participant.participant_id)

    ratio = {
        key: (count / online_count if online_count else 0.0) for key, count in by_label.items()
    }
    return Summary(
        by_label=by_label,
        ratio=ratio,
        online_count=online_count,
        members_by_label=members,
    )
