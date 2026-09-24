"""展示逻辑层：**纯函数**，无 I/O、无 GUI、无第三方依赖。

这一层的存在理由是被 CI 逼出来的（见设计稿 §06.3 / 实施方案 §2.1）：
``pyproject.toml`` 的覆盖率 ``source`` 已包含 ``app``，门槛 80%，
而 CI 跑在**无显示环境**——Tkinter 窗口代码在 CI 里根本执行不到。
所以「能测的规则」全部推到这里，``app/client/`` 只留「读快照 → 调用本层 → 画」。
"""

from __future__ import annotations

from app.present.bubble import student_components, teacher_components
from app.present.colors import (
    DIM_COLOR,
    STATE_COLORS,
    color_for,
    color_for_count,
    color_for_key,
    is_dim,
)
from app.present.grid import MAX_GRID_COLUMNS, columns_for, grid_cells, sort_key
from app.present.summary import DISPLAY_LABELS, LABEL_ORDER, LABEL_TEXT, Summary, summarize
from app.present.visibility import visible_to

__all__ = [
    "DIM_COLOR",
    "DISPLAY_LABELS",
    "LABEL_ORDER",
    "LABEL_TEXT",
    "MAX_GRID_COLUMNS",
    "STATE_COLORS",
    "Summary",
    "color_for",
    "color_for_count",
    "color_for_key",
    "columns_for",
    "grid_cells",
    "is_dim",
    "sort_key",
    "student_components",
    "summarize",
    "teacher_components",
    "visible_to",
]
