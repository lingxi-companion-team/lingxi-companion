"""统一感知契约。

本文件是三人并行开发的**唯一接口基线**，冻结后任何修改必须：
1. 提交到 ``chore/interface-*`` 分支；
2. 附带兼容性说明与三方回归测试；
3. 经三人评审通过。

命名约定（全局唯一，禁止别名）::

    AGENT_EXPRESSION = "expression"   表情智能体
    AGENT_BEHAVIOR   = "behavior"     行为智能体
    AGENT_ENV        = "env"          环境智能体

文档中出现的 ``face`` / ``pose`` 均为历史写法，一律对应到
``expression`` / ``behavior``，不要在新代码中使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

__all__ = [
    "AGENT_BEHAVIOR",
    "AGENT_ENV",
    "AGENT_EXPRESSION",
    "AGENT_IDS",
    "EmotionLabel",
    "EnvContext",
    "FinalState",
    "FusionOutput",
    "PerceptionResult",
]

# --------------------------------------------------------------------------
# 智能体标识：全系统唯一的字符串约定
# --------------------------------------------------------------------------

AGENT_EXPRESSION = "expression"
AGENT_BEHAVIOR = "behavior"
AGENT_ENV = "env"

#: 参与情感融合的感知智能体（env 是效度评估器，不是情感判定源）
AGENT_IDS = (AGENT_EXPRESSION, AGENT_BEHAVIOR)


class EmotionLabel(str, Enum):
    """三个智能体共享的统一标签体系（4 类，M1 后冻结）。

    与说明文档的对应关系：

    - ``FOCUSED``    → "持续专注"
    - ``CONFUSED``   → "瞬时困惑"
    - ``DISTRACTED`` → "可能分心"
    - ``UNKNOWN``    → "不确定"（置信度过低或输入无效时的安全输出）

    教学场景若只需要「专注 / 非专注」二分类，请在**输出层**做映射，
    不要在智能体层裁剪标签空间。
    """

    FOCUSED = "focused"
    CONFUSED = "confused"
    DISTRACTED = "distracted"
    UNKNOWN = "unknown"


def _check_unit_range(**values: float) -> None:
    """校验若干取值均落在 [0, 1] 闭区间内。"""
    for name, value in values.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1, got {value!r}")


@dataclass(frozen=True)
class PerceptionResult:
    """单个感知智能体的输出（表情 / 行为共用）。

    ``ts`` 与 ``frame_id`` 由**采集侧**统一赋值，三个智能体必须回填同一帧的
    相同值，否则下游丢帧与延迟统计会失真。
    """

    label: EmotionLabel
    prob: float
    confidence: float
    agent_id: str
    ts: float = 0.0
    frame_id: int = 0

    def __post_init__(self) -> None:
        _check_unit_range(prob=self.prob, confidence=self.confidence)
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if self.frame_id < 0:
            raise ValueError("frame_id must not be negative")


@dataclass(frozen=True)
class EnvContext:
    """环境智能体输出，供融合层做权重转移。

    ``env_score`` 即算法文档中的环境可信度因子 ``E``。

    ``occlusion`` 为遮挡程度，越大表示人脸被遮挡越严重，用于触发
    三级协商中的「环境驱动降权」。
    """

    brightness: float
    blur: float
    env_score: float
    occlusion: float = 0.0
    ts: float = 0.0
    frame_id: int = 0

    def __post_init__(self) -> None:
        _check_unit_range(
            brightness=self.brightness,
            blur=self.blur,
            env_score=self.env_score,
            occlusion=self.occlusion,
        )
        if self.frame_id < 0:
            raise ValueError("frame_id must not be negative")


@dataclass(frozen=True)
class FusionOutput:
    """融合层的瞬时输出（尚未经过时序平滑）。

    ``weights`` 的 key 必须取自 :data:`AGENT_EXPRESSION` / :data:`AGENT_BEHAVIOR`，
    与 :class:`PerceptionResult` 的 ``agent_id`` 同源，便于前端直接按
    agent_id 关联展示。

    ``reason`` 记录本次协商命中的级别，取值 ``"consensus"`` / ``"reweight"``
    / ``"low_confidence"``，供调试与前端标注，不参与任何计算。
    """

    label: EmotionLabel
    confidence: float
    weights: Mapping[str, float] = field(default_factory=dict)
    reason: str = ""
    ts: float = 0.0
    frame_id: int = 0

    def __post_init__(self) -> None:
        _check_unit_range(confidence=self.confidence)


@dataclass(frozen=True)
class FinalState:
    """时序平滑后的稳定输出，交给前端展示。

    ``stale`` 为 True 表示本帧未达到确认票数，``label`` 沿用上一个稳定状态。
    前端应据此显示「维持中」，而不是把状态变化当作新结果。
    """

    label: EmotionLabel
    timestamp: float
    stale: bool = False
    confidence: float = 0.0
    weights: Mapping[str, float] = field(default_factory=dict)
    frame_id: int = 0
