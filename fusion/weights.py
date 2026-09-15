"""环境驱动的动态权重函数。

替代 ``docs/algorithm.md`` §3.2 原先的分段常数版本。分段常数在阈值处发生阶跃，
与文档「权重变化为连续过程而非开关切换」的表述矛盾——本模块用 logistic
函数实现真正的连续过渡。

数学形式::

    w_face(E)   = 1 / (1 + exp(-k * (E - E0)))
    w_behavior(E) = 1 - w_face(E)

即 logistic 的标准形式：环境越好（E 越大），表情权重越高。

默认 ``E0=0.6``、``k=8.0`` 时：

======  ==========  ============
E       w_face      w_behavior
======  ==========  ============
0.90    0.917       0.083
0.80    0.832       0.168
0.60    0.500       0.500
0.40    0.168       0.832
0.35    0.119       0.881
======  ==========  ============

覆盖了文档描述的「0.7:0.3 ↔ 0.3:0.7」区间，且处处连续可导。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from common.perception_types import AGENT_EXPRESSION

__all__ = ["DEFAULT_E0", "DEFAULT_K", "normalized_weights", "w_behavior", "w_face"]

#: logistic 中心点：E 等于该值时两路权重相等
DEFAULT_E0 = 0.6
#: logistic 斜率：越大过渡越陡，8.0 约对应 0.35~0.85 的过渡带
DEFAULT_K = 8.0


def w_face(E: float, E0: float = DEFAULT_E0, k: float = DEFAULT_K) -> float:
    """表情通道的环境权重。

    环境可信度越高（E 越大），人脸细节越可靠，表情权重越高。

    Args:
        E: 环境可信度因子，取值 [0, 1]。超出范围会被裁剪（不抛异常，
            因为权重函数处在数据流末端，宁可钳制也不要让整帧失败）。
        E0: logistic 中心点。
        k: logistic 斜率。

    Returns:
        ``w_face`` ∈ (0, 1)，环境越好取值越高。
    """
    E = min(1.0, max(0.0, float(E)))
    return 1.0 / (1.0 + math.exp(-k * (E - E0)))


def w_behavior(E: float, E0: float = DEFAULT_E0, k: float = DEFAULT_K) -> float:
    """行为通道的环境权重，与表情权重互补。"""
    return 1.0 - w_face(E, E0=E0, k=k)


def normalized_weights(
    E: float,
    active: Iterable[str] | None = None,
    E0: float = DEFAULT_E0,
    k: float = DEFAULT_K,
) -> dict[str, float]:
    """按实际在线的模态归一化权重。

    这一步是「风险互备」的落地点：若行为智能体尚未交付，融合层拿到
    ``active=["expression"]`` 时会自动把表情权重补到 1.0，而不需要任何
    ``if`` 分支判断——三条工作流因此可以真正并行，谁也不阻塞谁。

    Args:
        E: 环境可信度因子。
        active: 当前可用的 agent_id 集合。``None`` 表示两路都在。
        E0: logistic 中心点。
        k: logistic 斜率。

    Returns:
        ``{agent_id: weight}``，权重之和为 1。``active`` 为空时退化为
        全部给表情通道，保证下游永远拿得到可用权重。
    """
    from common.perception_types import AGENT_BEHAVIOR

    if active is None:
        active_set = {AGENT_EXPRESSION, AGENT_BEHAVIOR}
    else:
        active_set = set(active)

    raw: dict[str, float] = {}
    if AGENT_EXPRESSION in active_set:
        raw[AGENT_EXPRESSION] = w_face(E, E0=E0, k=k)
    if AGENT_BEHAVIOR in active_set:
        raw[AGENT_BEHAVIOR] = w_behavior(E, E0=E0, k=k)

    if not raw:
        return {AGENT_EXPRESSION: 1.0}

    total = sum(raw.values())
    if total <= 0.0:
        # 理论上不可达（两项均 > 0），保底避免除零
        return {agent: 1.0 / len(raw) for agent in raw}

    weights = {agent: value / total for agent, value in raw.items()}

    # 单通道时归一化后恒为 1.0，显式写回避免浮点误差
    if len(weights) == 1:
        only = next(iter(weights))
        weights[only] = 1.0
    return weights


def weights_from_config(
    E: float, config: Mapping, active: Iterable[str] | None = None
) -> dict[str, float]:
    """从 ``configs/thresholds.yaml`` 的 ``weights`` 段读取参数并计算。

    让实现方无需在代码里硬编码 E0 / k，调参只改配置文件。
    """
    section = (config or {}).get("weights", {}) if isinstance(config, Mapping) else {}
    E0 = float(section.get("e0", DEFAULT_E0))
    k = float(section.get("k", DEFAULT_K))
    return normalized_weights(E, active=active, E0=E0, k=k)
