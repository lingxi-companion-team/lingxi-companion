"""串行集成主流程的单测（C 主责）。

关注点不是「融合算得对不对」（那是 ``tests/fusion/`` 的事），而是**装配与韧性**：

* 两条入口（原始帧 / 感知流）是否汇到同一套收尾流程；
* 三路任一路抛异常时，链路是否降级而不是崩溃；
* 报告是否如实记下「哪一帧、哪一路、为什么降级」；
* 信封与在线表是否被正确喂到（教师不占格）。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agents.env.agent import RuleBasedEnvAgent
from app.envelope import ROLE_STUDENT, ROLE_TEACHER
from app.hub import Hub
from app.integration import (
    ListFrameSource,
    MockScenarioSource,
    SerialSession,
    SyntheticFrame,
    SyntheticFrameSource,
    neutral_env_context,
)
from common.agent_base import EnvAgent, PerceptionAgent
from common.config import DEFAULT_E0
from common.mock import generator
from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_EXPRESSION,
    EmotionLabel,
    EnvContext,
)
from fusion.fusion_engine import REASON_EMPTY

# ----------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------


class _BoomAgent(PerceptionAgent):
    """推理必炸的感知智能体（协议要求不抛，但它违反了 —— 集成层必须兜住）。"""

    agent_id = AGENT_EXPRESSION

    def warmup(self) -> None:
        """无资源。"""

    def infer(self, frame, ts: float, frame_id: int):
        raise RuntimeError("boom")


class _NamelessAgent(_BoomAgent):
    """连 ``agent_id`` 都没填的智能体（降级结果的 agent_id 必须仍有兜底值）。"""

    agent_id = ""


class _BoomEnvAgent(EnvAgent):
    """评估必炸的环境智能体。"""

    def warmup(self) -> None:
        """无资源。"""

    def assess(self, frame, ts: float, frame_id: int):
        raise RuntimeError("env boom")


class _BoomEngine:
    """融合必炸（正常实现不该如此，测的是集成层的兜底）。"""

    def fuse(self, results, env):
        raise RuntimeError("fuse boom")


class _BoomSmoother:
    """平滑必炸。"""

    def update(self, *args, **kwargs):
        raise RuntimeError("smooth boom")


class _BoomSource:
    """产出 1 帧后读失败的输入源。"""

    def __init__(self) -> None:
        self.closed = False

    def frames(self):
        yield (SyntheticFrame(), 0.1, 1)
        raise RuntimeError("read failed")

    def close(self) -> None:
        self.closed = True


def _session(**kwargs) -> SerialSession:
    """带两路假感知智能体的会话（真模型未交付时的标准装配）。"""
    kwargs.setdefault(
        "expression", generator.fake_agent(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.85)
    )
    kwargs.setdefault("behavior", generator.fake_agent(AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.80))
    return SerialSession(**kwargs)


# ----------------------------------------------------------------------
# 感知流路径（mock / demo）
# ----------------------------------------------------------------------


def test_mock_consensus_confirms_from_the_third_frame():
    """共识场景前两帧窗口未满，第三帧起确认为 focused。"""
    reports = _session().run(MockScenarioSource("consensus"))

    assert [item.final for item in reports[:2]] == [None, None]
    confirmed = reports[2].final
    assert confirmed is not None
    assert confirmed.label is EmotionLabel.FOCUSED
    assert confirmed.stale is False
    assert all(item.fused.reason == "consensus" for item in reports)
    assert not any(item.degraded for item in reports)


def test_mock_conflict_never_confirms():
    """冲突场景全程拒判：融合输出 UNKNOWN，平滑层也就永远攒不出窗口。"""
    reports = _session().run(MockScenarioSource("conflict"))

    assert reports
    assert all(item.fused.label is EmotionLabel.UNKNOWN for item in reports)
    assert all(item.fused.reason == "conflict" for item in reports)
    assert all(item.final is None for item in reports)


def test_perception_route_records_every_route():
    """感知流入口把 mock 给出的每一路都记进报告。"""
    report = _session().step_perception(*generator.scenario_frame("consensus"))

    assert report.origin == "perception"
    assert report.problems == ()
    assert [item.agent_id for item in report.agents] == [AGENT_EXPRESSION, AGENT_BEHAVIOR]
    assert all(item.ok for item in report.agents)
    assert report.agents[0].label == EmotionLabel.FOCUSED.value


def test_scenario_repeat_keeps_time_and_ids_monotonic():
    """重复播放场景时时间戳与帧序号必须整体平移，不能倒退。"""
    reports = _session().run(MockScenarioSource("consensus", repeat=2))

    assert len(reports) == 8
    stamps = [item.ts for item in reports]
    ids = [item.frame_id for item in reports]
    assert stamps == sorted(set(stamps))
    assert ids == sorted(set(ids))


def test_max_frames_limits_execution():
    """``max_frames`` 是硬上限。"""
    assert len(_session().run(MockScenarioSource("consensus"), max_frames=2)) == 2


def test_report_line_carries_the_key_facts():
    """一行摘要必须能读出帧号、每路结果、融合级别与最终状态。"""
    line = _session().step_perception(*generator.scenario_frame("consensus")).line()

    assert line.startswith("#0001 ")
    assert "expression=focused" in line
    assert "consensus" in line
    assert "final=-" in line


# ----------------------------------------------------------------------
# 原始帧路径（全链路）
# ----------------------------------------------------------------------


def test_frame_route_runs_all_three_agents():
    """原始帧入口跑满三路，环境路真实计算并回填 E。"""
    report = _session().run(ListFrameSource([(SyntheticFrame(), 0.1, 1)]))[0]

    assert report.origin == "frame"
    assert report.problems == ()
    # 顺序：两路感知在前（与 warmup 顺序一致），环境路在后
    assert [item.agent_id for item in report.agents] == [
        AGENT_EXPRESSION,
        AGENT_BEHAVIOR,
        "env",
    ]
    assert report.env.env_score > 0.9  # 合成噪声帧：细节充足、E 高


def test_frame_spec_violation_is_recorded_not_raised():
    """``None`` 无帧哨兵走降级：记进报告 + 环境路回中性值，不抛异常。"""
    report = _session().run(ListFrameSource([(None, 0.1, 1)]))[0]

    assert report.problems
    assert any("frame is None" in item for item in report.problems)
    assert report.degraded is True
    assert report.env.env_score == pytest.approx(DEFAULT_E0)


# ----------------------------------------------------------------------
# 降级路径
# ----------------------------------------------------------------------


def test_agent_exception_degrades_and_chain_continues():
    """一路推理抛异常 → 该路降级为 UNKNOWN，其余两路与整条链路继续。"""
    reports = _session(expression=_BoomAgent()).run(
        ListFrameSource([(SyntheticFrame(), 0.1, 1)] * 3)
    )

    first = reports[0]
    assert first.problems == ()  # 帧本身合格，问题只出在智能体
    assert first.agents[0].ok is False
    assert "boom" in first.agents[0].error
    assert first.agents[1].ok is True
    assert first.degraded is True
    assert first.fused.label is EmotionLabel.FOCUSED  # 由行为路单独支撑

    # 三帧之后平滑层照样确认 —— 一路挂掉不等于链路挂掉
    assert reports[2].final is not None
    assert reports[2].final.label is EmotionLabel.FOCUSED


def test_nameless_agent_still_produces_a_valid_degradation():
    """连 agent_id 都没填也不该引发第二次异常（降级结果必须带非空标识）。"""
    report = _session(expression=_NamelessAgent()).run(
        ListFrameSource([(SyntheticFrame(), 0.1, 1)])
    )[0]

    assert report.agents[0].agent_id == "_NamelessAgent"
    assert report.agents[0].ok is False


def test_env_exception_falls_back_to_neutral_value():
    """环境路抛异常 → 用中性环境值（E = e0），而不是猜一个好环境或坏环境。"""
    report = _session(env_agent=_BoomEnvAgent()).run(ListFrameSource([(SyntheticFrame(), 0.1, 1)]))[
        0
    ]

    assert report.env.env_score == pytest.approx(DEFAULT_E0)
    assert report.env.brightness == pytest.approx(0.5)
    assert report.env.occlusion == pytest.approx(0.0)
    assert report.agents[-1].ok is False
    assert "env boom" in report.agents[-1].error


def test_neutral_fallback_matches_the_protocol_default():
    """集成层的兜底实现必须与 ``EnvAgent._neutral`` 给出同一个中性先验。

    否则同一份「无信息」输入会因走了哪条降级路径（环境智能体自己失败 vs
    智能体崩到连方法都调不动）而得到不同的权重分配。
    """
    assert neutral_env_context(1.5, 7) == RuleBasedEnvAgent()._neutral(1.5, 7)


def test_fusion_exception_degrades_to_empty_reason():
    """融合抛异常 → 本帧判为 UNKNOWN，**不新造** reason 取值（复用 empty）。"""
    report = _session(engine=_BoomEngine()).run(MockScenarioSource("consensus"))[0]

    assert report.fused.label is EmotionLabel.UNKNOWN
    assert report.fused.reason == REASON_EMPTY
    assert any("融合异常" in item for item in report.problems)
    assert report.final is None  # UNKNOWN 不进投票窗口


def test_smoother_exception_leaves_no_final_state():
    """平滑抛异常 → 本帧不产出稳定状态，但报告仍然完整。"""
    report = _session(smoother=_BoomSmoother()).run(MockScenarioSource("consensus"))[0]

    assert report.final is None
    assert any("平滑异常" in item for item in report.problems)


def test_source_read_failure_ends_the_run_cleanly():
    """输入源读失败 → 结束迭代、保留已跑出的结果、仍然关闭源。"""
    source = _BoomSource()
    reports = _session().run(source)

    assert len(reports) == 1
    assert source.closed is True


def test_session_without_perception_routes_still_runs():
    """两路感知都没交付时，会话照常可跑（只有环境 + 融合），只是没有判定。"""
    reports = SerialSession().run(ListFrameSource([(SyntheticFrame(), 0.1, 1)]))

    assert len(reports) == 1
    assert len(reports[0].agents) == 1
    assert reports[0].agents[0].agent_id == "env"
    assert reports[0].fused.reason == REASON_EMPTY


def test_empty_perception_stream_is_reported_as_empty():
    """感知流给出空列表（无任何智能体）→ 融合走 empty 分支。"""

    class _EmptySource:
        def frames(self):
            yield [], EnvContext(brightness=0.5, blur=0.5, env_score=0.6, ts=0.1, frame_id=1)

        def close(self) -> None:
            """无资源。"""

    report = _session().run(_EmptySource())[0]

    assert report.fused.label is EmotionLabel.UNKNOWN
    assert report.fused.reason == REASON_EMPTY
    assert report.agents == ()


def test_warmup_failure_does_not_block_and_runs_once():
    """某路 warmup 失败不阻断启动，且 warmup 只调用一次（幂等）。"""

    class _BadWarmup(PerceptionAgent):
        agent_id = AGENT_EXPRESSION

        def __init__(self) -> None:
            self.calls = 0

        def warmup(self) -> None:
            self.calls += 1
            raise RuntimeError("warmup boom")

        def infer(self, frame, ts: float, frame_id: int):
            raise RuntimeError("never reached")

    agent = _BadWarmup()
    session = _session(expression=agent)
    reports = session.run(ListFrameSource([(SyntheticFrame(), 0.1, 1)] * 2))

    assert len(reports) == 2
    assert agent.calls == 1


def test_close_survives_a_failing_route():
    """收尾阶段的失败不该把主流程的结果一起抹掉。"""

    class _BadClose(PerceptionAgent):
        agent_id = AGENT_EXPRESSION

        def warmup(self) -> None:
            """无资源。"""

        def infer(self, frame, ts: float, frame_id: int):
            raise RuntimeError("boom")

        def close(self) -> None:
            raise RuntimeError("close boom")

    session = _session(expression=_BadClose())
    session.close()  # 不抛即为通过


# ----------------------------------------------------------------------
# 前端信封
# ----------------------------------------------------------------------


def test_publish_registers_student_into_hub():
    """publish 把稳定状态封进信封，并让在线表立刻能取到 payload。"""
    hub = Hub()
    session = _session(hub=hub, participant_id="s07")
    reports = session.run(MockScenarioSource("consensus"))
    participant = session.publish(reports[-1])

    assert participant.participant_id == "s07"
    assert participant.state == reports[-1].final

    payload = hub.snapshot_for("s07")
    assert payload["viewer"] == "s07"
    assert payload["role"] == ROLE_STUDENT
    assert [cell["participant_id"] for cell in payload["grid"]] == ["s07"]
    assert "summary" not in payload  # A2：学生端压根不下发汇总


def test_teacher_publishes_no_state_and_occupies_no_cell():
    """教师不带自己的状态（A1：教师不占格）。"""
    session = _session(role=ROLE_TEACHER)
    reports = session.run(MockScenarioSource("consensus"))
    participant = session.publish(reports[-1])

    assert participant.state is None
    assert participant.occupies_cell is False


def test_invalid_participant_is_rejected_at_construction():
    """非法参与者标识 / 角色在会话构造时就报错，而不是等第一帧 publish。"""
    with pytest.raises(ValueError):
        SerialSession(participant_id="")
    with pytest.raises(ValueError):
        SerialSession(role="guest")


# ----------------------------------------------------------------------
# 输入源本身
# ----------------------------------------------------------------------


def test_synthetic_source_emits_monotonic_time_and_ids():
    """合成源：ts 与 frame_id 都单调、尺寸与纹理可控。"""
    source = SyntheticFrameSource(frames=3, height=64, width=64, texture="flat")
    items = list(source.frames())
    source.close()

    assert [item[2] for item in items] == [0, 1, 2]
    assert [item[1] for item in items] == [0.0, 0.1, 0.2]
    assert all(item[0].shape == (64, 64, 3) for item in items)


def test_synthetic_source_rejects_illegal_arguments_eagerly():
    """非法参数在**构造时**就报错，不拖到第一帧。"""
    with pytest.raises(ValueError):
        SyntheticFrameSource(frames=-1)
    with pytest.raises(ValueError):
        SyntheticFrameSource(fps=0)
    with pytest.raises(ValueError):
        SyntheticFrameSource(height=0)
    with pytest.raises(ValueError):
        SyntheticFrameSource(texture="blur")


def test_mock_source_exposes_scenario_metadata():
    """场景元数据可读（联调记录要引用 note）。"""
    source = MockScenarioSource("low_light")
    source.close()

    assert source.scenario_name == "low_light"
    assert source.frame_count == 4
    assert "弱光" in source.note


def test_mock_source_rejects_unknown_scenario_and_bad_repeat():
    """未知场景抛 KeyError（带可用名），非法 repeat 抛 ValueError。"""
    with pytest.raises(KeyError):
        MockScenarioSource("no_such_scenario")
    with pytest.raises(ValueError):
        MockScenarioSource("consensus", repeat=0)


def test_list_frame_source_replays_in_order():
    """显式帧列表原样回放（含 None 无帧哨兵）。"""
    source = ListFrameSource([(SyntheticFrame(), 0.1, 1), (None, 0.2, 2)])
    items = list(source.frames())
    source.close()

    assert [item[2] for item in items] == [1, 2]
    assert items[1][0] is None


def test_synthetic_frame_textures_behave_as_documented():
    """三种纹理的像素行为：flat 恒定、smooth 递增、noise 同种子可复现。"""
    flat = SyntheticFrame(height=64, width=64, texture="flat", level=200)
    assert flat.pixel(0, 0) == (200, 200, 200)
    assert flat.pixel(63, 63) == (200, 200, 200)

    smooth = SyntheticFrame(height=64, width=64, texture="smooth", level=10)
    assert smooth.pixel(63, 63)[0] > smooth.pixel(0, 0)[0]

    first = SyntheticFrame(height=64, width=64, texture="noise", seed=7)
    same = SyntheticFrame(height=64, width=64, texture="noise", seed=7)
    other = SyntheticFrame(height=64, width=64, texture="noise", seed=8)
    assert first.pixel(5, 9) == same.pixel(5, 9)
    assert first.pixel(5, 9) != other.pixel(5, 9)


def test_synthetic_frame_rejects_illegal_size_and_texture():
    """帧替身自身也守住参数：非法尺寸 / 纹理直接报错。"""
    with pytest.raises(ValueError):
        SyntheticFrame(height=0)
    with pytest.raises(ValueError):
        SyntheticFrame(texture="blur")


def test_mock_source_handles_degenerate_timestamps(monkeypatch):
    """场景内所有帧同刻（间隔推不出来）→ 步长退化为 1 秒，且时间不倒退。"""
    env = EnvContext(brightness=0.5, blur=0.5, env_score=0.6, ts=0.5, frame_id=1)
    scenario = generator.Scenario(
        name="degenerate",
        note="所有帧共用同一时间戳（构造出来的边界场景）",
        frames=[([], env), ([], replace(env, frame_id=2))],
    )
    monkeypatch.setattr(generator, "get_scenario", lambda name: scenario)

    source = MockScenarioSource("degenerate", repeat=2)
    stamps = [item[1].ts for item in source.frames()]

    assert source.frame_count == 2
    assert stamps == sorted(stamps)
    assert stamps[0] == stamps[1]  # 场景内确实同刻
    assert stamps[2] > stamps[0]  # 第二轮整体平移，不回退


def test_source_close_failure_does_not_break_the_run():
    """输入源 close 抛异常也不影响结果：收尾失败不该抹掉已跑出的帧。"""

    class _BadCloseSource:
        def frames(self):
            yield (SyntheticFrame(), 0.1, 1)

        def close(self) -> None:
            raise RuntimeError("close boom")

    reports = _session().run(_BadCloseSource())

    assert len(reports) == 1
