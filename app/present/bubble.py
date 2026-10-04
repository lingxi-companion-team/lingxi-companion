"""气泡内容生成（纯逻辑；D1：按状态分量展示，**不拼句子**）。

决策 D1 明确了「状态不佳成员 8/30」只是比喻，真正要展示的维度是**状态本身**：
有几个状态就几个分量，每个分量可单击查看细节。所以这一层产出的是**分量列表**，
而不是一句自然语言 —— 文案拼接留给将来可能的多语言/换皮需求，不在这里焊死。

v8（2026-10-01）改名
--------------------
原先叫 ``student_components`` / ``teacher_components``。需求文档要求「不区分教师端和
学生端」，而这两个名字把「谁看」焊进了函数名 —— 于是同一个「房间聚合分量」
在教师那儿叫 teacher、在学生那儿根本不下发。现在改成**按内容命名**：

- :func:`personal_components` —— **个人分量**（本人状态 + 置信度，1 个）；
- :func:`aggregate_components` —— **房间聚合分量**（每个状态一个，带人数）。

页面给谁看由调用方决定（现在是**所有人**都拿得到聚合），函数名不再预设身份。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.present.colors import color_for_count, is_dim
from app.present.summary import DISPLAY_LABELS
from common.perception_types import FinalState

__all__ = ["aggregate_components", "personal_components"]


def personal_components(state: FinalState) -> list[dict[str, Any]]:
    """个人分量 = 本人状态这一个分量。

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


def aggregate_components(by_label: Mapping[str, int]) -> list[dict[str, Any]]:
    """房间聚合分量 = 每个状态一个分量。

    分量数**恒为 4**、顺序**恒定**（设计稿 §10.1 d1）：人数为 0 的分量保留占位并置灰。
    固定数量是为了避免气泡宽度随人数变化而抖动 —— 扫一眼的连续几秒里，
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
