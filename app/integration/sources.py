"""输入源：把「数据从哪来」与「主流程怎么跑」分开。

两类输入
--------
1. **感知级**（:class:`PerceptionSource`）—— 直接产出 ``(多路感知结果, 环境上下文)``。
   ``common/mock`` 的七个场景属于这一类。它**跳过三路智能体**，用来在模型尚未交付时
   先把「融合 → 平滑 → 前端」这段跑通 —— 这正是 ``configs/thresholds.yaml`` 里
   ``app.demo_mode`` 的含义。
2. **采集级**（:class:`FrameSource`）—— 产出原始 BGR 帧，三路智能体真实执行。
   摄像头适配器（``cv2.VideoCapture``）归桌面客户端 ``app/client/``：它需要 opencv，
   而 CI 不装第三方库，所以本模块只定义协议 + **零依赖**的实现。

为什么合成帧放在这里（而不是测试目录）
--------------------------------------
:class:`SyntheticFrame` 有两个消费者：**演示**（无摄像头时给全链路供帧）与
**压测**（``tests/stress`` 的载荷）。两处各写一份必然会慢慢分叉 —— 压测报告的
数字与演示看到的画面就不再是同一个东西了，故只留一份，由测试侧 import 复用。

⚠️ 它是**纯 Python 生成的合成画面**，不是真实图像：只用于演示与压测，
**不能**拿去评估模型精度。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any, Protocol

from common.mock import generator
from common.perception_types import EnvContext, PerceptionResult

__all__ = [
    "TEXTURES",
    "Frame",
    "FrameSource",
    "ListFrameSource",
    "MockScenarioSource",
    "PerceptionSource",
    "SyntheticFrame",
    "SyntheticFrameSource",
]

#: 一帧原始图像。规格（BGR / ``uint8`` / ``(H, W, 3)``）见 ``common.input_spec``。
#: 这里刻意用 ``Any`` —— 本模块不 import numpy（CI 没有它），一律走鸭子类型。
Frame = Any


# ----------------------------------------------------------------------
# 协议：两种输入的共同形状
# ----------------------------------------------------------------------


class PerceptionSource(Protocol):
    """感知级输入源：产出 ``(感知结果列表, 环境上下文)``。"""

    def frames(self) -> Iterator[tuple[list[PerceptionResult], EnvContext]]:
        """产出帧序列；迭代终止即数据结束。"""
        ...  # pragma: no cover —— Protocol 的方法体只是占位，运行期永不执行

    def close(self) -> None:
        """释放资源。允许空实现。"""
        ...  # pragma: no cover —— 同上，Protocol 的占位体


class FrameSource(Protocol):
    """采集级输入源：产出 ``(原始帧, ts, frame_id)``。"""

    def frames(self) -> Iterator[tuple[Frame, float, int]]:
        """产出帧序列；迭代终止即数据结束。"""
        ...  # pragma: no cover —— Protocol 的方法体只是占位，运行期永不执行

    def close(self) -> None:
        """释放资源。允许空实现。"""
        ...  # pragma: no cover —— 同上，Protocol 的占位体


# ----------------------------------------------------------------------
# 合成帧：按需计算像素
# ----------------------------------------------------------------------

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

    构造是 O(1)：真帧（numpy HWC 数组）的构造成本与分辨率成正比，若把构造
    计入测量，测到的是「造数据有多慢」而不是「算法有多慢」；而且 CI 环境里
    根本没有 numpy。像素在 ``frame[y][x]`` 时才算 —— 这与
    :func:`agents.env.agent.sample_luma` 的真实访问模式一致（只读 32×32 个块
    中心像素），所以能拿它压 1080p 而不付出 600 万次算术的代价。

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


# ----------------------------------------------------------------------
# 三个零依赖输入源
# ----------------------------------------------------------------------


class SyntheticFrameSource:
    """合成帧序列（无摄像头时的全链路演示源）。

    ``ts = index / fps``、``frame_id = index``（从 0 起，契约要求非负），
    因此时间戳严格单调、且与实际帧率一致 —— 平滑层与延迟统计都依赖这两点。

    Args:
        frames: 产出帧数。``0`` 表示空序列。
        height / width: 帧尺寸。短边须 ≥ ``input_spec.MIN_FRAME_SIDE``（64），
            否则三路智能体会因规格检查不过而集体降级。
        texture: 见 :class:`SyntheticFrame`。
        fps: 合成帧率，仅用于生成时间戳。
        level: 基准亮度。
        seed: 噪声种子。
    """

    def __init__(
        self,
        *,
        frames: int = 30,
        height: int = 480,
        width: int = 640,
        texture: str = "noise",
        fps: float = 10.0,
        level: int = 128,
        seed: int = 0,
    ) -> None:
        if frames < 0:
            raise ValueError("frames must not be negative")
        if fps <= 0:
            raise ValueError("fps must be positive")
        if height < 1 or width < 1:
            raise ValueError("height / width must be positive")
        # 提前构造一次，让非法的 texture 在这里就报错，而不是等到第一帧
        self._probe = SyntheticFrame(
            height=height, width=width, texture=texture, level=level, seed=seed
        )
        self._count = frames
        self._fps = fps

    def frames(self) -> Iterator[tuple[Frame, float, int]]:
        for index in range(self._count):
            frame = SyntheticFrame(
                height=self._probe.height,
                width=self._probe.width,
                texture=self._probe.texture,
                level=self._probe.level,
                seed=self._probe.seed,
            )
            yield frame, index / self._fps, index

    def close(self) -> None:
        """空实现：合成帧不持有任何外部资源。"""


class MockScenarioSource:
    """回放 ``common/mock`` 某个场景的感知流。

    ``repeat`` 用来把 4~5 帧的场景拉长到演示需要的长度，便于观察平滑层的
    ``hold``（票数不足时维持上一稳定状态）行为。

    ⚠️ **重复时必须整体平移时间戳与帧序号**：mock 场景自带 ``ts``（0.1 起）与
    ``frame_id``（1 起），直接原样重放会让时间**倒退** —— 而「ts 单调递增」
    是整条链路（延迟统计、平滑层的 ``_last_ts``）默认的前提。平移的步长取场景
    自身的最小时间间隔，因此重复播放表现为一段连续的长视频，而不是画面回退。

    场景名非法时 :func:`common.mock.generator.get_scenario` 会抛出**带可用名称
    列表**的 ``KeyError``，主流程直接把它暴露给调用方，不做二次包装。
    """

    def __init__(self, scenario: str = "consensus", *, repeat: int = 1) -> None:
        if repeat < 1:
            raise ValueError("repeat must be positive")
        self._scenario = generator.get_scenario(scenario)
        self._repeat = repeat
        self._step = self._time_step()
        self._span = self._step * max(1, len(self._scenario.frames))

    @property
    def scenario_name(self) -> str:
        return self._scenario.name

    @property
    def note(self) -> str:
        """该场景的预期行为说明（直接来自 mock 定义，供联调记录引用）。"""
        return self._scenario.note

    @property
    def frame_count(self) -> int:
        return len(self._scenario.frames)

    def _time_step(self) -> float:
        """场景自身的时间间隔（取最小正间隔）；不足两帧时按 1 秒处理。"""
        stamps = sorted({env.ts for _, env in self._scenario.frames})
        if len(stamps) < 2:
            return 1.0
        return min(later - earlier for earlier, later in zip(stamps, stamps[1:], strict=False))

    def frames(self) -> Iterator[tuple[list[PerceptionResult], EnvContext]]:
        per_round = len(self._scenario.frames)
        for round_index in range(self._repeat):
            ts_offset = round_index * self._span
            id_offset = round_index * per_round
            for perceptions, env in self._scenario.frames:
                yield (
                    [
                        replace(item, ts=item.ts + ts_offset, frame_id=item.frame_id + id_offset)
                        for item in perceptions
                    ],
                    replace(env, ts=env.ts + ts_offset, frame_id=env.frame_id + id_offset),
                )

    def close(self) -> None:
        """空实现：场景数据是模块级常量。"""


class ListFrameSource:
    """把显式给出的一批帧当输入源。

    集成测试与联调记录需要「一帧一个确定结果」，而合成源只能提供纹理级别的
    控制；这里接受任意 ``(frame, ts, frame_id)`` 三元组，因此可以直接放进
    手写帧、``None`` 无帧哨兵或刻意违反规格的帧来验证降级路径。
    """

    def __init__(self, frames: Sequence[tuple[Frame, float, int]]) -> None:
        self._frames = list(frames)

    def frames(self) -> Iterator[tuple[Frame, float, int]]:
        yield from self._frames

    def close(self) -> None:
        """空实现：数据已在内存里。"""
