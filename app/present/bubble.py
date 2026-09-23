"""气泡内容生成（纯逻辑；D1：按状态分量展示，**不拼句子**）。

决策 D1 明确了「状态不佳成员 8/30」只是比喻，真正要展示的维度是**状态本身**：
有几个状态就几个分量，每个分量可单击查看细节。所以这一层产出的是**分量列表**，
而不是一句自然语言 —— 文案拼接留给将来可能的多语言/换皮需求，不在这里焊死。

- 学生气泡：**1 个分量**（本人状态 + 置信度）；
- 教师气泡：**4 个分量**（每个状态一个，带人数；0 人置灰但仍占位）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.present.colors import color_for_count, is_dim
from app.present.summary import DISPLAY_LABELS
from common.perception_types import FinalState

__all__ = ["student_components", "teacher_components"]


def student_components(state: FinalState) -> list[dict[str, Any]]:
    """学生气泡 = 本人状态这一个分量。

    本人只有一个状态，所以是 1 个分量。``stale`` 一并带出去 —— 契约层要求
    前端在 ``stale=True`` 时显示「维持中」而不是把状态当成新结果
    （见 ``perception_types.FinalState`` 的文档字符串）。
    """
    return [
        {
            "label": state.label.value,
            "count": 1,
            "color": color_for_count(state.label, 1),
            "dim": False,
            "confidence": state.confidence,
            "stale": state.stale,
        }
    ]


def teacher_components(by_label: Mapping[str, int]) -> list[dict[str, Any]]:
    """教师气泡 = 每个状态一个分量。

    分量数**恒为 4**、顺序**恒定**（设计稿 §10.1 d1）：人数为 0 的分量保留占位并置灰。
    固定数量是为了避免气泡宽度随人数变化而抖动 —— 教师扫一眼的连续几秒里，
    气泡忽宽忽窄会让人看不清。
    """
    components: list[dict[str, Any]] = []
    for label in DISPLAY_LABELS:
        key = label.value
        count = int(by_label.get(key, 0))
        components.append(
            {
                "label": key,
                "count": count,
                "color": color_for_count(label, count),
                "dim": is_dim(count),
            }
        )
    return components
