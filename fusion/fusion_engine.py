"""空间协同决策：动态加权融合 + 三级置信度协商。

替代原先的等权多数投票占位实现。核心公式来自 ``docs/algorithm.md`` §3.1::

    S = Σ w_i(E) · P_i

实现要点
--------
1. **不缺路降级**：``results`` 里有两路就加权两路，只有一路就单路直出，
   一路都没有则返回 ``UNKNOWN``。融合层不关心某人的模块是否已交付。
2. **算法即协商**：文档里的三级协商不是三套独立逻辑，而是同一套加权计算的
   三种表现。因此这里统一先算权重、再加权，最后按结果判定命中了哪一级，
   避免「一级直出」和「二级加权」两套代码给出不一致答案。
3. **可解释**：``FusionOutput.reason`` 回填命中的级别，``weights`` 回填实际
   权重，前端与消融实验都能直接读取。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from common.config import get, load_config
from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_EXPRESSION,
    EmotionLabel,
    EnvContext,
    FusionOutput,
    PerceptionResult,
)
from fusion.weights import normalized_weights

__all__ = ["FusionEngine", "majority_label"]

#: 协商级别标识，写入 FusionOutput.reason
REASON_CONSENSUS = "consensus"
REASON_REWEIGHT = "reweight"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_EMPTY = "empty"


class FusionEngine:
    """把多路感知结果融合为单一瞬时判定。

    无状态、可复用。线程安全性由调用方保证（同一实例不被并发调用）。

    Args:
        config: 配置字典。``None`` 时从 ``configs/thresholds.yaml`` 读取。
    """

    def __init__(self, config: Mapping | None = None) -> None:
        self._config = dict(config) if config is not None else load_config()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def fuse(
        self,
        results: Sequence[PerceptionResult],
        env: EnvContext | None = None,
    ) -> FusionOutput:
        """融合多路感知结果。

        Args:
            results: 各智能体对**同一帧**的输出。允许缺少任意路。
            env: 环境上下文。``None`` 时按「环境未知」处理，使用中性权重。

        Returns:
            瞬时融合结果，含标签、置信度、实际权重与命中的协商级别。
        """
        active = [r for r in results if r.label is not EmotionLabel.UNKNOWN]
        ts = env.ts if env is not None else 0.0
        frame_id = env.frame_id if env is not None else 0

        if not active:
            return FusionOutput(
                label=EmotionLabel.UNKNOWN,
                confidence=0.0,
                weights={},
                reason=REASON_EMPTY,
                ts=ts,
                frame_id=frame_id,
            )

        E = env.env_score if env is not None else 0.5
        occlusion = env.occlusion if env is not None else 0.0

        # 按 agent_id 去重：同一智能体重复上报时保留置信度最高的一路
        by_agent: dict[str, PerceptionResult] = {}
        for result in active:
            prev = by_agent.get(result.agent_id)
            if prev is None or result.confidence > prev.confidence:
                by_agent[result.agent_id] = result

        weights = self._resolve_weights(E, sorted(by_agent), occlusion)

        # 按标签聚合加权得分：S(label) = Σ_i w_i · P_i(label)
        scores: dict[EmotionLabel, float] = {}
        for agent_id, result in by_agent.items():
            weight = weights.get(agent_id, 0.0)
            if weight <= 0.0:
                continue
            scores[result.label] = scores.get(result.label, 0.0) + weight * result.prob

        if not scores:
            return FusionOutput(
                label=EmotionLabel.UNKNOWN,
                confidence=0.0,
                weights=weights,
                reason=REASON_EMPTY,
                ts=ts,
                frame_id=frame_id,
            )

        winner = max(scores, key=lambda label: (scores[label], label.value))
        confidence = min(1.0, scores[winner])

        reason = self._classify(
            E=E,
            occlusion=occlusion,
            confidence=confidence,
            by_agent=by_agent,
        )

        # 三级：置信度不足时，等级再高也不强判
        label = EmotionLabel.UNKNOWN if reason == REASON_LOW_CONFIDENCE else winner

        return FusionOutput(
            label=label,
            confidence=confidence,
            weights=weights,
            reason=reason,
            ts=ts,
            frame_id=frame_id,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _resolve_weights(
        self,
        E: float,
        active_ids: list[str],
        occlusion: float,
    ) -> dict[str, float]:
        """计算实际使用的权重。

        遮挡严重时把表情权重压向 0 —— 此时人脸像素本身不可信，
        无论 E 多高都不该让表情主导判断。

        实现注意：**不能**先压低表情再整体归一化。归一化会把惩罚抵消掉
        （压低 a 后总和 &lt; 1，再除以总和等于把两路同时放大，比例回到原点）。
        正确做法是放大行为通道，让比例真正发生转移。
        """
        weights = normalized_weights(
            E,
            active=[a for a in active_ids if a in (AGENT_EXPRESSION, AGENT_BEHAVIOR)],
            E0=float(get("weights", "e0", default=0.6, config=self._config)),
            k=float(get("weights", "k", default=8.0, config=self._config)),
        )

        trigger = float(
            get("negotiation", "occlusion_trigger", default=0.5, config=self._config)
        )
        if occlusion > trigger and AGENT_EXPRESSION in weights and AGENT_BEHAVIOR in weights:
            # 遮挡越严重，行为通道的相对增益越大。
            # 增益从 1.0（刚好到触发线）线性升到 1/occlusion_penalty（完全遮挡）。
            excess = (occlusion - trigger) / max(1e-6, 1.0 - trigger)
            gain = 1.0 / max(0.05, 1.0 - excess)
            weights[AGENT_BEHAVIOR] *= gain

            # 归一化后比例真正转移，且总和仍为 1
            total = sum(weights.values())
            if total > 0:
                weights = {agent: value / total for agent, value in weights.items()}

        return weights

    def _classify(
        self,
        *,
        E: float,
        occlusion: float,
        confidence: float,
        by_agent: Mapping[str, PerceptionResult],
    ) -> str:
        """判定命中了哪一级协商。"""
        low_conf = float(
            get("negotiation", "low_confidence", default=0.4, config=self._config)
        )
        high_E = float(
            get("negotiation", "high_trust_E", default=0.8, config=self._config)
        )
        trigger = float(
            get("negotiation", "occlusion_trigger", default=0.5, config=self._config)
        )

        # 三级优先：置信度不足时，等级再高也不能强判
        if confidence < low_conf:
            return REASON_LOW_CONFIDENCE

        # 一级：环境良好 + 无遮挡 + 两路一致
        both_agree = (
            len(by_agent) >= 2
            and len({r.label for r in by_agent.values()}) == 1
        )
        if E > high_E and both_agree and occlusion <= trigger:
            return REASON_CONSENSUS

        # 二级：环境驱动降权（环境差、有遮挡，或两路不一致需要消解）
        return REASON_REWEIGHT


# ----------------------------------------------------------------------
# 向后兼容：旧调用方的平滑迁移路径
# ----------------------------------------------------------------------


def majority_label(results: list[PerceptionResult]) -> EmotionLabel:
    """等权多数投票（**兼容保留，不参与主流程**）。

    新代码请使用 :meth:`FusionEngine.fuse`。此函数保留仅为避免旧调用方
    直接崩溃，会被逐步淘汰。
    """
    if not results:
        return EmotionLabel.UNKNOWN
    counts: dict[EmotionLabel, int] = {}
    for result in results:
        counts[result.label] = counts.get(result.label, 0) + 1
    return max(counts, key=lambda label: (counts[label], label.value))
