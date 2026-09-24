"""表 B —— 感知输入规格（``frame`` 契约）。

这张表解决什么问题
------------------
``PerceptionAgent.infer(frame, ts, frame_id)`` / ``EnvAgent.assess(frame, ts, frame_id)``
的 ``frame`` 原先**没有定义**（旧 docstring 写的是「实现方自行决定如何解析」）。
后果是三路各自假设一种格式：表情侧按 BGR 处理、行为侧按 RGB、环境侧先转灰度 ——
集成时才发现对「一帧」的理解不同，而这类 bug 不会在单测里暴露，**只在联调时爆**。

本模块把这些假设写成**可被机器校验的常量与函数**，并规定唯一的一句话语义：

    ``frame`` = 采集侧直出的**原始彩色帧**：BGR 通道序、``uint8``、取值 ``[0, 255]``、
    形状 ``(H, W, 3)``、HWC 排布、分辨率不缩放不裁剪。

三条配套约定
------------
1. **``pipeline/`` 不做任何语义处理**：不裁 ROI、不缩放、不转色彩空间、不做归一化。
   它只负责「采集 → 打时间戳/帧号 → 调度 → 丢过期帧」。理由是三路需要的预处理**互不相同**：
   表情要人脸 ROI、行为要全帧骨骼点、环境要降采样全图 —— 任何一处统一预处理都必然
   让另外两路做二次转换。**谁需要 ROI 谁自己裁**（表情侧内部第一步）。
2. **序列不进 ``frame``**：行为智能体需要 ``behavior_agent.sequence_len``（默认 16）帧的
   骨骼点序列，但它收到的仍是**单帧**；序列由它自己维护一个滚动缓冲。
   这样三路签名保持一致，``pipeline/`` 也不需要知道谁的窗口有多长。
   冷启动（缓冲不足 ``sequence_len`` 帧）时**该路输出 ``UNKNOWN``**（整路弃权），
   由融合层按 §3.5 通道缺失降级处理 —— 不要用重复帧填充凑数，那会伪造一段不存在的时序。
3. **``None`` 是无帧哨兵**：``check_frame(None)`` 会报出问题，但协议允许它出现
   （单测与 mock 路径都用 ``None``），此时智能体必须降级为 ``UNKNOWN`` / 中性
   ``EnvContext``，**不得抛异常**（见 :mod:`common.agent_base` 的约定 2）。

``frame_id`` / ``ts`` 由**采集侧统一赋值**，同一帧的三路结果必须回填相同的值 ——
这是算法文档 §3.1 的 ``S(ℓ) = Σ wᵢ(E)·Pᵢ(ℓ)`` 能成立的前提（同帧对齐假设）。

改动流程
--------
本模块是**跨模块契约**：任何一个常量变化都同时影响 A（行为）、B（表情/流水线）、
C（环境）。**增加或删除常量 = 接口变更，需三方确认**；只改注释/文档无需评审。

⚠️ 与本文件「无关」的东西：模型权重格式（``.pt`` / ``.onnx``）与归一化均值方差属于
**各智能体内部实现** —— 因为 ``frame`` 不进入模型，模型吃的是各智能体自己转换后的张量。
把它们写在这里会让本文件变成三份实现的混合体，反而更难维护。
"""

from __future__ import annotations

from typing import Any

from common.config import get

__all__ = [
    "FRAME_CHANNELS",
    "FRAME_COLOR_SPACE",
    "FRAME_DTYPE",
    "FRAME_SHAPE_ORDER",
    "FRAME_VALUE_RANGE",
    "MIN_FRAME_SIDE",
    "check_frame",
    "require_frame",
    "roi_size",
    "sequence_len",
    "sequence_span_seconds",
    "target_fps",
]

#: 通道序。取 BGR 而不是 RGB：采集侧是 ``cv2.VideoCapture``（OpenCV 原生 BGR），
#: 选 RGB 会让**每一帧**都多一次 ``cvtColor``，而只有表情/环境两路需要 RGB。
FRAME_COLOR_SPACE = "BGR"

#: 元素类型名（用字符串而不是 ``numpy.uint8`` —— 本模块**零依赖**，
#: 不能在 CI 里 import numpy）。
FRAME_DTYPE = "uint8"

