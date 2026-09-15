"""空间协同决策：动态加权融合 + 三级置信度协商（含冲突识别）。

替代原先的等权多数投票占位实现。核心公式来自 ``docs/algorithm.md`` §3.1::

    S(ℓ) = Σ w_i(E) · P_i(ℓ)

实现要点
--------
1. **完整分布而非 top-1**：每个标签的得分来自**所有**通道的加权贡献
   （``PerceptionResult.prob_dist``）。若某路只填了标量 ``prob``，则退化为
   只在它的 top 标签上累加 ``w_i·prob`` —— 这与旧实现完全一致，用于兼容尚未
   改造的智能体。
2. **不缺路降级**：``results`` 里有两路就加权两路，只有一路就单路直出，
   一路都没有则返回 ``UNKNOWN``。融合层不关心某人的模块是否已交付。
3. **算法即协商**：文档里的协商级别不是几套独立逻辑，而是同一套加权计算的
   不同表现。因此这里统一先算权重、再加权，最后按结果判定命中了哪一级，
   避免「一级直出」和「二级加权」两套代码给出不一致答案。
4. **不强行下结论**：当首位与次位得分差距小于 ``conflict_margin`` 时，判定为
   ``conflict`` 并输出 ``UNKNOWN``。此时任何「选一个」都会引入与语义相关的
   系统性偏置（例如总是偏向按字典序靠前的 ``focused``），比拒判更糟。
5. **可解释**：``FusionOutput.reason`` 回填命中的级别，``weights`` 回填实际
   权重，前端与消融实验都能直接读取。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from common.config import DEFAULT_E0, DEFAULT_K, get, load_config
from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_EXPRESSION,
    EMOTION_LABELS,
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
#: 冲突：两路（或分布内部）得分过于接近，证据不足以定论 → 输出 UNKNOWN
REASON_CONFLICT = "conflict"
REASON_EMPTY = "empty"

#: 并列得分的确定性裁决顺序（与语义无关，仅避免结果随遍历顺序漂移）
_CANON_RANK = {label: index for index, label in enumerate(EMOTION_LABELS)}


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
            env: 环境上下文。``None`` 时按「环境未知」处理：取
                ``weights.e0`` 作为 $E$（此处两路权重相等），遮挡按 0 计。

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

        E = env.env_score if env is not None else self._neutral_E()
        occlusion = env.occlusion if env is not None else 0.0

        # 按 agent_id 去重：同一智能体重复上报时保留置信度最高的一路
        by_agent: dict[str, PerceptionResult] = {}
        for result in active:
            prev = by_agent.get(result.agent_id)
            if prev is None or result.confidence > prev.confidence:
                by_agent[result.agent_id] = result

        weights = self._resolve_weights(E, sorted(by_agent), occlusion)

        # 按标签聚合加权得分：S(ℓ) = Σ_i w_i · P_i(ℓ)
        scores: dict[EmotionLabel, float] = {}
        for agent_id, result in by_agent.items():
            weight = weights.get(agent_id, 0.0)
            if weight <= 0.0:
                continue
            if result.prob_dist:
                # 完整分布：本路对**每个**标签都投出 w_i · P_i(ℓ)
                for label, prob in result.prob_dist.items():
                    if prob > 0.0:
                        scores[label] = scores.get(label, 0.0) + weight * prob
            else:
                # 兼容降级：只有标量 prob 时，视作全部概率压在 top 标签上。
                # 这与旧的加权多数投票逐位等价。
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

        # 排序：先按得分降序，同分时按与语义无关的规范顺序，保证可复现
        ranked = sorted(
            scores.items(),
            key=lambda item: (-item[1], _CANON_RANK.get(item[0], len(_CANON_RANK))),
        )
        winner, top_score = ranked[0]
        confidence = min(1.0, top_score)
        margin = top_score - ranked[1][1] if len(ranked) > 1 else float("inf")

        reason = self._classify(
            E=E,
            occlusion=occlusion,
            confidence=confidence,
            margin=margin,
            by_agent=by_agent,
        )

        # 三级与冲突：置信度不足或无法定论时，等级再高也不强判
        label = (
            EmotionLabel.UNKNOWN if reason in (REASON_LOW_CONFIDENCE, REASON_CONFLICT) else winner
        )

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

    def _neutral_E(self) -> float:
        """环境未知（``env is None``）时采用的中性环境因子。

        取 ``weights.e0``（默认 0.6）而非固定 0.5 —— logistic 中心点处两路权重
        正好相等（0.5 : 0.5），是真正的「无信息」先验。若取 0.5，因 ``0.5 < E0``
        会偏向行为通道（约 0.31 : 0.69），等于在环境未知时**偷偷假设环境很差**。
        """
        return float(get("weights", "e0", default=DEFAULT_E0, config=self._config))

    def _resolve_weights(
        self,
        E: float,
        active_ids: list[str],
        occlusion: float,
    ) -> dict[str, float]:
        """计算实际使用的权重。

        遮挡严重时把表情权重压向 0 —— 此时人脸像素本身不可信，
        无论 E 多高都不该让表情主导判断。

        实现注意：一次「把某一路乘以系数、再整体归一化」在数学上等价于对该路
        施加**相对增益**，所以「压低表情」与「放大行为」是可以互相换算的
        （``gain ↔ 1/penalty``），并不存在「先压低再归一化会把惩罚抵消掉」的
        陷阱。真正会出错的是另外两件事：

        1. **忘记归一化** —— 权重之和不再为 1，``confidence`` 的绝对阈值失效；
        2. **两路同乘同一个系数** —— 比例不变，等于没做补偿。

        本实现选择「放大行为通道后归一化」，因为增益的作用方向更直观
        （遮挡越重 → 行为越被信任）。
        """
        weights = normalized_weights(
            E,
            active=[a for a in active_ids if a in (AGENT_EXPRESSION, AGENT_BEHAVIOR)],
            E0=float(get("weights", "e0", default=DEFAULT_E0, config=self._config)),
            k=float(get("weights", "k", default=DEFAULT_K, config=self._config)),
        )

        trigger = float(get("negotiation", "occlusion_trigger", default=0.5, config=self._config))
        if occlusion > trigger and AGENT_EXPRESSION in weights and AGENT_BEHAVIOR in weights:
            # 遮挡越严重，行为通道的相对增益越大。
            # 增益从 1.0（刚好到触发线）线性升到 1/0.05（完全遮挡）。
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
        margin: float,
        by_agent: Mapping[str, PerceptionResult],
    ) -> str:
        """判定命中了哪一级协商。

        判定优先级（高 → 低）：``low_confidence`` > ``conflict`` >
        ``consensus`` > ``reweight``。
        """
        low_conf = float(get("negotiation", "low_confidence", default=0.4, config=self._config))
        high_E = float(get("negotiation", "high_trust_E", default=0.8, config=self._config))
        trigger = float(get("negotiation", "occlusion_trigger", default=0.5, config=self._config))
        conflict_margin = float(
            get("negotiation", "conflict_margin", default=0.05, config=self._config)
        )

        # 三级优先：置信度不足时，等级再高也不能强判
        if confidence < low_conf:
            return REASON_LOW_CONFIDENCE

        # 冲突：首位与次位得分过于接近，任何「选一个」都属强行下结论。
        # 置于 consensus 之前 —— 即便两路 top 标签巧合一致，分数并列也说明
        # 证据无区分度。
        if margin < conflict_margin:
            return REASON_CONFLICT

        # 一级：环境良好 + 无遮挡 + 两路一致
        both_agree = len(by_agent) >= 2 and len({r.label for r in by_agent.values()}) == 1
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
    直接崩溃，会被逐步淘汰。平局时按 :data:`EMOTION_LABELS` 的规范顺序裁决，
    与 :meth:`FusionEngine.fuse` 保持一致。
    """
    if not results:
        return EmotionLabel.UNKNOWN
    counts: dict[EmotionLabel, int] = {}
    for result in results:
        counts[result.label] = counts.get(result.label, 0) + 1
    return max(
        counts,
        key=lambda label: (counts[label], -_CANON_RANK.get(label, len(_CANON_RANK))),
    )
