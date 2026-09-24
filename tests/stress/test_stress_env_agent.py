"""环境智能体压测：单帧预算、分辨率无关性、长跑稳定性。

预算的取法
----------
这里的预算取「实测中位数的 100~400 倍」（实测值见 ``docs/reports/`` 的压测报告）。
看起来松得离谱，但它针对的不是性能认证，而是**数量级退化**：

* 采样点只有 32×32 = 1024 个，耗时理应与帧分辨率**无关**；
  若有人改成遍历全帧像素（480p 是 30 万次），耗时会跳 2~3 个数量级 —— 预算照样拦得住；
* 长跑比值与内存增长是**结构性**判据（状态有没有被截断），与机器快慢无关。

反过来，把预算压到接近实测值只会让 CI 在繁忙时随机变红，而**随机变红的压测
等于没有压测**（大家会习惯性重跑）。

⚠️ 本文件的断言全部只用标准库与合成帧：CI 不装 numpy / opencv，
这里也就不能依赖真帧 —— 详见 ``stresskit`` 的说明。
"""

from __future__ import annotations

import pytest

from agents.env import RuleBasedEnvAgent
from common.input_spec import check_frame
from tests.stress.stresskit import (
    SyntheticFrame,
    assert_median_within,
    live_growth_bytes,
    measure,
    repeat,
    tail_growth_ratio,
)
from tests.stress.workloads import bench_env_agent, env_agent_step

#: 单帧评估的耗时预算（毫秒）。取值理由见模块 docstring。
ENV_BUDGET_MS = 20.0

#: 「耗时与分辨率无关」的容许倍数。理论上恒为 1，留 5 倍防调度抖动。
RESOLUTION_DRIFT_FACTOR = 5.0

#: 长跑「尾/首窗口中位」之比的上限：> 3 说明单次成本在随调用次数增长。
TAIL_GROWTH_LIMIT = 3.0

#: 长跑后仍存活内存的净增长上限（KB）。无泄漏时应当接近 0；
#: 单帧产出的数据约 9 KB，200 帧的阈值取 128 KB 相当于「单帧泄漏 0.64 KB 就会被抓」。
MEMORY_GROWTH_LIMIT_KB = 128

#: 分辨率梯度：从 VGA 到 1080p。
RESOLUTIONS = ((240, 320), (480, 640), (720, 1280), (1080, 1920))


# ---------------------------------------------------------------------------
# 合成帧本身（它是所有压测的基础，坏了会让后续结论全部失效）
# ---------------------------------------------------------------------------


def test_synthetic_frame_satisfies_the_input_spec() -> None:
    """合成帧必须过 ``check_frame`` —— 否则压测压的是一条智能体不会走的分支。"""
    frame = SyntheticFrame(height=480, width=640)
    assert check_frame(frame) == ()


def test_synthetic_frame_is_deterministic() -> None:
    """同 seed 同画面：压测的可复现性依赖这一点。"""
    first = SyntheticFrame(height=32, width=32, seed=7)
    second = SyntheticFrame(height=32, width=32, seed=7)
    assert [first[0][x] for x in range(32)] == [second[0][x] for x in range(32)]
    assert first[0][0] != SyntheticFrame(height=32, width=32, seed=8)[0][0]


@pytest.mark.parametrize("texture", ["noise", "flat", "smooth"])
def test_synthetic_frame_textures_differ(texture: str) -> None:
    """三种纹理必须真的不同 —— 否则「覆盖三条分支」只是说法。"""
    frame = SyntheticFrame(height=64, width=64, texture=texture)
    row = [frame[0][x][0] for x in range(64)]
    if texture == "flat":
        assert len(set(row)) == 1
    else:
        assert len(set(row)) > 1


def test_synthetic_frame_rejects_unknown_texture() -> None:
    with pytest.raises(ValueError):
        SyntheticFrame(texture="snow")


