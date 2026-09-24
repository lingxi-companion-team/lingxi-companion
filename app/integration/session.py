"""串行集成主流程：装配 + 日志 + 异常处理（C 主责）。

链路::

    (原始帧 | 感知流) → 三路智能体 → 融合 → 平滑 → ParticipantState（前端信封）

与 ``pipeline/`` 的分工（不要混）
--------------------------------
============  ================================  ============================
              ``app/integration/``（本模块）       ``pipeline/``（B 主责）
============  ================================  ============================
调度方式       串行、阻塞：一帧跑完再下一帧        多路并发 + 队列 + 丢过期帧
用途          CLI/演示可用性、联调与回归基线      200ms 指标与吞吐
依赖          零第三方依赖（CI 无 numpy）         numpy / torch / onnxruntime
============  ================================  ============================

串行版先行的理由见任务计划 §四 风险应对：「多进程框架受阻 → 先完成单线程串行
版本；B 在独立 feature 分支优化，不阻塞主线」。两者语义不同，**互不替代**。

设计要点
--------
1. **降级优先于崩溃**：协议要求 ``infer()`` / ``assess()`` 永不抛异常，但那是
   *约定*；集成层不能把「某人违反了约定」变成整场演示崩溃。故三路各自包一层
   try/except：失败者降级为 ``UNKNOWN`` / 中性环境值，其余两路继续，事件记进
   报告与日志。
2. **报告即证据**：每次 ``step`` 返回一个 :class:`FrameReport`（每路耗时、每路
   输出、融合命中的协商级别、平滑结果）。联调记录与后续压测从它取数，而不是
   让测试去解析日志文本。
3. **本层不含业务公式**：任何判定都委托给 ``fusion/`` 与 ``common/``；集成层
   一旦出现自己的加权、阈值或投票，就是越界。
4. **前端只走信封**：对外状态一律是 :class:`app.envelope.ParticipantState`，
   不把 ``FinalState`` 直接递给前端 —— 角色、可见性、占格判定都在信封里
   （见 ``app/envelope.py``）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from app.envelope import ROLE_STUDENT, ParticipantState
from app.integration.sources import Frame, FrameSource, PerceptionSource
from common.agent_base import EnvAgent, PerceptionAgent
from common.config import DEFAULT_E0, get
from common.input_spec import check_frame
from common.perception_types import (
    EmotionLabel,
    EnvContext,
    FinalState,
    FusionOutput,
    PerceptionResult,
)
from fusion.fusion_engine import REASON_EMPTY, FusionEngine
from fusion.temporal_smoother import TemporalSmoother

if TYPE_CHECKING:  # pragma: no cover
    import logging

    from app.hub import Hub

__all__ = [
    "AgentOutcome",
    "FrameReport",
    "SerialSession",
    "neutral_env_context",
]

#: 日志器名。集成链路的日志统一挂在它下面，便于 ``logging.basicConfig`` 后
#: 用 ``lingxi.integration`` 单独调级别。
LOGGER_NAME = "lingxi.integration"


def _logger() -> logging.Logger:
    """取模块日志器。

    ``logging`` 只在函数里 import：本模块要在**任何**环境下可导入，
    而日志器构造本身不需要在 import 期完成。
    """
    import logging

    return logging.getLogger(LOGGER_NAME)


def _agent_id(agent: Any) -> str:
    """取智能体标识；未按协议填写（``""`` / 缺失）时退化为类名。

    ``PerceptionResult`` 校验 ``agent_id`` 非空，因此降级路径必须有一个非空
    兜底值 —— 否则「某人的智能体忘了写 agent_id」会变成另一次异常，
    而这正是本层要消灭的东西。
    """
    raw = getattr(agent, "agent_id", "")
    name = raw if isinstance(raw, str) and raw.strip() else type(agent).__name__
    return name


def _elapsed_ms(start: float) -> float:
    return (perf_counter() - start) * 1000.0


def neutral_env_context(ts: float, frame_id: int) -> EnvContext:
    """环境评估不可用时的中性环境值。

    取值必须与 :meth:`common.agent_base.EnvAgent._neutral` **完全一致** ——
    否则同一份「无信息」输入会因为走了哪条降级路径而得到不同的权重分配
    （环境智能体自身失败 vs 智能体崩到连方法都调不动）。
    两条路径的一致性由 ``tests/app/test_integration_session.py`` 直接断言。
    """
    return EnvContext(
        brightness=0.5,
        blur=0.5,
        env_score=float(get("weights", "e0", default=DEFAULT_E0)),
        occlusion=0.0,
        ts=ts,
        frame_id=frame_id,
    )


@dataclass(frozen=True)
class AgentOutcome:
    """一路智能体本帧的执行情况。

    Attributes:
        agent_id: 智能体标识。
        ok: 是否产出了有效结果。``UNKNOWN`` 视为**降级**（不是有效判定）。
        ms: 本路耗时（毫秒）。感知级输入没有逐路计时，为 0.0。
        label: 感知路的标签值；**环境路为空字符串**。
        confidence: 感知路的置信度；**环境路复用为 ``env_score``（即 E）**。
        error: 降级原因；正常时为空字符串。
    """

    agent_id: str
    ok: bool
    ms: float = 0.0
    label: str = ""
    confidence: float = 0.0
    error: str = ""

    def line(self) -> str:
        """一行可读摘要（CLI 与联调记录直接贴）。"""
        if not self.ok:
            return f"{self.agent_id}=ERROR[{self.error}]"
        if not self.label:
            # 环境路：label 为空，confidence 位放的是 E
            return f"{self.agent_id}=E{self.confidence:.2f}({self.ms:.2f}ms)"
        return f"{self.agent_id}={self.label}:{self.confidence:.2f}({self.ms:.2f}ms)"


@dataclass(frozen=True)
class FrameReport:
    """一帧的完整执行记录。

    这是集成层的**对外证据对象**：CLI 打印它，联调记录引用它，测试断言它。
    它只描述「发生了什么」，不含任何判定逻辑。

    Attributes:
        frame_id: 帧序号（来自采集侧，三路共用）。
        ts: 帧时间戳（秒，单调递增）。
        origin: 输入来源，``"frame"``（原始帧）或 ``"perception"``（感知流）。
        problems: 本帧收集到的问题描述。含三类：帧规格不符、某路抛异常、
            融合或平滑异常。**空元组表示本帧全程无异常**。
        agents: 三路（或感知流中实际存在的那几路）的执行记录。
        env: 本帧使用的环境上下文（可能是中性降级值）。
        fused: 融合层的瞬时输出。
        final: 平滑后的稳定状态；尚无任何可对外输出的内容时为 ``None``。
    """

    frame_id: int
    ts: float
    origin: str
    problems: tuple[str, ...]
    agents: tuple[AgentOutcome, ...]
    env: EnvContext
    fused: FusionOutput
    final: FinalState | None

    @property
    def degraded(self) -> bool:
        """本帧是否有任何环节降级。"""
        return bool(self.problems) or any(not outcome.ok for outcome in self.agents)

    def line(self) -> str:
        """一行可读摘要。"""
        head = f"#{self.frame_id:04d} ts={self.ts:6.2f} E={self.env.env_score:.2f}"
        routes = " ".join(outcome.line() for outcome in self.agents) or "-"
        tail = f"{self.fused.label.value}({self.fused.reason})"
        if self.final is None:
            final = "final=-"
        else:
            final = f"final={self.final.label.value}" + ("/stale" if self.final.stale else "")
        return f"{head} {routes} -> {tail} {final}"


class SerialSession:
    """一次「串行集成会话」：三路智能体 + 融合 + 平滑 + 前端信封。

    三类输入走两个入口：:meth:`step`（原始帧，三路真实执行）与
    :meth:`step_perception`（感知流，跳过三路）。两者最终汇到同一个内部
    收尾流程，因此融合、平滑、信封的行为**不可能**出现两套。

    Args:
        expression / behavior: ``PerceptionAgent``。``None`` 表示该路尚未交付。
            ⚠️ **本层不会替你造假智能体** —— 静默替代会让人误以为跑的是真模型。
            联调时请显式传 ``common.mock.fake_agent(...)``。
        env_agent: ``EnvAgent``；``None`` 时用已落地的
            :class:`agents.env.agent.RuleBasedEnvAgent`。
        engine / smoother: 融合与平滑；``None`` 时按默认配置构造。
        participant_id / role / hidden: 前端信封字段（见 ``app/envelope.py``）。
        hub: 可选的 :class:`app.hub.Hub`。传入时 :meth:`publish` 会把状态登记
            进在线表，于是 ``Hub.snapshot_for`` 立刻能取到 payload。
    """

    def __init__(
        self,
        *,
        expression: PerceptionAgent | None = None,
        behavior: PerceptionAgent | None = None,
        env_agent: EnvAgent | None = None,
        engine: FusionEngine | None = None,
        smoother: TemporalSmoother | None = None,
        participant_id: str = "s01",
        role: str = ROLE_STUDENT,
        hidden: bool = False,
        hub: Hub | None = None,
    ) -> None:
        self._expression = expression
        self._behavior = behavior
        if env_agent is None:
            # 延迟 import：agents 与 app 互为兄弟包，模块级 import 会把
            # 「只想要感知流路径」的调用方也拖进环境智能体。
            from agents.env.agent import RuleBasedEnvAgent

            env_agent = RuleBasedEnvAgent()
        self._env_agent: EnvAgent = env_agent
        self._engine = engine if engine is not None else FusionEngine()
        self._smoother = smoother if smoother is not None else TemporalSmoother()
        self._hub = hub

        self.participant_id = participant_id
        self.role = role
        self.hidden = hidden
        # 借用信封自身的校验器拦一遍非法 id / role。否则这类错误会拖到第一帧
        # publish 时才暴露，而那时会话往往已经跑完一半。
        ParticipantState(participant_id=participant_id, role=role)

        self._warmed = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def _perception_agents(self) -> list[PerceptionAgent]:
        """实际参与融合的感知智能体（未交付的路直接缺席）。"""
        return [agent for agent in (self._expression, self._behavior) if agent is not None]

    def warmup(self) -> None:
        """对三路调用一次 ``warmup()``（幂等）。

        某一路加载失败**不阻断**启动：它会在逐帧推理时降级为 ``UNKNOWN``，
        比「因为表情模型没加载上，连环境与融合都不跑了」更符合演示的诉求。
        """
        if self._warmed:
            return
        agents: list[Any] = [*self._perception_agents(), self._env_agent]
        if not self._perception_agents():
            _logger().warning(
                "表情 / 行为两路均未提供：本会话只跑环境 + 融合（结果仅用于链路验证）"
            )
        for agent in agents:
            try:
                agent.warmup()
            except Exception as exc:
                _logger().warning(
                    "智能体 %s warmup 失败（不阻断启动，该路将逐帧降级）：%s",
                    _agent_id(agent),
                    exc,
                )
        self._warmed = True

    def close(self) -> None:
        """释放三路资源。逐个吞掉异常：收尾阶段的失败不该掩盖主流程的结果。"""
        # 显式标注 ``list[Any]``：两路感知与环境路的共同基类是 ABC，而 ``close``
        # 定义在各自的协议类上（``PerceptionAgent`` / ``EnvAgent``），
        # 让 mypy 自己求交结果会把 ``close`` 丢掉。
        agents: list[Any] = [*self._perception_agents(), self._env_agent]
        for agent in agents:
            try:
                agent.close()
            except Exception as exc:
                _logger().warning("智能体 %s close 失败：%s", _agent_id(agent), exc)

    # ------------------------------------------------------------------
    # 单帧：两个入口
    # ------------------------------------------------------------------

    def step(self, frame: Frame, ts: float, frame_id: int) -> FrameReport:
        """跑一帧**原始帧**：三路智能体真实执行。

        :func:`common.input_spec.check_frame` 的结论只记进报告并告警，**不抛异常**：
        边界拦截的意义是把非法帧挡在三路之前（让三路各自降级），而不是让整条
        链路崩在一次坏帧上。CLI/联调据此看到的是「这一帧为什么不可信」。
        """
        self.warmup()

        problems = check_frame(frame)
        if problems:
            _logger().warning("第 %d 帧不符合输入规格，三路将各自降级：%s", frame_id, problems)

        results: list[PerceptionResult] = []
        outcomes: list[AgentOutcome] = []
        for agent in self._perception_agents():
            result, outcome = self._safe_infer(agent, frame, ts, frame_id)
            results.append(result)
            outcomes.append(outcome)

        env, env_outcome = self._safe_assess(frame, ts, frame_id)
        outcomes.append(env_outcome)

        return self._finish(
            results,
            env,
            origin="frame",
            problems=tuple(problems),
            outcomes=tuple(outcomes),
        )

    def step_perception(
        self,
        results: list[PerceptionResult],
        env: EnvContext,
    ) -> FrameReport:
        """跑一帧**已感知**结果：跳过三路智能体（mock / demo 流）。

        ``ts`` 与 ``frame_id`` 取自 ``env`` —— 契约要求同一帧的多路结果共用二者，
        而环境上下文是这条链路上唯一必然存在的载体。
        """
        outcomes = tuple(
            AgentOutcome(
                agent_id=result.agent_id,
                ok=result.label is not EmotionLabel.UNKNOWN,
                label=result.label.value,
                confidence=result.confidence,
                error="" if result.label is not EmotionLabel.UNKNOWN else "本路返回 UNKNOWN",
            )
            for result in results
        )
        return self._finish(
            list(results),
            env,
            origin="perception",
            problems=(),
            outcomes=outcomes,
        )

    # ------------------------------------------------------------------
    # 批量：把输入源跑完
    # ------------------------------------------------------------------

    def iter_reports(
        self,
        source: PerceptionSource | FrameSource,
        *,
        max_frames: int | None = None,
    ) -> Iterator[FrameReport]:
        """逐帧执行 ``source`` 并产出报告。

        帧源**读失败**只结束迭代并记日志，不向上抛 —— 输入源（文件、摄像头、
        网络流）中断是常态，主流程该做的是把已跑出的结果留在日志里。
        判断依据是 ``frames()`` 每次 ``yield`` 的元素类型：二元组是感知流，
        三元组是原始帧。

        Args:
            source: :class:`FrameSource` 或 :class:`PerceptionSource`。
            max_frames: 最多跑多少帧；``None`` 表示跑完为止。
        """
        self.warmup()
        log = _logger()
        log.info(
            "串行集成开始：participant=%s role=%s 感知路=%s",
            self.participant_id,
            self.role,
            ",".join(_agent_id(a) for a in self._perception_agents()) or "无",
        )

        count = 0
        frames = source.frames()
        try:
            while max_frames is None or count < max_frames:
                try:
                    item = next(frames)
                except StopIteration:
                    break
                except Exception as exc:
                    log.error("输入源读取失败，串行主流程提前结束（已跑 %d 帧）：%s", count, exc)
                    break

                # 判别依据是元组的**长度**（二元组=感知流，三元组=原始帧），
                # 这在运行时天然成立，但静态类型推不出来，故显式断言一次。
                if len(item) == 2:
                    perceptions, env = cast("tuple[list[PerceptionResult], EnvContext]", item)
                    report = self.step_perception(perceptions, env)
                else:
                    frame, ts, frame_id = cast("tuple[Frame, float, int]", item)
                    report = self.step(frame, ts, frame_id)
                yield report
                count += 1
        finally:
            try:
                source.close()
            except Exception as exc:
                log.warning("输入源 close 失败：%s", exc)
            log.info("串行集成结束：共 %d 帧", count)

    def run(
        self,
        source: PerceptionSource | FrameSource,
        *,
        max_frames: int | None = None,
    ) -> list[FrameReport]:
        """:meth:`iter_reports` 的「跑完再返回」版本（测试与批量分析用）。"""
        return list(self.iter_reports(source, max_frames=max_frames))

    # ------------------------------------------------------------------
    # 前端信封
    # ------------------------------------------------------------------

    def publish(self, report: FrameReport) -> ParticipantState:
        """把一帧结果封装成前端信封，并（在配置了 ``hub`` 时）登记进在线表。

        教师**不带状态**（设计稿 A1：教师不占格）—— 教师端消费的是
        ``app.present.summary`` 的汇总，而不是自己的感知结果。
        """
        state = report.final if self.role == ROLE_STUDENT else None
        participant = ParticipantState(
            participant_id=self.participant_id,
            role=self.role,
            state=state,
            hidden=self.hidden,
            ts=report.ts,
        )
        if self._hub is not None:
            self._hub.register(participant)
        return participant

    # ------------------------------------------------------------------
    # 内部：三路各自的降级包装
    # ------------------------------------------------------------------

    def _safe_infer(
        self,
        agent: PerceptionAgent,
        frame: Frame,
        ts: float,
        frame_id: int,
    ) -> tuple[PerceptionResult, AgentOutcome]:
        """执行一路推理；异常一律降级为 ``UNKNOWN`` 并留证。"""
        agent_id = _agent_id(agent)
        start = perf_counter()
        try:
            result = agent.infer(frame, ts, frame_id)
        except Exception as exc:
            _logger().warning("智能体 %s 推理异常，本帧降级为 UNKNOWN：%s", agent_id, exc)
            return (
                PerceptionResult(
                    label=EmotionLabel.UNKNOWN,
                    prob=0.0,
                    confidence=0.0,
                    agent_id=agent_id,
                    ts=ts,
                    frame_id=frame_id,
                ),
                AgentOutcome(
                    agent_id=agent_id,
                    ok=False,
                    ms=_elapsed_ms(start),
                    error=f"{type(exc).__name__}: {exc}",
                ),
            )

        elapsed = _elapsed_ms(start)
        ok = result.label is not EmotionLabel.UNKNOWN
        return result, AgentOutcome(
            agent_id=result.agent_id or agent_id,
            ok=ok,
            ms=elapsed,
            label=result.label.value,
            confidence=result.confidence,
            error="" if ok else "本路返回 UNKNOWN（降级）",
        )

    def _safe_assess(
        self,
        frame: Frame,
        ts: float,
        frame_id: int,
    ) -> tuple[EnvContext, AgentOutcome]:
        """执行环境评估；异常一律降级为中性环境值并留证。

        优先复用智能体**自己继承来的** ``_neutral()``，这样「环境未知」的取值
        只有一处定义（``common/agent_base.py``）；只有在智能体不是 ``EnvAgent``
        体系（连方法都没有）时才退回 :func:`neutral_env_context` 的同值实现。
        """
        agent_id = _agent_id(self._env_agent)
        start = perf_counter()
        try:
            context = self._env_agent.assess(frame, ts, frame_id)
        except Exception as exc:
            _logger().warning("环境智能体异常，本帧改用中性环境值：%s", exc)
            fallback = getattr(self._env_agent, "_neutral", None)
            context = (
                fallback(ts, frame_id) if callable(fallback) else neutral_env_context(ts, frame_id)
            )
            return context, AgentOutcome(
                agent_id=agent_id,
                ok=False,
                ms=_elapsed_ms(start),
                confidence=context.env_score,
                error=f"{type(exc).__name__}: {exc}",
            )

        return context, AgentOutcome(
            agent_id=agent_id,
            ok=True,
            ms=_elapsed_ms(start),
            confidence=context.env_score,
        )

    # ------------------------------------------------------------------
    # 内部：收尾（融合 → 平滑 → 报告）
    # ------------------------------------------------------------------

    def _finish(
        self,
        results: list[PerceptionResult],
        env: EnvContext,
        *,
        origin: str,
        problems: tuple[str, ...],
        outcomes: tuple[AgentOutcome, ...],
    ) -> FrameReport:
        """两个入口的共同收尾：融合、平滑、组装报告。"""
        issues = list(problems)

        try:
            fused = self._engine.fuse(results, env)
        except Exception as exc:
            # 融合层契约上不该抛异常；抛了也不能让主流程崩。这里**不新造**
            # reason 取值（会与 docs/algorithm.md 的枚举脱节），而是复用
            # "empty" 表示「本帧未形成有效判定」，真正的异常记进 issues。
            _logger().error("融合异常，本帧降级为 UNKNOWN：%s", exc)
            issues.append(f"融合异常：{type(exc).__name__}: {exc}")
            fused = FusionOutput(
                label=EmotionLabel.UNKNOWN,
                confidence=0.0,
                weights={},
                reason=REASON_EMPTY,
                ts=env.ts,
                frame_id=env.frame_id,
            )

        final: FinalState | None = None
        try:
            final = self._smoother.update(
                fused.label,
                confidence=fused.confidence,
                weights=fused.weights,
                ts=fused.ts,
                frame_id=fused.frame_id,
            )
        except Exception as exc:
            _logger().error("平滑层异常，本帧不产出稳定状态：%s", exc)
            issues.append(f"平滑异常：{type(exc).__name__}: {exc}")

        report = FrameReport(
            frame_id=env.frame_id,
            ts=env.ts,
            origin=origin,
            problems=tuple(issues),
            agents=outcomes,
            env=env,
            fused=fused,
            final=final,
        )
        if report.degraded:
            _logger().warning("%s", report.line())
        else:
            _logger().debug("%s", report.line())
        return report
