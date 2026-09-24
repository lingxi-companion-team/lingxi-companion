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

from common.config import DEFAULT_E0, get
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
            frame: 采集侧提供的原始帧。**格式已由 :mod:`common.input_spec` 定死**
                （BGR / ``uint8`` / ``[0, 255]`` / ``(H, W, 3)`` / 不缩放不裁剪），
                不要再各自假设一种格式；需要人脸 ROI 就在本方法内部先裁。
                ``None`` 是无帧哨兵，用于单测与 mock —— 此时走降级分支，**不得抛异常**。
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

        Args:
            frame: 同 :meth:`PerceptionAgent.infer` —— 规格见 :mod:`common.input_spec`；
                降采样等预处理在本方法内部完成。

        Returns:
            环境上下文。失败时返回中性值（``env_score = weights.e0``，
            见 :meth:`_neutral`），不要抛异常。
        """

    def close(self) -> None:
        """释放资源。默认空实现，子类按需覆写。"""

    def _neutral(self, ts: float, frame_id: int) -> EnvContext:
        """构造中性环境值，供子类在异常分支复用。

        ``env_score``（即融合层的环境因子 ``E``）取 ``weights.e0``（默认 0.6）
        而非固定 0.5：``e0`` 是 logistic 中心点，只有 ``E = e0`` 时两路权重才
        相等（0.5 : 0.5），才是真正的「无信息」先验。若取 0.5，因 ``0.5 < e0``
        会算成约 0.31 : 0.69，等于在评估失败时**偷偷假设环境很差**，把判断权
        单方面让给行为通道。

        这与 :meth:`fusion.fusion_engine.FusionEngine._neutral_E` 取同一个值，
        两条降级路径（融合层拿不到 ``EnvContext``、环境智能体自身评估失败）
        必须给出同样的中性先验，否则同一份「无信息」输入会因走哪条路径而不同。

        ``brightness`` / ``blur`` 仍取 0.5：它们是原始观测量，「中等」即无倾向，
        不参与权重转移，与 ``e0`` 是两回事。
        ``occlusion`` 取 0.0：不得谎称存在遮挡，否则会额外触发一次环境降权。
        """
        return EnvContext(
            brightness=0.5,
            blur=0.5,
            env_score=float(get("weights", "e0", default=DEFAULT_E0)),
            occlusion=0.0,
            ts=ts,
            frame_id=frame_id,
        )


def _unknown_label():
    """延迟导入，避免模块级循环依赖。"""
    from common.perception_types import EmotionLabel

    return EmotionLabel.UNKNOWN