@pytest.mark.parametrize("size", [(0, 10), (10, 0)])
def test_synthetic_frame_rejects_degenerate_sizes(size: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        SyntheticFrame(height=size[0], width=size[1])


# ---------------------------------------------------------------------------
# 单帧预算
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("height", "width"), RESOLUTIONS)
def test_single_frame_stays_within_budget(height: int, width: int) -> None:
    timing = bench_env_agent(height=height, width=width, iterations=200)
    assert_median_within(timing, budget_ms=ENV_BUDGET_MS, label=f"环境评估 {width}×{height}")


@pytest.mark.parametrize("texture", ["flat", "smooth"])
def test_low_detail_paths_stay_within_budget(texture: str) -> None:
    """无细节画面会走到遮挡判定的平坦块分支，耗时不能因此变差。"""
    timing = bench_env_agent(texture=texture, iterations=200)
    assert_median_within(timing, budget_ms=ENV_BUDGET_MS, label=f"环境评估（{texture}）")


def test_cost_is_independent_of_resolution() -> None:
    """32×32 采样意味着耗时只与采样点数有关，与帧像素数无关。

    这是本模块最重要的一条结构断言：它把「按采样点读取」这个设计决策
    变成可执行的约束。若哪天有人在 ``sample_luma`` 里加了一次全帧遍历，
    这条会立刻失败，而单帧预算可能还兜得住。
    """
    small = measure(env_agent_step(height=240, width=320), iterations=300)
    large = measure(env_agent_step(height=1080, width=1920), iterations=300)
    ratio = large.median_ms / max(small.median_ms, 1e-9)
    assert ratio < RESOLUTION_DRIFT_FACTOR, (
        f"耗时随分辨率增长：240p {small.median_ms:.4f} ms → 1080p "
        f"{large.median_ms:.4f} ms（{ratio:.1f}×）；采样层可能引入了全帧遍历"
    )


# ---------------------------------------------------------------------------
# 长跑稳定性
# ---------------------------------------------------------------------------


def test_assess_does_not_get_slower_over_time() -> None:
    """1200 帧里单次成本应当恒定 —— 环境智能体是无状态的，不应有任何累积。

    迭代次数取 1200 而不是「越多越好」：CI 上每个 job 都要在无差异的预算里跑完
    整套测试，长跑只要**足够长到让增长显形**即可（尾/首窗口各 100 帧，
    线性增长到 2 倍就会被判不通过）。
    """
    timing = measure(env_agent_step(), iterations=1200, warmup=10)
    ratio = tail_growth_ratio(timing)
    assert ratio < TAIL_GROWTH_LIMIT, f"单帧成本随调用次数增长：尾/首比 {ratio:.2f}"


def test_assess_does_not_accumulate_memory() -> None:
    """200 帧后仍存活的新增对象应当远小于「每帧留一份」的量级。

    每帧都会产出一个 ``EnvContext`` 与一个 32×32 网格（约 9 KB）；若它们被某处
    累积，200 帧就是约 1.8 MB，而正常实现应当在几十 KB 量级。

    ⚠️ **帧数刻意压得很低**：``tracemalloc`` 会给每次分配加约 15 倍的固定开销
    （实测 1000 帧要 29 秒，占掉整个压测套件一半的时长）。这里的目标是**粗筛**，
    200 帧足以让「按帧线性累积」显形 —— 别为了「更严谨」把它调大。
    """
    growth = live_growth_bytes(repeat(env_agent_step(), 200))
    assert growth < MEMORY_GROWTH_LIMIT_KB * 1024, f"存活内存增长 {growth / 1024:.1f} KB"


def test_agent_instance_is_reusable_across_frames() -> None:
    """同一实例连跑 300 帧后仍能给出正确结果（压测常抓到的「第二次调用就崩」）。"""
    agent = RuleBasedEnvAgent()
    agent.warmup()
    step = env_agent_step(agent=agent)
    last = None
    for _ in range(300):
        last = step()
    assert last is not None
    assert 0.0 <= last.env_score <= 1.0
    assert last.env_score > 0.9, "噪声帧清晰且亮度正常，E 应当很高"
