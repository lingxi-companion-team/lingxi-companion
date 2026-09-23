"""按观看者裁剪可见参与者（D5，纯逻辑）。

规则（设计稿 §01.1）::

    viewer = teacher   → 全部（含已隐藏者），状态原样
    viewer = student V → 他人的 hidden 状态被掩去；自己永远看得见自己

⚠️ 与设计稿 §01.1 伪代码的一处**有意的偏离**
------------------------------------------
伪代码写的是「过滤掉已隐藏者」（``participants = {p | ...}``），
但设计稿 §10.1 d2 又要求被隐藏者**仍占一格**、只是显示为「已隐藏」。两者不能同时成立 ——
真过滤掉就没人占格了。

故这里实现为**掩去状态（mask）而非移除条目**，返回的列表**长度与顺序都不变**；
配合 :attr:`app.envelope.ParticipantState.occupies_cell`（有状态 **或** 被隐藏才占格），
两条要求同时满足。

为什么坚持在**服务端**做而不是前端隐藏：若只下发全量、前端不画，
「隐藏」就只是视觉上的 —— 对方的进程内存里照样有你的状态，抓个包就看到了。
这与「汇总只下发给教师」用的是同一条原则：不该有的数据，不要出现在不该出现的地方。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.envelope import ParticipantState

__all__ = ["visible_to"]


def visible_to(
    viewer: ParticipantState,
    everyone: Iterable[ParticipantState],
) -> list[ParticipantState]:
    """返回观看者应看到的参与者列表（**长度与顺序不变**，仅可能掩去状态）。

    三条性质对应验收项 A6 / A7：

    - 教师不受影响 —— 教师拿到的是原样列表（A7）；
    - 学生看不到「已隐藏的他人」的状态（A6-①）；
    - 学生**永远看得见自己** —— 否则开关一按，自己都不知道自己什么状态（A6-②）。
    """
    people = list(everyone)
    if viewer.is_teacher:
        return people
    return [
        (
            participant.masked()
            if participant.hidden and participant.participant_id != viewer.participant_id
            else participant
        )
        for participant in people
    ]
