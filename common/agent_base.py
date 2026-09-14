"""感知智能体协议。

三个智能体（表情 / 行为 / 环境）都实现本协议。它是三人**并行开发的解耦点**：
只要实现符合协议，任何模块都能被单独实例化并测试，无需另外两人的代码存在。

约定
----
1. ``agent_id`` 必须是 :data:`common.perception_types.AGENT_EXPRESSION` /
   :data:`~common.perception_types.AGENT_BEHAVIOR` /
   :data:`~common.perception_types.AGENT_ENV` 之一。
2. ``infer()`` **不得抛出异常**。任何内部失败都应降级为
   :class:`~common.perception_types.EmotionLabel.UNKNOWN`（环境智能体则返回
   中性 ``EnvContext``），让融合层继续工作而不是整体崩溃。
3. 实现方不需要关心线程安全：调度由 ``pipeline/`` 负责，同一实例不会被并发调用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from common.perception_types import EnvContext, PerceptionResult

__all__ = ["EnvAgent", "PerceptionAgent"]


class PerceptionAgent(ABC):
    """产出情感判定的智能体（表情 / 行为）。

    最小实现示例::

        class MyAgent(PerceptionAgent):
            agent_id = AGENT_EXPRESSION

            def warmup(self) -> None:
                self._model = load_model()

            def infer(self, frame, ts, frame_id) -> PerceptionResult:
                try:
                    label, prob = self._model(frame)
                except Exception:
                    return self._unknown(ts, frame_id)
                return PerceptionResult(label, prob, prob, self.agent_id, ts, frame_id)
    """

    #: 子类必须覆写为 AGENT_EXPRESSION 或 AGENT_BEHAVIOR
    agent_id: str = ""

    @abstractmethod
    def warmup(self) -> None:
        """加载模型、初始化资源。集成方在启动时调用一次。"""

    @abstractmethod
    def infer(self, frame: Any, ts: float, frame_id: int) -> PerceptionResult:
        """对单帧做推理。

        Args:
            frame: 采集侧提供的原始帧（实现方自行决定如何解析；
                建议同时接受 numpy 数组与 ``None``，后者用于单元测试）。
            ts: 帧时间戳（秒，单调递增）。
            frame_id: 帧序号，同一帧的多路结果必须一致。

        Returns:
            本智能体的判定结果。失败时返回 ``UNKNOWN``，不要抛异常。
        """

    def close(self) -> None:
        """释放资源。默认空实现，子类按需覆写。"""

    def _unknown(self, ts: float, frame_id: int) -> PerceptionResult:
        """构造降级用的「不确定」结果，供子类在异常分支复用。"""
        return PerceptionResult(
            label=_unknown_label(),
            prob=0.0,
            confidence=0.0,
            agent_id=self.agent_id or "unknown",
            ts=ts,
            frame_id=frame_id,
        )


class EnvAgent(ABC):
    """评估环境质量的智能体。

    与 :class:`PerceptionAgent` 分开定义，因为它的输出是
    :class:`~common.perception_types.EnvContext`（效度因子）而不是情感标签，
    两者语义不同，强行合并会让类型标注失去意义。
    """

    agent_id = "env"

    @abstractmethod
    def warmup(self) -> None:
        """加载模型或初始化资源。集成方在启动时调用一次。"""

    @abstractmethod
    def assess(self, frame: Any, ts: float, frame_id: int) -> EnvContext:
        """评估单帧的环境质量。

        Returns:
            环境上下文。失败时返回中性值（``env_score=0.5``），不要抛异常。
        """

    def close(self) -> None:
        """释放资源。默认空实现，子类按需覆写。"""

    def _neutral(self, ts: float, frame_id: int) -> EnvContext:
        """构造中性环境值，供子类在异常分支复用。

        取 0.5 而非 0.0 或 1.0：既不谎称环境良好（避免表情权重被错误抬高），
        也不谎称环境恶劣（避免无端降权）。
        """
        return EnvContext(
            brightness=0.5,
            blur=0.5,
            env_score=0.5,
            occlusion=0.0,
            ts=ts,
            frame_id=frame_id,
        )


def _unknown_label():
    """延迟导入，避免模块级循环依赖。"""
    from common.perception_types import EmotionLabel

    return EmotionLabel.UNKNOWN
