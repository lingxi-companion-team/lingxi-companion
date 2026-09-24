"""压测骨架：合成帧、计时统计与预算断言（零第三方依赖）。

为什么要一个「骨架」而不是几个写死的基准测试
--------------------------------------------
压测与普通单测的目标不同：

* 单测问「对不对」——输入确定、断言精确；
* 压测问「**快不快、稳不稳**」——依赖机器，断言只能给**预算**，而预算必须
  留足余量，否则 CI 会在不同机器上随机变红。

因此本模块提供三样东西，让「加一条压测」变成一行：构造输入（`SyntheticFrame`）、
测量（`measure`）、判定（`assert_median_within`）。预算取「实测中位数的 10~50 倍」，
目标是抓住**数量级退化**（例如有人把按采样点读取改成遍历全帧像素），
而不是 certify 某个绝对性能数字 —— 真实指标必须上目标机复测。

合成帧从哪来
------------
:class:`~app.integration.sources.SyntheticFrame` 的**定义**放在
``app/integration/sources.py``，不在这里 —— 它有两个消费者：无摄像头时的全链路
演示（``python -m app.integration --source synthetic``）与本目录的压测载荷。
两处各写一份必然分叉（演示看到的画面与压测压的画面会悄悄变成两个东西），
故本模块只 import 后**原样导出**，``from tests.stress.stresskit import SyntheticFrame``
这类既有写法继续可用。

它按需计算像素的设计要点见那个类的文档串：真帧的构造成本与分辨率成正比，
把构造计入测量就变成「测造数据多慢」；而 CI 里根本没有 numpy。
"""

from __future__ import annotations

import gc
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.integration.sources import TEXTURES, SyntheticFrame

__all__ = [
    "TEXTURES",
    "SyntheticFrame",
    "Timing",
    "assert_median_within",
    "live_growth_bytes",
    "measure",
    "repeat",
    "tail_growth_ratio",
]


@dataclass(frozen=True)
class Timing:
    """一次测量的统计结果（单位毫秒）。"""

    iterations: int
    samples_ms: tuple[float, ...]
    median_ms: float
    mean_ms: float
    best_ms: float
    worst_ms: float
    p95_ms: float

    def line(self, label: str) -> str:
        """一行可读摘要，便于在断言失败信息与报告里直接贴。"""
        return (
            f"{label}: median {self.median_ms:.3f} ms / p95 {self.p95_ms:.3f} ms / "
            f"best {self.best_ms:.3f} ms（{self.iterations} 次）"
        )


def measure(call: Callable[[], Any], *, iterations: int, warmup: int = 3) -> Timing:
    """测量 ``call`` 的单次耗时，返回统计量。

    ``warmup`` 次不计入统计：首次调用往往包含解释器预热、缓存填充等一次性成本，
    把它们算进去会让中位数被少数几次慢样本抬高（这也是「压测要丢弃预热」的常规理由）。

    ⚠️ 本函数**故意不做 gc.disable()**：CI 上的对象分配行为与真实运行一致，
    关掉 GC 只会让数字好看、失去参考价值。
    """
    if iterations < 1:
        raise ValueError("iterations must be positive")

    for _ in range(warmup):
        call()

    samples: list[float] = []
    for _ in range(iterations):
        start = perf_counter()
        call()
        samples.append((perf_counter() - start) * 1000.0)

    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    return Timing(
        iterations=iterations,
        samples_ms=tuple(samples),
        median_ms=ordered[len(ordered) // 2],
        mean_ms=sum(ordered) / len(ordered),
        best_ms=ordered[0],
        worst_ms=ordered[-1],
        p95_ms=ordered[index],
    )


def assert_median_within(timing: Timing, *, budget_ms: float, label: str) -> None:
    """中位数必须落在预算内。

    用中位数而非平均值：平均值会被偶发的调度抖动拉高，从而在繁忙的 CI 上随机失败；
    中位数对这类噪声不敏感，同时仍能察觉数量级退化。
    """
    assert timing.median_ms <= budget_ms, (
        f"{label} 超出预算：{timing.line(label)}，预算 {budget_ms} ms"
    )


def tail_growth_ratio(timing: Timing, *, window: int = 100) -> float:
    """「后 ``window`` 次中位数 ÷ 前 ``window`` 次中位数」。

    这是**无状态性**的判据：单次成本若随调用次数增长（例如缓存越攒越大、
    窗口忘了截断），比值会明显大于 1。比值接近 1 说明每帧成本恒定。
    """
    if timing.iterations < 2 * window:
        raise ValueError(f"iterations ({timing.iterations}) must be >= 2 * window ({2 * window})")

    head = sorted(timing.samples_ms[:window])[window // 2]
    tail = sorted(timing.samples_ms[-window:])[window // 2]
    if head <= 0.0:
        return float("inf") if tail > 0.0 else 1.0
    return tail / head


def repeat(call: Callable[[], Any], times: int) -> Callable[[], Any]:
    """把 ``call`` 包装成「连续调用 ``times`` 次」的零参调用。

    给长跑与内存类断言用：``measure`` 统计的是**单次**耗时，而泄漏与状态增长
    只有在重复足够多次后才会显形。
    """
    if times < 1:
        raise ValueError("times must be positive")

    def loop() -> None:
        for _ in range(times):
            call()

    return loop


def live_growth_bytes(call: Callable[[], Any]) -> int:
    """执行 ``call``，返回**结束后仍存活**的新增可追踪字节数。

    只累加 ``size_diff > 0`` 的条目，因此返回值是「净增长」而不是「分配总量」——
    有分配也有释放的正常代码会得到接近 0 的结果，而真正的泄漏（每次调用都留下
    一份不再释放的对象）会随调用次数线性增长。

    ⚠️ 这是泄漏的**粗筛**，不是精确内存剖析：``tracemalloc`` 只看到 Python 层
    对象，也看不到被分配器缓存的空闲块。压测只要求「跑 N 次后仍存活的数据量
    远小于 N × 单帧数据量」这一量级判断。
    """
    tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.take_snapshot()
        call()
        gc.collect()
        after = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()

    return sum(
        stat.size_diff for stat in after.compare_to(before, "filename") if stat.size_diff > 0
    )
