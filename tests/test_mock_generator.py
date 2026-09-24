"""Mock 数据生成器单测（零第三方依赖，与 CI 环境一致）。

为什么这个文件值得单独存在
--------------------------
``common/mock/generator.py`` 承担的是**协作基础设施**的角色：``fake_agent()`` /
``fake_env_agent()`` 让三方在彼此模块未交付时也能各自跑通联调（见其模块
docstring：「这是把『三人并行』从口号变成事实的关键工具」）。但在此之前，
这两个工厂**没有任何测试** —— 而它们恰恰是最该被钉死的：一旦输出偏离契约，
三路都会「各自看起来都正常」，只在真正联调时才爆（与表 B 要解决的问题同源）。

断言分三类：

1. **契约一致性** —— mock 产出必须本身就是合法的 ``PerceptionResult`` /
   ``EnvContext``（``prob_dist`` 归一化、不含 ``UNKNOWN``、与 ``label`` 一致）；
   否则融合层会悄悄走兼容降级路径，mock 就失去了「验证真实路径」的价值。
2. **确定性** —— 同一场景每次产出相同。mock 的价值全在这里，不能引入随机数。
3. **工厂的可替换性** —— 假智能体只实现协议、不携带业务逻辑，因此可以被
   直接喂进融合层；这条断言是「三方并行」的技术依据。
"""

from __future__ import annotations

import pytest

from common.agent_base import EnvAgent, PerceptionAgent
from common.mock import generator
from common.mock.generator import (
    SCENARIOS,
    Scenario,
    _distribution,
    fake_agent,
    fake_env_agent,
    get_scenario,
    sample_stream,
    scenario_frame,
)
from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_ENV,
    AGENT_EXPRESSION,
    EMOTION_LABELS,
    EmotionLabel,
    EnvContext,
    PerceptionResult,
)
from fusion.fusion_engine import FusionEngine

#: 模块 docstring 承诺的七类情形。**顺序即设计顺序**，改动需同步文档。
EXPECTED_SCENARIOS = (
    "consensus",
    "conflict",
    "low_light",
    "low_confidence",
    "occluded",
    "single_channel",
    "jitter",
)


# ---------------------------------------------------------------------------
# 场景清单本身的完整性
# ---------------------------------------------------------------------------


def test_scenario_names_match_documented_set() -> None:
    """场景集合必须与设计文档一致 —— 少一个就意味着一类边界没人测。"""
    assert tuple(scenario.name for scenario in SCENARIOS) == EXPECTED_SCENARIOS


def test_scenario_names_are_unique() -> None:
    """``get_scenario`` 以名称检索，重名会让其中一个永远取不到。"""
    names = [scenario.name for scenario in SCENARIOS]
    assert len(set(names)) == len(names)


def test_every_scenario_has_note_and_frames() -> None:
    """``note`` 是「预期行为」的自解释说明，测试与文档都直接读它，不能空。"""
    for scenario in SCENARIOS:
        assert scenario.note.strip(), f"{scenario.name} 缺少 note"
        assert scenario.frames, f"{scenario.name} 没有帧"


# ---------------------------------------------------------------------------
# 每一帧都必须自洽（这是 mock 的核心承诺：走真实路径，而不是降级路径）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", EXPECTED_SCENARIOS)
def test_scenario_frames_satisfy_contract(name: str) -> None:
    for perceptions, env in get_scenario(name).frames:
        assert perceptions, f"{name}: 每帧至少应有一路感知结果"
        assert isinstance(env, EnvContext)

        for result in perceptions:
            assert isinstance(result, PerceptionResult)
            # prob_dist 非空 ⇒ 融合层走 §3.1 的完整分布公式，而不是只取 top 标签
            assert result.prob_dist, f"{name}: mock 必须填充 prob_dist"
            _assert_valid_distribution(result)

        # 同帧对齐：一帧内的多路结果与环境上下文必须共享 ts / frame_id
        assert len({result.ts for result in perceptions}) == 1
        assert len({result.frame_id for result in perceptions}) == 1
        assert perceptions[0].ts == env.ts
        assert perceptions[0].frame_id == env.frame_id


