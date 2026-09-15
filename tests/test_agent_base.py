"""``common/agent_base.py`` 的协议测试。

该模块是三人并行开发的**解耦点**：任一模块只要实现协议，就能被单独实例化并测试。
本文件把协议里「可被自动检查」的部分固定下来 —— 抽象方法是否真的抽象、
降级构造器产出的结果是否合法、``close()`` 是否有可用的默认实现。
"""

from __future__ import annotations

from typing import Any

import pytest

from common import agent_base
from common.agent_base import EnvAgent, PerceptionAgent
from common.config import DEFAULT_E0
from common.perception_types import (
    AGENT_EXPRESSION,
    EmotionLabel,
    EnvContext,
    PerceptionResult,
)


class _StubPerception(PerceptionAgent):
    """最小合规实现：只满足协议，不做真实推理。"""

    agent_id = AGENT_EXPRESSION

    def __init__(self, agent_id: str | None = None) -> None:
        if agent_id is not None:
            self.agent_id = agent_id
        self.warmed = False

    def warmup(self) -> None:
        self.warmed = True

    def infer(self, frame: Any, ts: float, frame_id: int) -> PerceptionResult:
        return self._unknown(ts, frame_id)


class _StubEnv(EnvAgent):
    """最小合规的环境智能体实现。"""

    def warmup(self) -> None:
        return None

    def assess(self, frame: Any, ts: float, frame_id: int) -> EnvContext:
        return self._neutral(ts, frame_id)


def test_module_exports() -> None:
    assert set(agent_base.__all__) == {"EnvAgent", "PerceptionAgent"}


class TestPerceptionAgentProtocol:
    def test_abstract_base_cannot_instantiate(self) -> None:
        """抽象基类本身不可实例化 —— 「必须实现协议」的第一道门。"""
        with pytest.raises(TypeError):
            PerceptionAgent()  # type: ignore[abstract]

    def test_partial_implementation_rejected(self) -> None:
        """只实现一半（缺 ``infer``）同样不合规。"""

        class _Incomplete(PerceptionAgent):
            def warmup(self) -> None:
                return None

        with pytest.raises(TypeError):
            _Incomplete()  # type: ignore[abstract]

    def test_full_implementation_instantiates(self) -> None:
        agent = _StubPerception()
        assert agent.agent_id == AGENT_EXPRESSION
        assert agent.warmed is False
        agent.warmup()
        assert agent.warmed is True

    def test_close_is_not_abstract(self) -> None:
        """``close()`` 有默认空实现，子类不覆写也能安全调用。"""
        assert _StubPerception().close() is None
        assert _StubEnv().close() is None

    def test_unknown_result_is_wellformed(self) -> None:
        """降级结果是合法契约对象，且各项哨兵值符合约定。"""
        result = _StubPerception().infer(None, ts=1.5, frame_id=7)
        assert result.label is EmotionLabel.UNKNOWN
        assert result.prob == 0.0
        assert result.confidence == 0.0
        assert result.agent_id == AGENT_EXPRESSION
        assert result.ts == 1.5
        assert result.frame_id == 7

    def test_unknown_result_falls_back_agent_id(self) -> None:
        """``agent_id`` 未覆写（空串）时降级为 ``"unknown"``，而不是空串。"""
        result = _StubPerception(agent_id="").infer(None, ts=0.0, frame_id=0)
        assert result.agent_id == "unknown"


class TestEnvAgentProtocol:
    def test_abstract_base_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            EnvAgent()  # type: ignore[abstract]

    def test_default_agent_id(self) -> None:
        assert _StubEnv().agent_id == "env"

    def test_neutral_is_valid_envcontext(self) -> None:
        """中性环境值必须是合法 ``EnvContext``（各字段都能通过契约校验）。"""
        context = _StubEnv().assess(None, ts=0.5, frame_id=3)
        for name in ("brightness", "blur", "env_score", "occlusion"):
            assert 0.0 <= getattr(context, name) <= 1.0, name
        assert context.ts == 0.5
        assert context.frame_id == 3

    def test_neutral_env_score_is_e0_not_half(self) -> None:
        """``env_score`` 必须是 ``weights.e0``（默认 0.6），而不是固定 0.5。

        回归护栏：``e0`` 是 logistic 中心点，只有 ``E = e0`` 时两路权重才相等
        （0.5 : 0.5），才是真正的「无信息」先验。若改回 0.5，因 ``0.5 < e0``
        权重会偏到行为通道（约 0.31 : 0.69），等于环境评估失败时偷偷假设
        「环境很差」，把话语权单方面让给行为通道。
        """
        env_score = _StubEnv().assess(None, ts=0.0, frame_id=0).env_score
        assert env_score == pytest.approx(DEFAULT_E0)
        assert env_score != pytest.approx(0.5)

    def test_neutral_reports_no_occlusion(self) -> None:
        """降级路径不应谎称存在遮挡 —— 否则会额外触发一次环境降权。"""
        assert _StubEnv().assess(None, ts=0.0, frame_id=0).occlusion == 0.0
