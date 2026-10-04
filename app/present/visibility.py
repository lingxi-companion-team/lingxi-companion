"""按观看者裁剪可见参与者（纯逻辑）。

规则
----
::

    房间规则 = 全员强制公开 → 所有人原样可见（个人开关被忽略）
    房间规则 = 个人自主选择 → 他人的「未公开」状态被掩去；自己永远看得见自己

**没有角色分支**。这是本次（v8，2026-10-01）按需求文档做的一处**有意删除**：
旧实现有一条 ``if viewer.is_teacher: return people``，让教师绕过所有人的隐私开关。
需求文档 §模块二.1 要求「界面统一化……仅通过权限区分功能可见性」，
§模块二.2 要求「可自主设置是否向房间公开自己的学习状态」，
§模块三.2 把发起人的杠杆定在**规则层面**（「全员强制公开 / 个人自主选择」）——
所以发起人的权力是**定规则**，不是**绕过隐私**。详见 :mod:`app.room`。

⚠️ 与设计稿 §01.1 伪代码的一处**有意的偏离**
------------------------------------------
伪代码写的是「过滤掉已隐藏者」（``participants = {p | ...}``），
但设计稿 §10.1 d2 又要求被隐藏者**仍占一格**、只是显示为「已隐藏」。两者不能同时成立 ——
真过滤掉就没人占格了。

故这里实现为**掩去状态（mask）而非移除条目**，返回的列表**长度与顺序都不变**；
配合 :attr:`app.envelope.ParticipantState.occupies_cell`（有状态 **或** 被隐藏才占格），
两条要求同时满足。

为什么坚持在**服务端**做而不是前端隐藏：若只下发全量、前端不画，
「未公开」就只是视觉上的 —— 对方的进程内存里照样有你的状态，抓个包就看到了。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.envelope import ParticipantState
from app.room import POLICY_OPTIONAL, RoomInfo

__all__ = ["HIDDEN_SCOPE_TEXT", "visible_to"]

#: 「未公开」开关的**用户可见文案**。
#:
#: 它放在这里（而不是某个 GUI 模块里）有两个理由：
#:
#: 1. 它描述的是 :func:`visible_to` 的行为，两者必须同步 —— 挨着放最不容易漂；
#: 2. 放在 ``app/present/`` 里就能被 CI 守住。GUI 层（``app/web/``）
#:    整体 omit 于覆盖率，把文案写在那边等于**没有任何测试兜底**。
#:
#: ⚠️ 历史教训（2026-10-02）：旧文案是「仅对同学隐藏（教师仍可见）」，
#: 那是 v7 的语义（教师绕过个人开关）。v8 删掉角色分支后，同一句话变成
#: **把保护范围说小**的误导性承诺。``tests/app/present/test_visibility.py``
#: 里有一条断言钉住「文案不得暗示存在能看到的人」。
HIDDEN_SCOPE_TEXT = "不向房间公开我的状态（仅本人可见）"


def visible_to(
    viewer: ParticipantState,
    everyone: Iterable[ParticipantState],
    *,
    room: RoomInfo | None = None,
) -> list[ParticipantState]:
    """返回观看者应看到的参与者列表（**长度与顺序不变**，仅可能掩去状态）。

    Args:
        viewer: 观看者自己。
        everyone: 房间内全部参与者。
        room: 房间元数据。``None`` 等价于「个人自主选择」—— 未建房间的裸 hub
            走最保守的一档（默认不公开），而不是最宽松的那档。

    Returns:
        与 ``everyone`` 等长等序的列表。

    三条性质：

    - **强制公开** 时原样返回（个人开关被忽略）；
    - **自主选择** 时看不到「未公开的他人」的状态；
    - **自己永远看得见自己** —— 否则开关一按，自己都不知道自己什么状态。
    """
    people = list(everyone)
    policy = room.publish_policy if room is not None else POLICY_OPTIONAL
    if policy != POLICY_OPTIONAL:
        # 强制公开（或将来新增的更宽松档）：不做任何掩去。
        return people
    return [
        (
            participant.masked()
            if participant.hidden and participant.participant_id != viewer.participant_id
            else participant
        )
        for participant in people
    ]
