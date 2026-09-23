"""共享宫格的过滤与排序（纯逻辑）。

宫格是**所有端看到同一张**（腾讯会议式互相可见），差别只在教师端多一栏汇总。
「谁不占格」刻意用**数据判据**而不是身份判据 —— 详见 :func:`grid_cells`。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.envelope import ParticipantState

__all__ = ["grid_cells", "sort_key"]


def sort_key(participant: ParticipantState) -> tuple[int, str]:
    """排序键：先角色档位、再 ``participant_id`` 字典序。

    教师排最后只是为了让「万一出现教师」时次序仍然确定可复现；正常宫格里不会有教师格，
    因为教师没有状态、也不占格（设计稿 §01）。真正的稳定性来自第二步的 id 字典序 ——
    人数变化不会让格子乱跳。
    """
    return (1 if participant.is_teacher else 0, participant.participant_id)


def grid_cells(participants: Iterable[ParticipantState]) -> list[ParticipantState]:
    """挑出应该在宫格里占格的参与者。

    判据是 :attr:`app.envelope.ParticipantState.occupies_cell`（**有状态 或 被隐藏**），
    **不是** ``role != "teacher"``。

    为什么强调这一点：将来若做「转移主持人」，角色是会变的，写死 ``role`` 的判据会在转移后
    全部失效；而「有没有可展示的东西」是数据事实，不受身份变化影响（设计稿 §01 的 note）。
    附带效果是教师被自然排除 —— 教师 ``state`` 恒为 ``None`` 且不隐藏。
    """
    return sorted((p for p in participants if p.occupies_cell), key=sort_key)
