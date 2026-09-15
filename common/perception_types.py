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

修订记录
--------
``2026-09-15`` —— :class:`PerceptionResult` 新增 ``prob_dist`` 字段。

- **动机**：融合层原先只能拿到每路的 top 标签概率，无法实现算法文档 §3.1 的
  ``S(ℓ) = Σ w_i·P_i(ℓ)``，实际退化为加权多数投票。
- **兼容性**：**完全向后兼容**。新字段带默认值 ``{}``，既有构造方式
  （位置参数前 4 个、关键字参数 ``ts`` / ``frame_id``）全部不受影响；
  ``prob_dist`` 为空时融合层走旧的降级路径。
- **新增约束**：``prob_dist`` 非空时必须满足「键为 ``EmotionLabel``、
  取值 ∈ [0,1]、总和为 1、包含 ``label``、且 ``prob == prob_dist[label]``」，
  且**不得包含** ``UNKNOWN``（``UNKNOWN`` 是哨兵而非情感类别，详见
  :data:`EMOTION_LABELS`）。
- **回归覆盖**：``tests/test_contract.py``（契约守卫）与
  ``tests/fusion/test_fusion.py``（两条路径的行为一致性）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "AGENT_BEHAVIOR",
    "AGENT_ENV",
    "AGENT_EXPRESSION",
    "AGENT_IDS",
    "EMOTION_LABELS",
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


#: **情感标签的规范顺序**（不含 ``UNKNOWN``）：``FOCUSED`` / ``CONFUSED`` / ``DISTRACTED``。
#:
#: 两个用途：
#: 1. ``prob_dist`` 只能以这三个标签为键 —— ``UNKNOWN`` 是「本帧未形成判定」的
#:    哨兵值，不是一种情感，因此不能出现在概率分布里（详见
#:    :meth:`PerceptionResult._validate_prob_dist`）。
#: 2. 融合层在得分完全并列时需要一个**与语义无关的确定性**裁决顺序，
#:    以免结果随字典遍历顺序漂移。注意：该顺序仅用于打破「数值完全相等」的
#:    退化情形，正常路径由 ``negotiation.conflict_margin`` 判定，不依赖此顺序。
EMOTION_LABELS = (EmotionLabel.FOCUSED, EmotionLabel.CONFUSED, EmotionLabel.DISTRACTED)


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

    ``prob`` 与 ``prob_dist`` 的关系
    --------------------------------
    - ``prob`` 是**单一标量**：本路对 ``label`` 这个标签的概率。
    - ``prob_dist`` 是**完整分布** ``{EmotionLabel: 概率}``，供融合层实现
      算法文档 §3.1 的 ``S(ℓ) = Σ w_i(E)·P_i(ℓ)``。

    为什么必须同时有两者：仅有 ``prob`` 时，融合层只能知道「本路选了哪个标签」，
    无法知道「本路给其他标签多少概率」，于是退化为**加权多数投票** —— 即
    第二名候选的票数被完全丢弃。这对「两路各自都有一点犹豫」的情形会给出
    过于自信的结论。完整分布让每个标签都能收到**所有**通道的加权贡献。

    ``prob_dist`` 的约束（非空时强制校验，见 :meth:`_validate_prob_dist`）：

    - 键必须是 :class:`EmotionLabel`，且**不含** ``UNKNOWN``（见 :data:`EMOTION_LABELS`）；
    - 取值 ∈ [0, 1]，总和为 1（容差 ``1e-3``）；
    - 必须包含 ``label``，且 ``prob == prob_dist[label]``（容差 ``1e-6``）。

    ``prob_dist`` 为空（默认）时，融合层回退到只使用 ``prob`` 的旧行为 ——
    这是为尚未改造的智能体保留的兼容路径。新代码应始终填充 ``prob_dist``。
    """

    label: EmotionLabel
    prob: float
    confidence: float
    agent_id: str
    ts: float = 0.0
    frame_id: int = 0
    prob_dist: Mapping[EmotionLabel, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_unit_range(prob=self.prob, confidence=self.confidence)
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if self.frame_id < 0:
            raise ValueError("frame_id must not be negative")
        if self.prob_dist:
            self._validate_prob_dist()

    def _validate_prob_dist(self) -> None:
        """校验完整分布：键集、取值范围、归一化、与 ``prob`` 的一致性。

        归一化必须校验 —— 若各通道的分布未归一化，加权得分 $S$ 的尺度会漂移，
        ``negotiation.low_confidence`` 这个绝对阈值就失去意义。事实上当各路
        分布都归一化、且权重之和为 1 时，$S$ 本身也是一个归一化的分布
        （$\\sum_\\ell S(\\ell) = 1$），这正是 §3.1 数学模型的成立前提。

        ``UNKNOWN`` 不允许作为键：它是「本帧未形成判定」的哨兵，不是情感类别。
        若把不确定度塞进 ``prob_dist``，$S$ 将不再是情感分布，argmax 可能选中
        ``UNKNOWN`` 而使「置信度」失去可比性。需要表达整路弃权时，请直接把
        ``label`` 置为 ``UNKNOWN``（融合层会过滤该路）。
        """
        normalized: dict[EmotionLabel, float] = {}
        for key, value in self.prob_dist.items():
            if not isinstance(key, EmotionLabel):
                raise ValueError(f"prob_dist keys must be EmotionLabel, got {type(key).__name__}")
            if key is EmotionLabel.UNKNOWN:
                raise ValueError(
                    "prob_dist must not contain UNKNOWN; set label=UNKNOWN to abstain instead"
                )
            _check_unit_range(**{f"prob_dist[{key.value}]": float(value)})
            normalized[key] = float(value)

        total = sum(normalized.values())
        if abs(total - 1.0) > 1e-3:
            raise ValueError(f"prob_dist must sum to 1, got {total!r}")

        if self.label not in normalized:
            raise ValueError("prob_dist must contain the reported label")
        if abs(normalized[self.label] - self.prob) > 1e-6:
            raise ValueError(
                f"prob ({self.prob!r}) must equal prob_dist[{self.label.value}] "
                f"({normalized[self.label]!r})"
            )

        # frozen dataclass 内部赋值须绕开 __setattr__
        object.__setattr__(self, "prob_dist", normalized)


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
    / ``"low_confidence"`` / ``"conflict"`` / ``"empty"``，供调试与前端标注，
    不参与任何计算。判定优先级：``low_confidence`` > ``conflict`` >
    ``consensus`` > ``reweight``。
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
