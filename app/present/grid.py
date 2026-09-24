"""共享宫格的过滤、排序与版面（纯逻辑）。

宫格是**所有端看到同一张**（腾讯会议式互相可见），差别只在教师端多一栏汇总。
「谁不占格」刻意用**数据判据**而不是身份判据 —— 详见 :func:`grid_cells`。
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from app.envelope import ParticipantState

__all__ = ["MAX_GRID_COLUMNS", "columns_for", "grid_cells", "sort_key"]

#: 宫格一行的列数上限。
#:
#: 设计稿未规定具体列数，8 是「一行还能一眼扫完」的实用上限：再多，格子会被压得
#: 很窄，中文状态名换行后更难读。
MAX_GRID_COLUMNS = 8


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


def columns_for(count: int, *, max_columns: int = MAX_GRID_COLUMNS) -> int:
    """给 ``count`` 个格子挑一个列数（``ceil(sqrt(n))``，再夹到 ``[1, max_columns]``）。

    ``ceil(sqrt(n))`` 是「最接近方阵」的经典取法：格子是正方形的，方阵的总周长最小、
    也最容易一眼扫完。人数很多时再夹到 :data:`MAX_GRID_COLUMNS`，避免横幅式一路排开。

    这是**纯几何布局**，没有业务语义 —— 放在这里只是因为它是可被 CI 完整测试的纯函数，
    而 ``app/client/`` 整体被覆盖率 ``omit``，规则不得下沉到那边（设计稿 §06.4）。
    """
    if count <= 0:
        return 1
    return max(1, min(max_columns, math.ceil(math.sqrt(count))))
