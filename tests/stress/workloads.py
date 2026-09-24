"""压测载荷：把「要压什么」定义一次，测试与基准脚本共用。

为什么不把载荷写进测试文件
--------------------------
同一套载荷有两个消费者：

* ``tests/stress/test_*.py`` —— 用**预算断言**守住「不许数量级退化」；
* ``tests/stress/benchmark.py`` —— 把实测数字打印成 Markdown 表，写进
  ``docs/reports/`` 的报告。

若两处各写一份，报告里的数字与 CI 守的那条线就会慢慢分叉（改了一边忘另一边），
报告也就失去了「这是当前代码的实测」的意义。故统一放这里。

三个载荷的层级
--------------
1. :func:`env_agent_step` —— C 的模块单点（采样 → 度量 → 合成 → 协议适配）；
2. :func:`fusion_step` —— 融合层单点（mock 场景 → 加权/协商）；
3. :func:`serial_chain_step` —— **最小端到端**：三路串联（环境真实 + 两路假）
   → 融合 → 平滑。

第 3 条是当前能构造出的最完整链路：表情与行为两路尚未交付真实实现，用
``common/mock`` 的假智能体顶上。它同时也是「三方并行」的回归线 ——
一旦某一环的接口变了而这里跟着变慢或变错，压测会先于联调发现。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents.env import SAMPLE_COLS, SAMPLE_ROWS, RuleBasedEnvAgent, sample_luma
from common.mock import generator
from common.perception_types import AGENT_BEHAVIOR, AGENT_EXPRESSION, EmotionLabel
from fusion.fusion_engine import FusionEngine
from fusion.temporal_smoother import TemporalSmoother
from tests.stress.stresskit import SyntheticFrame, Timing, measure

__all__ = [
    "env_agent_step",
    "fusion_step",
    "sample_only_step",
    "serial_chain_step",
    "bench_env_agent",
    "bench_fusion",
    "bench_sample_only",
    "bench_serial_chain",
]


def _counter() -> Callable[[], int]:
    """闭包式的自增计数器（避免 ``itertools.count`` 在热路径里多一层对象）。"""
    state = [0]

    def tick() -> int:
        state[0] += 1
        return state[0]

    return tick


def env_agent_step(
    *,
    height: int = 480,
    width: int = 640,
    texture: str = "noise",
    agent: RuleBasedEnvAgent | None = None,
) -> Callable[[], Any]:
    """返回一个「评估一帧」的零参调用，可反复调用进行测量。

    ``frame_id`` / ``ts`` 单调递增，避免任何「按帧号缓存」的实现被白送加速。
    """
    instance = agent if agent is not None else RuleBasedEnvAgent()
    instance.warmup()
    frame = SyntheticFrame(height=height, width=width, texture=texture)
    tick = _counter()

    def step() -> Any:
        index = tick()
        return instance.assess(frame, index * 0.1, index)

    return step


def sample_only_step(
    *,
    height: int = 480,
    width: int = 640,
    texture: str = "noise",
) -> Callable[[], Any]:
    """返回一个「只做降采样」的零参调用 —— 不含任何度量与合成。

    **用途是把载体成本从算法成本里剥离出来**：``SyntheticFrame`` 的像素是纯 Python
    算出来的（每次访问一次整数散列），而真帧是 numpy 数组、单点访问快两个数量级。
    若不做这一步分解，压测报告里的绝对数字会被合成帧的访问成本主导，看起来像是
    「环境智能体要 2 ms」，容易得出错误结论。

    于是报告里的口径是：**评估耗时 = 采样（本载荷） + 度量与合成（相减）**。
    """
    frame = SyntheticFrame(height=height, width=width, texture=texture)
    return lambda: sample_luma(frame, cols=SAMPLE_COLS, rows=SAMPLE_ROWS)


def fusion_step(*, scenario: str = "consensus") -> Callable[[], Any]:
    """返回一个「融合一帧」的零参调用（载荷来自 mock 场景，确定且已归一化）。"""
    engine = FusionEngine()
    perceptions, env = generator.scenario_frame(scenario)

    def step() -> Any:
        return engine.fuse(perceptions, env)

    return step


def serial_chain_step(
    *,
    height: int = 480,
    width: int = 640,
    texture: str = "noise",
    smoother: TemporalSmoother | None = None,
) -> Callable[[], Any]:
    """返回一个「跑完一次串行链路」的零参调用。

    链路：环境智能体（真实）→ 表情 / 行为（假智能体）→ 融合 → 平滑。

    注意两路假智能体是**恒输出**的，因此这条链路的耗时主要来自环境侧的图像
    度量和融合层；等真实模型接入后，本载荷的绝对值会变，但**基线对比的方法**
    与报告表格都还能接着用。
    """
    env_agent = RuleBasedEnvAgent()
    env_agent.warmup()
    expression = generator.fake_agent(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.85)
    behavior = generator.fake_agent(AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.80)
    engine = FusionEngine()
    smoother = smoother if smoother is not None else TemporalSmoother()
    frame = SyntheticFrame(height=height, width=width, texture=texture)
    tick = _counter()

    def step() -> Any:
        index = tick()
        ts = index * 0.1
        env = env_agent.assess(frame, ts, index)
        results = [expression.infer(frame, ts, index), behavior.infer(frame, ts, index)]
        fused = engine.fuse(results, env)
        return smoother.update(
            fused.label,
            confidence=fused.confidence,
            weights=fused.weights,
            ts=fused.ts,
            frame_id=fused.frame_id,
        )

    return step


def bench_env_agent(*, iterations: int = 300, **kwargs: Any) -> Timing:
    """测「评估一帧」的中位耗时。``kwargs`` 透传给 :func:`env_agent_step`。"""
    return measure(env_agent_step(**kwargs), iterations=iterations)


def bench_sample_only(*, iterations: int = 2000, **kwargs: Any) -> Timing:
    """测「只做降采样」的中位耗时（载体成本）。"""
    return measure(sample_only_step(**kwargs), iterations=iterations)


def bench_fusion(*, iterations: int = 2000, **kwargs: Any) -> Timing:
    """测「融合一帧」的中位耗时。"""
    return measure(fusion_step(**kwargs), iterations=iterations)


def bench_serial_chain(*, iterations: int = 1000, **kwargs: Any) -> Timing:
    """测「串行链路一帧」的中位耗时。"""
    return measure(serial_chain_step(**kwargs), iterations=iterations)
