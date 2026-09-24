"""串行集成主流程（C 主责）—— 任务计划 3.2「先跑通串行版」的落点。

把「一帧进来 → 三路智能体 → 融合 → 平滑 → 可分发状态」装配起来，并承担
日志与异常处理。链路与分工::

    (原始帧 | 感知流) → 三路智能体 → 融合 → 平滑 → ParticipantState（前端信封）

它**不替代** B 的 ``pipeline/``，两者语义不同：

============  ================================  ============================
              ``app/integration/``（本包）        ``pipeline/``（B 主责）
============  ================================  ============================
调度方式       串行、阻塞：一帧跑完再下一帧        多路并发 + 队列 + 丢过期帧
用途          CLI/演示可用性、联调与回归基线      200ms 指标与吞吐
依赖          零第三方依赖（CI 无 numpy）         numpy / torch / onnxruntime
============  ================================  ============================

串行版先行的理由见任务计划 §四 风险应对：「多进程框架受阻 → 先完成单线程串行
版本；B 在独立 feature 分支优化，不阻塞主线」。

入口::

    python -m app.integration --help
"""

from __future__ import annotations

from app.integration.session import AgentOutcome, FrameReport, SerialSession, neutral_env_context
from app.integration.sources import (
    TEXTURES,
    FrameSource,
    ListFrameSource,
    MockScenarioSource,
    PerceptionSource,
    SyntheticFrame,
    SyntheticFrameSource,
)

__all__ = [
    "TEXTURES",
    "AgentOutcome",
    "FrameReport",
    "FrameSource",
    "ListFrameSource",
    "MockScenarioSource",
    "PerceptionSource",
    "SerialSession",
    "SyntheticFrame",
    "SyntheticFrameSource",
    "neutral_env_context",
]
