"""串行链路压测：融合单点预算、端到端单帧预算、长跑稳定性。

这是当前能构造出的**最完整链路**：环境智能体（真实）→ 表情 / 行为（假智能体）
→ 融合 → 平滑。表情与行为两路尚未交付，故用 ``common/mock`` 顶替；等真实模型
接入后，本文件的**预算与判据不用改**，只需要把 ``workloads.serial_chain_step``
里的假智能体换成真实现 —— 这就是把载荷单独放一层的好处。

预算与 `pipeline.target_fps` 的关系
-----------------------------------
``pipeline.target_fps``（默认 15）给出的是**采集侧**的帧率要求，因此端到端单帧
耗时必须低于 ``1000 / target_fps`` 毫秒，否则处理追不上采集、队列必然积压。
这条断言直接用配置里的帧率算预算，而不是写死一个毫秒数：

* 调高目标帧率会让预算自动收紧 —— 这是应该的（指标变了）；
* 真实模型接入后若追不上，会在这里先红，而不是等到演示时才发现卡顿。
"""

from __future__ import annotations

import pytest

from common.config import get
from common.perception_types import EmotionLabel
from tests.stress.stresskit import (
    assert_median_within,
    live_growth_bytes,
    measure,
    repeat,
    tail_growth_ratio,
)
from tests.stress.workloads import (
    bench_env_agent,
    bench_fusion,
    bench_serial_chain,
    fusion_step,
    serial_chain_step,
)

#: 融合单点的耗时预算（毫秒）。融合层是无状态的纯计算，只有几个字典操作。
FUSION_BUDGET_MS = 5.0

#: 端到端单帧的兜底预算（毫秒）。不等于 ``1000/target_fps``，后者另有一条断言。
CHAIN_BUDGET_MS = 20.0

#: 长跑「尾/首窗口中位」之比的上限。
TAIL_GROWTH_LIMIT = 3.0

#: 长跑后仍存活内存净增长的上限（KB）。链路每帧产生 5~6 个对象，泄漏会线性累积。
#: 200 帧的阈值取 256 KB 相当于「单帧泄漏 1.3 KB 就会被抓」。
MEMORY_GROWTH_LIMIT_KB = 256


def _frame_budget_ms() -> float:
    """采集侧每帧的可用时间（毫秒）——由配置的 ``target_fps`` 推出。"""
    target_fps = float(get("pipeline", "target_fps", default=15))
    assert target_fps > 0, "pipeline.target_fps 必须为正"
    return 1000.0 / target_fps


# ---------------------------------------------------------------------------
# 单帧预算
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["consensus", "conflict", "low_light", "occluded"])
def test_fusion_single_frame_stays_within_budget(scenario: str) -> None:
    """四个协商级别各测一遍 —— 不能只测最快的那条路径。"""
    timing = bench_fusion(scenario=scenario, iterations=1000)
    assert_median_within(timing, budget_ms=FUSION_BUDGET_MS, label=f"融合 {scenario}")


def test_serial_chain_stays_within_budget() -> None:
    timing = bench_serial_chain(iterations=500)
    assert_median_within(timing, budget_ms=CHAIN_BUDGET_MS, label="串行链路单帧")


def test_serial_chain_keeps_up_with_the_capture_rate() -> None:
    """端到端单帧耗时必须低于采集侧的帧间隔，否则队列必然积压。

    预算从配置推导（``pipeline.target_fps``），所以调高目标帧率会自动收紧它。
    """
    budget_ms = _frame_budget_ms()
    timing = bench_serial_chain(iterations=500)
    assert_median_within(timing, budget_ms=budget_ms, label="串行链路 vs 采集帧率")


def test_chain_cost_is_dominated_by_the_environment_route() -> None:
    """结构断言：链路耗时应当**不低于**环境侧单点的耗时。

    两路假智能体是恒输出的、不读帧，因此链路的额外成本只有融合与平滑。
    若链路比环境侧还快，说明某一环被短路了（例如某个 assert 提前返回），
    那样后面的所有长跑结论都会失去意义 —— 所以要显式钉住这条下界。
    """
    env_timing = bench_env_agent(iterations=300)
    chain_timing = bench_serial_chain(iterations=300)
    assert chain_timing.median_ms >= env_timing.median_ms * 0.5, (
        f"链路 ({chain_timing.median_ms:.4f} ms) 明显快于环境单点 "
        f"({env_timing.median_ms:.4f} ms)，怀疑链路被短路"
    )


# ---------------------------------------------------------------------------
# 长跑稳定性
# ---------------------------------------------------------------------------


def test_chain_does_not_get_slower_over_time() -> None:
    """1200 帧里单帧成本应当恒定：融合层无状态、平滑层窗口定长。"""
    timing = measure(serial_chain_step(), iterations=1200, warmup=10)
    ratio = tail_growth_ratio(timing)
    assert ratio < TAIL_GROWTH_LIMIT, f"单帧成本随调用次数增长：尾/首比 {ratio:.2f}"


def test_chain_does_not_accumulate_memory() -> None:
    """200 帧后仍存活的内存增长必须远小于「每帧留一份」的量级。

    平滑层的窗口是定长 ``deque``、稳定状态只存最近一次，因此这里应当接近 0。

    ⚠️ **帧数刻意压得很低**：``tracemalloc`` 给每次分配加约 15 倍固定开销
    （实测 1000 帧要 29 秒），而这里只需要**粗筛**「是否按帧线性累积」。
    """
    growth = live_growth_bytes(repeat(serial_chain_step(), 200))
    assert growth < MEMORY_GROWTH_LIMIT_KB * 1024, f"存活内存增长 {growth / 1024:.1f} KB"


def test_fusion_engine_is_reusable_across_frames() -> None:
    """同一融合实例连跑 1000 帧后结果不变 —— 无状态性由行为而非注释保证。"""
    step = fusion_step(scenario="consensus")
    first = step()
    last = first
    for _ in range(1000):
        last = step()
    assert last.label is first.label
    assert last.reason == first.reason
    assert last.confidence == pytest.approx(first.confidence)


def test_chain_actually_exercises_the_full_path() -> None:
    """压测载荷必须真的走到「确认」这一步，否则长跑测的是空转。

    两路假智能体恒输出 FOCUSED、环境健康 → 三帧后应确认 FOCUSED。
    这条同时是压测载荷的**自检**：若链路某处被短路，它会先红。
    """
    step = serial_chain_step()
    state = None
    for _ in range(3):
        state = step()
    assert state is not None, "链路三帧后仍未产出任何状态"
    assert state.label is EmotionLabel.FOCUSED
    assert state.stale is False