#: 取值域（闭区间）。
FRAME_VALUE_RANGE = (0, 255)

#: 轴顺序：``(H, W, C)``。
FRAME_SHAPE_ORDER = "HWC"

#: 通道数。
FRAME_CHANNELS = 3

#: 短边下限。低于它三路都做不出有意义的判定（表情找不到 48px 以上的人脸、
#: 环境降采样后退化、骨骼点不稳），故在边界上直接拦掉，而不是让三路各写一份判断。
MIN_FRAME_SIDE = 64

_FRAME_ATTR_HINT = "期望 numpy.ndarray（本模块只做鸭子类型检查，不 import numpy）"


def check_frame(frame: Any) -> tuple[str, ...]:
    """检查一帧是否符合本规格。

    只做**鸭子类型**检查（读 ``.shape`` / ``.dtype``），因此本模块零依赖、
    且不需要 numpy 就能测。

    Args:
        frame: 待检查的帧；``None`` 是允许出现的无帧哨兵（会被报出来）。

    Returns:
        问题描述元组；**空元组表示合格**。返回多条而不是抛异常，便于
        ``pipeline/`` 收集诊断信息、也便于测试直接断言。
    """
    if frame is None:
        return ("frame is None（无帧哨兵；只允许出现在单测/mock 路径，智能体须降级）",)

    shape = getattr(frame, "shape", None)
    if shape is None:
        return (f"缺少 .shape：{_FRAME_ATTR_HINT}，实际类型 {type(frame).__name__}",)

    problems: list[str] = []
    if len(shape) != 3:
        problems.append(
            f"维度应为 3（{FRAME_SHAPE_ORDER}），实际 {len(shape)}：shape={tuple(shape)}"
        )
    else:
        height, width, channels = (int(value) for value in shape)
        if channels != FRAME_CHANNELS:
            problems.append(f"通道数应为 {FRAME_CHANNELS}（{FRAME_COLOR_SPACE}），实际 {channels}")
        if height < MIN_FRAME_SIDE or width < MIN_FRAME_SIDE:
            problems.append(f"短边应 ≥ {MIN_FRAME_SIDE}px，实际 {height}×{width}")

    dtype_name = getattr(getattr(frame, "dtype", None), "name", None)
    if dtype_name is None:
        problems.append(f"缺少 .dtype：应为 {FRAME_DTYPE}")
    elif dtype_name != FRAME_DTYPE:
        problems.append(f"dtype 应为 {FRAME_DTYPE}，实际 {dtype_name}")

    return tuple(problems)


def require_frame(frame: Any) -> None:
    """与 :func:`check_frame` 同规则，但不合格时抛异常。

    ⚠️ **只在两个地方调用**：``pipeline/`` 的出口边界，以及测试。
    **不要**在智能体内部调用 —— 协议要求 ``infer()`` / ``assess()`` 永不抛异常，
    边界拦截的意义就是把非法帧挡在三路之前，而不是让它变成三路各自的崩溃。
    """
    problems = check_frame(frame)
    if problems:
        raise ValueError("frame 不符合输入规格： " + "；".join(problems))


def target_fps() -> float:
    """目标处理帧率（``configs/thresholds.yaml: pipeline.target_fps``）。"""
    return float(get("pipeline", "target_fps", default=15.0))


def sequence_len() -> int:
    """行为智能体的序列长度（``behavior_agent.sequence_len``，默认 16 帧）。"""
    return int(get("behavior_agent", "sequence_len", default=16))


def roi_size() -> int:
    """表情智能体的 ROI 归一化边长（``expression_agent.roi_size``，默认 224）。"""
    return int(get("expression_agent", "roi_size", default=224))


def sequence_span_seconds() -> float:
    """序列长度对应的**时间跨度**（秒）。

    默认取值下为 ``16 / 15 ≈ 1.07`` 秒 —— 这是行为智能体「看得见多久的历史」，
    直接决定它能捕捉到多快/多慢的动作，调 ``sequence_len`` 或 ``target_fps``
    时必须一起看这个数。``target_fps <= 0``（配置被写坏）时返回 ``0.0``，
    而不是让调用方拿到 ``inf``。
    """
    fps = target_fps()
    if fps <= 0.0:
        return 0.0
    return sequence_len() / fps
