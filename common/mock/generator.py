"""Mock 数据生成器。

为本地集成与单元测试提供**确定性**输入流，覆盖融合与平滑的各种边界情形。
零第三方依赖（只用标准库），因此任何人在任何环境下都能跑。

设计原则
--------
1. **确定性**：不使用随机数。同一场景每次产出相同结果，便于断言。
2. **覆盖边界**：共识、冲突、弱光、低置信、遮挡、单通道、抖动，七类情形。
3. **可自解释**：每个场景都带 ``note`` 说明预期行为，测试直接读取。

场景清单见 :data:`SCENARIOS`。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_ENV,
    AGENT_EXPRESSION,
    EmotionLabel,
    EnvContext,
    PerceptionResult,
)

__all__ = [
    "SCENARIOS",
    "Scenario",
    "fake_agent",
    "fake_env_agent",
    "get_scenario",
    "sample_stream",
    "scenario_frame",
]


@dataclass(frozen=True)
class Scenario:
    """一个完整的测试场景。

    Attributes:
        name: 场景标识，用于 ``get_scenario`` 检索。
        note: 预期行为说明。
        frames: 帧序列。每帧是 ``(感知结果列表, 环境上下文)``。
             感知结果列表**允许缺路**，用于测试降级。
    """

    name: str
    note: str
    frames: list[tuple[list[PerceptionResult], EnvContext]] = field(default_factory=list)


def _frame(
    results: list[tuple[str, EmotionLabel, float]],
    *,
    E: float,
    brightness: float = 0.8,
    blur: float = 0.2,
    occlusion: float = 0.0,
    frame_id: int = 1,
) -> tuple[list[PerceptionResult], EnvContext]:
    """构造一帧：感知结果 + 环境上下文。"""
    ts = frame_id * 0.1  # 假定 10 FPS，便于断言时间戳
    perceptions = [
        PerceptionResult(label, prob, prob, agent_id, ts=ts, frame_id=frame_id)
        for agent_id, label, prob in results
    ]
    env = EnvContext(
        brightness=brightness,
        blur=blur,
        env_score=E,
        occlusion=occlusion,
        ts=ts,
        frame_id=frame_id,
    )
    return perceptions, env


# ----------------------------------------------------------------------
# 七个场景
# ----------------------------------------------------------------------

SCENARIOS: list[Scenario] = [
    Scenario(
        name="consensus",
        note="环境良好 + 两路一致 → 一级协商，直接输出共识（FOCUSED）",
        frames=[
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.92),
                    (AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.88),
                ],
                E=0.92,
                brightness=0.9,
                blur=0.1,
                frame_id=i,
            )
            for i in range(1, 5)
        ],
    ),
    Scenario(
        name="conflict",
        note="环境良好但两路冲突 → 二级协商，表情权重占优，判定随表情",
        frames=[
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.75),
                    (AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.70),
                ],
                E=0.85,
                brightness=0.85,
                blur=0.15,
                frame_id=i,
            )
            for i in range(1, 5)
        ],
    ),
    Scenario(
        name="low_light",
        note="弱光 → 二级协商触发权重反转，行为权重超过表情",
        frames=[
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.60),
                    (AGENT_BEHAVIOR, EmotionLabel.DISTRACTED, 0.65),
                ],
                E=0.30,
                brightness=0.22,
                blur=0.55,
                frame_id=i,
            )
            for i in range(1, 5)
        ],
    ),
    Scenario(
        name="low_confidence",
        note="最高概率低于 0.4 → 三级协商，输出 UNKNOWN 而不强判",
        frames=[
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.35),
                    (AGENT_BEHAVIOR, EmotionLabel.DISTRACTED, 0.33),
                ],
                E=0.70,
                brightness=0.7,
                blur=0.3,
                frame_id=i,
            )
            for i in range(1, 5)
        ],
    ),
    Scenario(
        name="occluded",
        note="人脸遮挡 0.95 → 表情权重被压制（0.25 : 0.75），改由行为主导，判定为 CONFUSED",
        frames=[
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.88),
                    (AGENT_BEHAVIOR, EmotionLabel.CONFUSED, 0.86),
                ],
                E=0.75,
                brightness=0.8,
                blur=0.2,
                occlusion=0.95,
                frame_id=i,
            )
            for i in range(1, 5)
        ],
    ),
    Scenario(
        name="single_channel",
        note="只有表情一路（行为智能体未交付）→ 融合层单路直出，不报错",
        frames=[
            _frame(
                [(AGENT_EXPRESSION, EmotionLabel.DISTRACTED, 0.70)],
                E=0.60,
                brightness=0.6,
                blur=0.3,
                frame_id=i,
            )
            for i in range(1, 5)
        ],
    ),
    Scenario(
        name="jitter",
        note=(
            "先确认 FOCUSED，随后三帧标签互不相同（F/C/D）导致窗口无多数 → "
            "平滑层不确认，保持 FOCUSED 并置 stale=True"
        ),
        frames=[
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.80),
                    (AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.78),
                ],
                E=0.9,
                frame_id=1,
            ),
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.80),
                    (AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.78),
                ],
                E=0.9,
                frame_id=2,
            ),
            # 以下三帧构造「无多数」窗口：F / C / D 各一票，
            # 但必须让融合层输出的瞬时标签可控，故直接控制两路标签组合。
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.90),
                    (AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.90),
                ],
                E=0.9,
                frame_id=3,
            ),
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.CONFUSED, 0.90),
                    (AGENT_BEHAVIOR, EmotionLabel.CONFUSED, 0.90),
                ],
                E=0.9,
                frame_id=4,
            ),
            _frame(
                [
                    (AGENT_EXPRESSION, EmotionLabel.DISTRACTED, 0.90),
                    (AGENT_BEHAVIOR, EmotionLabel.DISTRACTED, 0.90),
                ],
                E=0.9,
                frame_id=5,
            ),
        ],
    ),
]

_BY_NAME = {s.name: s for s in SCENARIOS}


def get_scenario(name: str) -> Scenario:
    """按名称取场景，不存在时抛 KeyError 并列出可用名称。"""
    try:
        return _BY_NAME[name]
    except KeyError:
        available = ", ".join(sorted(_BY_NAME))
        raise KeyError(f"unknown scenario {name!r}; available: {available}") from None


def scenario_frame(
    name: str, index: int = 0
) -> tuple[list[PerceptionResult], EnvContext]:
    """直接取某场景的第 ``index`` 帧，便于写单行断言。"""
    return get_scenario(name).frames[index]


def sample_stream() -> Iterator[tuple[list[PerceptionResult], EnvContext]]:
    """按顺序产出所有场景的首帧。

    保留此函数以兼容既有调用方；新代码建议直接用 :func:`get_scenario`
    以便按需组合多帧序列。
    """
    for scenario in SCENARIOS:
        if scenario.frames:
            yield scenario.frames[0]


# ----------------------------------------------------------------------
# 假智能体：让 B / C 在模型未就绪时也能联调
# ----------------------------------------------------------------------


def fake_agent(
    agent_id: str = AGENT_EXPRESSION,
    label: EmotionLabel = EmotionLabel.FOCUSED,
    prob: float = 0.9,
    confidence: float | None = None,
):
    """返回一个符合 :class:`PerceptionAgent` 协议的假智能体。

    用途：B 在自己的模型训练完成前测试 ``pipeline/``；C 在 A 的融合完成前
    搭建前端。**这是把「三人并行」从口号变成事实的关键工具。**

    Args:
        agent_id: 智能体标识。
        label: 恒定输出的标签。
        prob: 恒定输出的概率。
        confidence: 恒定置信度，``None`` 时与 ``prob`` 相同。
    """
    from common.agent_base import PerceptionAgent

    conf = prob if confidence is None else confidence

    class _Fake(PerceptionAgent):
        agent_id_ = agent_id

        def warmup(self) -> None:
            return None

        def infer(self, frame, ts: float, frame_id: int) -> PerceptionResult:
            return PerceptionResult(
                label=label,
                prob=prob,
                confidence=conf,
                agent_id=agent_id,
                ts=ts,
                frame_id=frame_id,
            )

    _Fake.agent_id = agent_id
    _Fake.__name__ = f"FakeAgent_{agent_id}"
    return _Fake()


def fake_env_agent(
    E: float = 0.9,
    brightness: float = 0.8,
    blur: float = 0.2,
    occlusion: float = 0.0,
):
    """返回一个符合 :class:`EnvAgent` 协议的假环境智能体。

    用途：A 在 C 的环境智能体交付前，为融合层提供确定的 E 值做调试。
    """
    from common.agent_base import EnvAgent

    class _FakeEnv(EnvAgent):
        def warmup(self) -> None:
            return None

        def assess(self, frame, ts: float, frame_id: int) -> EnvContext:
            return EnvContext(
                brightness=brightness,
                blur=blur,
                env_score=E,
                occlusion=occlusion,
                ts=ts,
                frame_id=frame_id,
            )

    _FakeEnv.__name__ = "FakeEnvAgent"
    return _FakeEnv()