def _assert_valid_distribution(result: PerceptionResult) -> None:
    """契约对 ``prob_dist`` 的全部要求，逐条复核。"""
    keys = set(result.prob_dist)
    assert keys <= set(EMOTION_LABELS), "prob_dist 只能以情感标签为键"
    assert EmotionLabel.UNKNOWN not in keys, "UNKNOWN 是哨兵，不属于情感分布"
    assert sum(result.prob_dist.values()) == pytest.approx(1.0)
    assert result.prob_dist[result.label] == pytest.approx(result.prob)
    # mock 应当落在「标签即 argmax」的干净形态上，否则它示范的是歧义情形
    top = max(result.prob_dist.values())
    assert result.prob_dist[result.label] == pytest.approx(top)


@pytest.mark.parametrize("name", EXPECTED_SCENARIOS)
def test_scenario_is_deterministic(name: str) -> None:
    """同一场景两次取出的帧必须完全相等 —— 断言可复现的前提。"""
    assert get_scenario(name).frames == get_scenario(name).frames


@pytest.mark.parametrize("name", EXPECTED_SCENARIOS)
def test_scenario_only_uses_known_agent_ids(name: str) -> None:
    """``agent_id`` 是融合层分路与权重转移的依据，写错会让某一路被静默丢弃。"""
    allowed = {AGENT_EXPRESSION, AGENT_BEHAVIOR}
    for perceptions, _env in get_scenario(name).frames:
        for result in perceptions:
            assert result.agent_id in allowed, f"{name}: 环境智能体不参与情感投票"


# ---------------------------------------------------------------------------
# 检索接口
# ---------------------------------------------------------------------------


def test_get_scenario_returns_the_registered_instance() -> None:
    assert get_scenario("consensus") is SCENARIOS[0]


def test_get_scenario_unknown_name_lists_available() -> None:
    """报错信息要能直接指导使用者 —— 只抛 KeyError 会让人去翻源码。"""
    with pytest.raises(KeyError) as excinfo:
        get_scenario("no_such_scenario")
    message = str(excinfo.value)
    assert "no_such_scenario" in message
    for name in EXPECTED_SCENARIOS:
        assert name in message


def test_scenario_frame_defaults_to_first_frame() -> None:
    assert scenario_frame("consensus") == get_scenario("consensus").frames[0]


def test_scenario_frame_honours_index() -> None:
    assert scenario_frame("jitter", 4) == get_scenario("jitter").frames[4]


def test_scenario_frame_propagates_index_error() -> None:
    """越界是调用方的错误，不静默兜底（否则测试会拿错帧却看不出来）。"""
    with pytest.raises(IndexError):
        scenario_frame("jitter", 99)


def test_sample_stream_yields_first_frame_of_every_scenario() -> None:
    frames = list(sample_stream())
    assert len(frames) == len(SCENARIOS)
    assert frames == [scenario.frames[0] for scenario in SCENARIOS]


def test_sample_stream_skips_scenarios_without_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """空场景不得产出 ``IndexError`` —— 它是「某一类边界暂时没有样本」的合法状态。"""
    filled = Scenario(name="filled", note="有帧", frames=[([], EnvContext(0.5, 0.5, 0.6))])
    empty = Scenario(name="empty", note="无帧")
    monkeypatch.setattr(generator, "SCENARIOS", [empty, filled])
    assert list(sample_stream()) == filled.frames


