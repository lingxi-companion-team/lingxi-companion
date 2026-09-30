"""状态的几何形状编码（纯逻辑；颜色之外的第二编码通道）。

现有设计只靠颜色 + 4 字中文区分四种状态。色觉异常（男性约 8%，28 人班期望
值约 1 人）下，「专注蓝」与「分神红」可能不可区分。形状是一个零成本、与颜色
正交的冗余通道：同一状态永远同一形状，扫一眼即可分辨。

形状语义（与状态性质对应，便于记忆）：

- 专注 → ``round``（稳定、收敛）；
- 困惑 → ``triangle``（有棱角、需要关注）；
- 分神 → ``diamond``（游离、指向外）；
- 未知/无结果 → ``flat``（平线，表示「无信号」）。
"""

from __future__ import annotations

from common.perception_types import EmotionLabel

__all__ = ["STATE_SHAPES", "shape_for", "shape_for_key"]

#: 状态 → 形状名。键覆盖 ``EmotionLabel`` 全集（含 UNKNOWN），与
#: ``colors.STATE_COLORS`` 保持同样的完备性约定。
STATE_SHAPES: dict[EmotionLabel, str] = {
    EmotionLabel.FOCUSED: "round",
    EmotionLabel.CONFUSED: "triangle",
    EmotionLabel.DISTRACTED: "diamond",
    EmotionLabel.UNKNOWN: "flat",
}

#: 兜底形状：未登记 label 不抛错（与 ``colors.color_for`` 的兜底约定一致）。
_FALLBACK_SHAPE = "flat"


def shape_for(label: EmotionLabel) -> str:
    """返回 ``label`` 对应的形状名；未登记时退回 :data:`_FALLBACK_SHAPE`。"""
    return STATE_SHAPES.get(label, _FALLBACK_SHAPE)


def shape_for_key(label: str) -> str:
    """线路字符串键入口（payload 里 label 是 ``str`` 不是枚举）。

    与 :func:`colors.color_for_key` 同一套兜底策略：解析失败退回兜底形状，
    不让一个脏值把前端画崩。
    """
    try:
        return shape_for(EmotionLabel(label))
    except ValueError:
        return _FALLBACK_SHAPE
