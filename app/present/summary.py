"""教师端汇总指标（纯逻辑）。

两轮决策的落点：

- **D1**：不再有「不佳 / 良好」的二分口径。有几个状态就统计几个分量，
  所以这里产出的是 ``by_label`` + 每个状态的名单，而不是一个「不佳人数」。
- **D2**：分母是**当前在线人数**（``online_count``），不引入应到人数 / 花名册。

v7（2026-10-01）新增 ``closed_count``：v7 仪表盘把「已关闭感知」做成第 5 个 KPI 与
第 4 个图例分量，而它**不是一种情感**（是用户主动关掉采集，不是系统判定），
所以单独成一个计数，不塞进 ``by_label`` 的四个情感键里。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.envelope import ParticipantState
from common.perception_types import EMOTION_LABELS, EmotionLabel

__all__ = ["DISPLAY_LABELS", "LABEL_ORDER", "LABEL_TEXT", "Summary", "summarize"]

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

#: 状态的**中文展示名**（键 = 线路格式的 ``label``）。
#:
#: 文案取自设计稿 §04.1 的四色图例。之所以放在这里而不是客户端里：它是**展示规则**
#: （与配色同级），而 ``app/client/`` 整体 omit 于覆盖率 —— 规则写在那里就等于没有护栏。
#: 放这儿还能顺带保证「后端颜色 / 文案」与「前端渲染」用的是同一套键。
LABEL_TEXT: dict[str, str] = {
    EmotionLabel.FOCUSED.value: "专注",
    EmotionLabel.CONFUSED.value: "困惑",
    EmotionLabel.DISTRACTED.value: "分神",
    EmotionLabel.UNKNOWN.value: "未知",
}


@dataclass(frozen=True)
class Summary:
    """教师端汇总。字段全部是 JSON-ready 的普通容器。

    Attributes:
        by_label: 各状态人数，键取自 :data:`LABEL_ORDER`（4 个键恒存在）。
        ratio: 各状态占在线人数的比例；在线为 0 时全为 ``0.0``。
        online_count: **当前在线人数**（D2 的分母）。**含已关闭感知者** ——
            他们人在课堂里，只是没在采集。
        members_by_label: 各状态对应的参与者 id 列表，供气泡分量点击后展开。
        closed_count: 已关闭感知的人数（v7 仪表盘第 5 个 KPI）。**不计入**
            ``by_label`` —— ``by_label`` 的键是情感标签，而「已关闭」不是一种情感。

    不变式（``test_summary.py`` 钉住）::

        sum(by_label.values()) + closed_count == online_count

    ``ratio`` 的分母是 ``online_count``（含已关闭者），因此四个情感分量的比例之和
    等于 ``1 - closed_ratio`` —— 这正是「有 10% 的人关了感知，专注最多只能到 90%」
    这条事实的数学表达。
    """

    by_label: Mapping[str, int]
    ratio: Mapping[str, float]
    online_count: int
    members_by_label: Mapping[str, Sequence[str]]
    closed_count: int = 0

    @property
    def closed_ratio(self) -> float:
        """已关闭感知者占在线人数的比例；在线为 0 时为 ``0.0``。"""
        return self.closed_count / self.online_count if self.online_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        """转为线路格式。``Sequence`` 统一转 ``list``，避免序列化出元组。"""
        return {
            "by_label": dict(self.by_label),
            "ratio": dict(self.ratio),
            "online_count": self.online_count,
            "members_by_label": {key: list(ids) for key, ids in self.members_by_label.items()},
            "closed_count": self.closed_count,
            "closed_ratio": self.closed_ratio,
        }


def summarize(participants: Iterable[ParticipantState]) -> Summary:
    """统计汇总。

    统计口径是**传入的全部参与者**，包含「已对同学隐藏」的人 —— 这正是
    「教师端不受隐藏影响」的实现方式（验收项 A7）：它落在服务端的一份数据上，
    而不是客户端的一个开关。

    **已隐藏者也会被计入**，因为教师本该看得见他。至于学生视角，汇总根本不下发（A2）。

    ``online_count`` 计入两类人：**有状态的**与**已关闭感知的**。前者不消说；
    后者人在课堂里（只是没在采集），把他们排除会让「总在线」比实际人数少 ——
    而 v7 仪表盘的总在线人数正是这两类之和。

    「还没出结果的人」（``state is None`` 且未关闭）**不**计入：他们谈不上在线与否，
    计入会让分母虚高、各分量之和与分母对不上。
    """
    by_label: dict[str, int] = dict.fromkeys(LABEL_ORDER, 0)
    members: dict[str, list[str]] = {key: [] for key in LABEL_ORDER}
    online_count = 0
    closed_count = 0

    for participant in participants:
        if participant.closed:
            # 关闭采集者：计入在线，但不进 by_label —— 它的键是情感标签，
            # 而「已关闭感知」不是一种情感（归因是用户主动，不是系统判定）。
            closed_count += 1
            online_count += 1
            continue
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
        closed_count=closed_count,
    )
