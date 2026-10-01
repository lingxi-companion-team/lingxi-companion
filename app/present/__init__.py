"""展示逻辑层：**纯函数**，无 I/O、无 GUI、无第三方依赖。

这一层的存在理由是被 CI 逼出来的（见设计稿 §06.3 / 实施方案 §2.1）：
``pyproject.toml`` 的覆盖率 ``source`` 已包含 ``app``，门槛 80%，
而 CI 跑在**无显示环境**——Tkinter 窗口代码在 CI 里根本执行不到。
所以「能测的规则」全部推到这里，``app/client/`` 只留「读快照 → 调用本层 → 画」。
"""

from __future__ import annotations

from app.present.bubble import student_components, teacher_components
from app.present.colors import (
    CLOSED_COLOR,
    DIM_COLOR,
    STATE_COLORS,
    color_for,
    color_for_count,
    color_for_key,
    color_for_key_count,
    is_dim,
)
from app.present.diag import diag_lines
from app.present.diff import changed_participants
from app.present.grid import MAX_GRID_COLUMNS, columns_for, grid_cells, sort_key
from app.present.scale import (
    SCALE_COMPACT,
    SCALE_ROOMY,
    SCALE_STANDARD,
    WIDTH_COMPACT,
    WIDTH_ROOMY,
    CardGeometry,
    card_geometry,
    columns_at,
    scale_for_width,
)
from app.present.shape import STATE_SHAPES, shape_for, shape_for_key
from app.present.status import FRESH_AGING_SECONDS, freshness, freshness_key, offline
from app.present.summary import DISPLAY_LABELS, LABEL_ORDER, LABEL_TEXT, Summary, summarize
from app.present.visibility import visible_to

__all__ = [
    "CLOSED_COLOR",
    "DIM_COLOR",
    "DISPLAY_LABELS",
    "FRESH_AGING_SECONDS",
    "LABEL_ORDER",
    "LABEL_TEXT",
    "MAX_GRID_COLUMNS",
    "SCALE_COMPACT",
    "SCALE_ROOMY",
    "SCALE_STANDARD",
    "STATE_COLORS",
    "STATE_SHAPES",
    "Summary",
    "WIDTH_COMPACT",
    "WIDTH_ROOMY",
    "CardGeometry",
    "card_geometry",
    "changed_participants",
    "color_for",
    "color_for_count",
    "color_for_key",
    "color_for_key_count",
    "columns_at",
    "columns_for",
    "diag_lines",
    "freshness",
    "freshness_key",
    "grid_cells",
    "is_dim",
    "offline",
    "scale_for_width",
    "shape_for",
    "shape_for_key",
    "sort_key",
    "student_components",
    "summarize",
    "teacher_components",
    "visible_to",
]
