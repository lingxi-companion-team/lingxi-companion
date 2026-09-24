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

为什么合成帧是「按需计算」的
----------------------------
真帧（numpy HWC 数组）的构造成本与分辨率成正比。若把构造计入测量，测到的是
「造数据有多慢」而不是「算法有多慢」；而且 CI 环境里根本没有 numpy。
`SyntheticFrame` 构造 O(1)，像素在 ``frame[y][x]`` 时才算 —— 这与
:func:`agents.env.agent.sample_luma` 的真实访问模式一致（只读 32×32 个块中心
像素），所以能拿它压 1080p 而不付出 600 万次算术的代价。
"""

from __future__ import annotations

import gc
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

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

#: 可用的合成纹理。语义见 :class:`SyntheticFrame`。
TEXTURES = ("noise", "flat", "smooth")


class _DType:
    """``.dtype.name`` 的最小替身（``common.input_spec`` 只读这个属性）。"""

    name = "uint8"


class _SyntheticRow:
    """``frame[y]`` 的返回值：把 ``row[x]`` 转成按需计算的像素。"""

    __slots__ = ("_frame", "_y")

    def __init__(self, frame: SyntheticFrame, y: int) -> None:
        self._frame = frame
        self._y = y

    def __getitem__(self, x: int) -> tuple[int, int, int]:
        return self._frame.pixel(self._y, x)


class SyntheticFrame:
    """按需计算像素的 BGR 帧替身，可代表任意分辨率。

    三种纹理对应三种极端画面，覆盖环境智能体的三条主要分支：

    ==========  ==========================================================
    ``noise``   高频充足（失焦的反面）→ ``blur`` 接近 0、``E`` 高
    ``flat``    整帧同色 → 没有任何细节 → ``blur`` 到顶、``E`` 归零
    ``smooth``  低频渐变（线性斜坡）→ 拉普拉斯恒为 0，同样是「无细节」
    ==========  ==========================================================

    ``flat`` 与 ``smooth`` 都表达「没有可用的高频细节」，但前者还会被
    ``occlusion`` 的平坦块判据扫到（取决于亮度是否极端），后者不会 ——
    两者并存是为了让压测也能覆盖遮挡分支的不同走向。

    Args:
        height: 帧高（像素）。
        width: 帧宽（像素）。
        texture: 见上表。
        level: 基准亮度（0–255）。
        seed: 噪声种子。同一 seed 产出同一画面。
    """

    def __init__(
        self,
        *,
        height: int = 480,
        width: int = 640,
        texture: str = "noise",
        level: int = 128,
        seed: int = 0,
    ) -> None:
        if texture not in TEXTURES:
            raise ValueError(f"unknown texture {texture!r}; available: {', '.join(TEXTURES)}")
        if height < 1 or width < 1:
            raise ValueError("height / width must be positive")

        self.height = height
        self.width = width
        self.texture = texture
        self.level = level
        self.seed = seed

        self.shape = (height, width, 3)
        self.dtype = _DType()

    def __getitem__(self, y: int) -> _SyntheticRow:
        return _SyntheticRow(self, y)

    def pixel(self, y: int, x: int) -> tuple[int, int, int]:
        """返回 ``(y, x)`` 处的 BGR 像素（灰度，三分量相同）。"""
        if self.texture == "flat":
            value = self.level
        elif self.texture == "smooth":
            # 线性斜坡：二阶导为 0 ⇒ 拉普拉斯方差 0 ⇒ blur 到顶。
            span = max(1, self.height + self.width)
            value = self.level + ((y + x) * 16) // span
        else:
            # 确定性伪随机（整数散列，不用 random 以保持零状态）。
            mixed = (y * 73856093) ^ (x * 19349663) ^ (self.seed * 83492791)
            value = (mixed >> 8) & 0xFF
        value = 0 if value < 0 else (255 if value > 255 else value)
        return (value, value, value)


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