# ---------------------------------------------------------------------------
# _distribution：把标量概率展开成合法分布
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", EMOTION_LABELS)
def test_distribution_sums_to_one(label: EmotionLabel) -> None:
    dist = _distribution(label, 0.7)
    assert sum(dist.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("label", EMOTION_LABELS)
def test_distribution_excludes_unknown(label: EmotionLabel) -> None:
    assert EmotionLabel.UNKNOWN not in _distribution(label, 0.7)


def test_distribution_splits_remainder_evenly_and_keeps_label_prob() -> None:
    """余量均分是刻意的：它不给其余两类引入任何额外语义偏好。"""
    dist = _distribution(EmotionLabel.CONFUSED, 0.6)
    assert dist[EmotionLabel.CONFUSED] == pytest.approx(0.6)
    assert dist[EmotionLabel.FOCUSED] == pytest.approx(0.2)
    assert dist[EmotionLabel.DISTRACTED] == pytest.approx(0.2)


def test_distribution_at_certainty_zeroes_the_others() -> None:
    dist = _distribution(EmotionLabel.FOCUSED, 1.0)
    assert dist == {
        EmotionLabel.FOCUSED: 1.0,
        EmotionLabel.CONFUSED: 0.0,
        EmotionLabel.DISTRACTED: 0.0,
    }


@pytest.mark.parametrize("prob", [0.36, 0.5, 0.9, 1.0])
def test_distribution_is_accepted_by_the_contract(prob: float) -> None:
    """展开结果必须能直接塞进契约 —— 否则 mock 会在构造期就炸。"""
    dist = _distribution(EmotionLabel.DISTRACTED, prob)
    result = PerceptionResult(EmotionLabel.DISTRACTED, prob, prob, AGENT_EXPRESSION, prob_dist=dist)
    assert result.prob_dist[EmotionLabel.DISTRACTED] == pytest.approx(prob)


# ---------------------------------------------------------------------------
# fake_agent：B / C 在模型未就绪时的替身
# ---------------------------------------------------------------------------


def test_fake_agent_implements_perception_protocol() -> None:
    agent = fake_agent()
    assert isinstance(agent, PerceptionAgent)
    assert agent.agent_id == AGENT_EXPRESSION
    assert agent.warmup() is None
    assert agent.close() is None


def test_fake_agent_infer_fills_the_contract() -> None:
    agent = fake_agent(AGENT_BEHAVIOR, EmotionLabel.CONFUSED, 0.8)
    result = agent.infer(None, ts=1.5, frame_id=15)
    assert isinstance(result, PerceptionResult)
    assert result.agent_id == AGENT_BEHAVIOR
    assert result.label is EmotionLabel.CONFUSED
    assert result.prob == pytest.approx(0.8)
    assert (result.ts, result.frame_id) == (1.5, 15)
    _assert_valid_distribution(result)


def test_fake_agent_confidence_defaults_to_prob() -> None:
    """默认置信度等于概率 —— 让「恒定输出」在两级协商里都表现得像确定判定。"""
    result = fake_agent(prob=0.75).infer(None, 0.0, 0)
    assert result.confidence == pytest.approx(0.75)


def test_fake_agent_accepts_explicit_confidence() -> None:
    """需要构造「概率高但置信度低」这类矛盾输入时用得上（低置信场景的变体）。"""
    result = fake_agent(prob=0.9, confidence=0.2).infer(None, 0.0, 0)
    assert result.prob == pytest.approx(0.9)
    assert result.confidence == pytest.approx(0.2)


def test_fake_agent_empty_dist_switches_to_the_legacy_path() -> None:
    """显式传 ``{}`` 是「关闭完整分布」的开关，用于验证融合层的兼容降级路径。"""
    result = fake_agent(prob_dist={}).infer(None, 0.0, 0)
    assert result.prob_dist == {}
    assert result.label is EmotionLabel.FOCUSED


def test_fake_agent_copies_the_supplied_distribution() -> None:
    """工厂内部取的是副本：调用方之后改写字典不得影响假智能体的输出。"""
    dist = dict(_distribution(EmotionLabel.FOCUSED, 0.6))
    agent = fake_agent(prob=0.6, prob_dist=dist)
    dist[EmotionLabel.FOCUSED] = 0.0
    assert agent.infer(None, 0.0, 0).prob_dist[EmotionLabel.FOCUSED] == pytest.approx(0.6)


def test_fake_agent_instances_are_independent() -> None:
    """每次调用生成独立子类，两个假智能体之间不得串味（多路联调的前提）。"""
    expression = fake_agent(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.9)
    behavior = fake_agent(AGENT_BEHAVIOR, EmotionLabel.DISTRACTED, 0.4)
    assert expression.infer(None, 0.0, 0).label is EmotionLabel.FOCUSED
    assert behavior.infer(None, 0.0, 0).label is EmotionLabel.DISTRACTED
    assert expression.agent_id != behavior.agent_id


def test_fake_agent_class_name_is_self_describing() -> None:
    """失败时 pytest 会打印类名，带上 ``agent_id`` 才能一眼看出是哪一路。"""
    assert type(fake_agent(AGENT_BEHAVIOR)).__name__ == f"FakeAgent_{AGENT_BEHAVIOR}"


def test_fake_agent_accepts_the_none_frame_sentinel() -> None:
    """``None`` 是无帧哨兵：假智能体不读帧，因此永远可用 —— 这正是它的用途。"""
    assert fake_agent().infer(None, 0.0, 0) is not None


def test_fake_agents_can_drive_the_fusion_engine() -> None:
    """端到端最小闭环：两个假智能体 + 假环境 → 融合层给出确定结论。

    这条断言是「三人并行」的技术依据 —— 若它失败，说明 mock 与融合层的接口
    已经脱钩，A / B / C 就不能再各自独立推进了。
    """
    expression = fake_agent(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.9)
    behavior = fake_agent(AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.85)
    env = fake_env_agent(E=0.9).assess(None, 0.0, 0)
    fused = FusionEngine().fuse([expression.infer(None, 0.0, 0), behavior.infer(None, 0.0, 0)], env)
    assert fused.label is EmotionLabel.FOCUSED


# ---------------------------------------------------------------------------
# fake_env_agent：A 在环境智能体交付前的替身
# ---------------------------------------------------------------------------


def test_fake_env_agent_implements_env_protocol() -> None:
    agent = fake_env_agent()
    assert isinstance(agent, EnvAgent)
    assert agent.agent_id == AGENT_ENV
    assert agent.warmup() is None
    assert agent.close() is None


def test_fake_env_agent_defaults_are_a_good_environment() -> None:
    """默认值刻意取「环境良好」：多数测试关心的是融合逻辑，不是环境降权。"""
    context = fake_env_agent().assess(None, 0.0, 0)
    assert context.env_score == pytest.approx(0.9)
    assert context.brightness == pytest.approx(0.8)
    assert context.blur == pytest.approx(0.2)
    assert context.occlusion == 0.0


def test_fake_env_agent_echoes_configuration_and_timestamps() -> None:
    agent = fake_env_agent(E=0.3, brightness=0.22, blur=0.55, occlusion=0.95)
    context = agent.assess(None, ts=2.0, frame_id=20)
    assert context.env_score == pytest.approx(0.3)
    assert context.brightness == pytest.approx(0.22)
    assert context.blur == pytest.approx(0.55)
    assert context.occlusion == pytest.approx(0.95)
    assert (context.ts, context.frame_id) == (2.0, 20)


def test_fake_env_agent_instances_are_independent() -> None:
    good = fake_env_agent(E=0.9)
    bad = fake_env_agent(E=0.3)
    assert good.assess(None, 0.0, 0).env_score == pytest.approx(0.9)
    assert bad.assess(None, 0.0, 0).env_score == pytest.approx(0.3)


def test_fake_env_agent_rejects_out_of_range_values() -> None:
    """假智能体不绕过契约：非法 E 应当在 ``EnvContext`` 构造期就被拦下。"""
    with pytest.raises(ValueError):
        fake_env_agent(E=1.5).assess(None, 0.0, 0)
